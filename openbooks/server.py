"""OpenBooks HTTP API — a thin, dependency-free wrapper over the query layer.

Run::

    python -m openbooks.server                       # DuckDB next to repo root
    python -m openbooks.server --db /path/warehouse.duckdb --port 9000
    python -m openbooks.server --postgres postgresql://app@db/openbooks

Environment variables (flags take precedence):

    OPENBOOKS_DB        path to warehouse.duckdb
    OPENBOOKS_PG_DSN    postgresql:// DSN (overrides OPENBOOKS_DB)
    OPENBOOKS_HOST      bind address           (default 127.0.0.1)
    OPENBOOKS_PORT      port                   (default 8765)
    OPENBOOKS_CORS      Access-Control-Allow-Origin value (default: unset —
                        no CORS header; set to a specific origin, or '*'
                        for local development only)
    OPENBOOKS_STATIC    directory containing index.html (default: repo root)

This server is stdlib-only by design — appropriate for localhost use and
small internal deployments. If you embed OpenBooks in a larger app,
prefer mounting :class:`openbooks.OpenBooks` behind your existing
framework (FastAPI/Flask/Django) instead of fronting this server.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

# ── .env loading ──────────────────────────────────────────────────────
# Load ~/.hermes/.env (where Hermes stores secrets like ANTHROPIC_API_KEY)
# and a local .env in the project root, if either exists.  This lets the
# Ask (NL query) layer pick up provider credentials without manual exports.
for _env_path in (
    os.path.expanduser("~/.hermes/.env"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
):
    if os.path.isfile(_env_path):
        with open(_env_path) as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

from openbooks.queries import OpenBooks

log = logging.getLogger("openbooks.server")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MAX_BODY_BYTES = 64 * 1024  # verdict payloads are tiny; reject anything big


def _first(params: dict, key: str, default: str | None = None) -> str | None:
    """First query-string value for ``key``, or default."""
    values = params.get(key)
    return values[0] if values else default


def _int_param(params: dict, key: str, default: int, lo: int = 1, hi: int = 500) -> int:
    """Parse an integer query param defensively, clamped to [lo, hi]."""
    raw = _first(params, key)
    if raw is None or raw == "":
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError as exc:
        raise BadRequest(f"parameter {key!r} must be an integer, got {raw!r}") from exc


class BadRequest(ValueError):
    """Client error → HTTP 400."""


def _load_budget_json() -> dict:
    """Load SB1847 structured budget JSON for the /api/budget endpoint."""
    import json
    import os
    paths = [
        os.path.join(os.getcwd(), "budget_pdfs", "fy2027_sb1847_structured.json"),
        os.path.join(os.path.dirname(__file__), "..", "budget_pdfs", "fy2027_sb1847_structured.json"),
    ]
    for p in paths:
        if os.path.isfile(p):
            with open(p) as f:
                return json.load(f)
    return {"error": "budget JSON not found", "agencies": []}


# Manual aliases for budget→checkbook agency name matching
_BUDGET_AGENCY_ALIASES: dict[str, str] = {
    "ARIZONA HEALTH CARE COST CONTAINMENT SYSTEM": "AHCCCS",
    "SUPERINTENDENT OF PUBLIC INSTRUCTION": "DEPT OF EDUCATION",
    "STATE DEPARTMENT OF CORRECTIONS": "DEPT OF CORRECTIONS",
    "STATE DEPT OF CORRECTIONS": "DEPT OF CORRECTIONS",
}


def _match_budget_to_checkbook(budget_agency: str, spend_agencies_norm: dict[str, str],
                               norm_fn) -> str | None:
    """Match a budget agency name to a normalized checkbook agency name."""
    import re
    norm = norm_fn(budget_agency)

    # 1) Direct match
    if norm in spend_agencies_norm:
        return norm

    # 2) Alias table
    alias = _BUDGET_AGENCY_ALIASES.get(norm) or _BUDGET_AGENCY_ALIASES.get(budget_agency.upper())
    if alias and norm_fn(alias) in spend_agencies_norm:
        return norm_fn(alias)

    # 3) Strip STATE/ARIZONA prefix and try again
    stripped = re.sub(r'^(STATE |ARIZONA )', '', norm)
    if stripped in spend_agencies_norm:
        return stripped
    for sn in spend_agencies_norm:
        if stripped == sn or sn in stripped or stripped in sn:
            return sn

    # 4) Keyword overlap (≥2 significant words)
    stop = {'DEPT', 'OF', 'THE', 'BOARD', 'COMMISSION', 'OFFICE', 'DIVISION',
            'BUREAU', 'DEPARTMENT', 'STATE', 'ARIZONA', 'AND', 'FOR'}
    words = set(re.findall(r'[A-Z]+', stripped)) - stop
    best = None
    best_score = 0
    for sn in spend_agencies_norm:
        s_words = set(re.findall(r'[A-Z]+', sn)) - stop
        if not words or not s_words:
            continue
        overlap = words & s_words
        score = len(overlap)
        if score > best_score:
            best_score = score
            best = sn
    if best and best_score >= 2:
        return best
    return None


def _triangulation_data(ob) -> dict:
    """Join budget authorization + checkbook actual spend + AG audit per agency."""
    import json as _json
    import os

    budget = _load_budget_json()
    if "error" in budget:
        return budget

    # Build spend lookup from agency_summary (fast, pre-aggregated)
    spend_rows = ob._query(
        "SELECT agency, fiscal_year, total_amount, txn_count "
        "FROM agency_summary WHERE transaction_type = 'EX' "
        "AND fiscal_year IN (2025, 2024)"
    )
    spend_map: dict[str, dict[int, dict]] = {}
    for row in spend_rows:
        norm = ob._norm_agency(row["agency"])
        if norm not in spend_map:
            spend_map[norm] = {}
        spend_map[norm][int(row["fiscal_year"])] = {
            "total": float(row["total_amount"] or 0),
            "n_txn": int(row["txn_count"] or 0),
        }
    spend_agencies_norm = {k: k for k in spend_map}

    results = []
    for ba in budget.get("agencies", []):
        agency_name = ba.get("agency", "")
        authorized = ba.get("total_appropriation", 0) or 0
        fte = ba.get("fte_positions", 0) or 0

        # Match to checkbook
        matched_norm = _match_budget_to_checkbook(
            agency_name, spend_agencies_norm, ob._norm_agency
        )
        spend_25 = 0
        spend_24 = 0
        n_txn_25 = 0
        if matched_norm and matched_norm in spend_map:
            s25 = spend_map[matched_norm].get(2025, {})
            s24 = spend_map[matched_norm].get(2024, {})
            spend_25 = s25.get("total", 0)
            spend_24 = s24.get("total", 0)
            n_txn_25 = s25.get("n_txn", 0)

        # AG audit
        ag = ob._ag_audit(matched_norm or agency_name) if matched_norm or agency_name else None
        n_ag_reports = 0
        questioned_cost = 0
        n_adverse = 0
        ag_first_fy = None
        ag_last_fy = None
        if ag:
            n_ag_reports = ag.get("n_reports", 0) or 0
            questioned_cost = ag.get("total_questioned_cost", 0) or 0
            n_adverse = ag.get("n_adverse_findings", 0) or 0
            ag_first_fy = ag.get("first_fy")
            ag_last_fy = ag.get("last_fy")

        variance = spend_25 - authorized if spend_25 and authorized else None
        variance_pct = (
            (variance / authorized * 100)
            if variance is not None and authorized
            else None
        )

        # Risk score: combines audit severity + variance magnitude
        risk_score = 0
        if n_adverse > 0:
            risk_score += min(n_adverse * 10, 40)
        if questioned_cost > 0:
            risk_score += min(int(questioned_cost / 1e6), 30)
        if variance_pct is not None and variance_pct > 20:
            risk_score += min(int(variance_pct), 30)

        results.append({
            "agency": agency_name,
            "matched": matched_norm is not None,
            "authorized": authorized,
            "actual_spend_fy25": spend_25,
            "actual_spend_fy24": spend_24,
            "variance": variance,
            "variance_pct": variance_pct,
            "fte": fte,
            "n_line_items": len(ba.get("line_items") or []),
            "n_fund_sources": len(ba.get("fund_sources") or []),
            "n_txn_fy25": n_txn_25,
            "n_ag_reports": n_ag_reports,
            "questioned_cost": questioned_cost,
            "n_adverse": n_adverse,
            "ag_first_fy": ag_first_fy,
            "ag_last_fy": ag_last_fy,
            "risk_score": risk_score,
            "fund_sources": ba.get("fund_sources", []),
            "line_items": ba.get("line_items", []),
        })

    # Sort by authorized descending
    results.sort(key=lambda x: x["authorized"], reverse=True)

    # Summary stats
    total_authorized = sum(r["authorized"] for r in results)
    total_authorized_matched = sum(r["authorized"] for r in results if r["matched"])
    total_spend = sum(r["actual_spend_fy25"] for r in results if r["matched"])
    total_questioned = sum(r["questioned_cost"] for r in results)
    total_adverse = sum(r["n_adverse"] for r in results)
    n_matched = sum(1 for r in results if r["matched"])

    return {
        "agencies": results,
        "summary": {
            "n_budget_agencies": len(results),
            "n_matched": n_matched,
            "total_authorized": total_authorized,
            "total_authorized_matched": total_authorized_matched,
            "total_actual_spend_fy25": total_spend,
            "total_questioned_cost": total_questioned,
            "total_adverse_findings": total_adverse,
            # Statewide variance uses matched-only authorized so the
            # comparison is apples-to-apples (spend is only available
            # for matched agencies).  total_authorized (all agencies)
            # is still surfaced separately for the budget headline.
            "overall_variance": total_spend - total_authorized_matched if total_spend else None,
            "overall_variance_pct": (
                (total_spend - total_authorized_matched) / total_authorized_matched * 100
                if total_spend and total_authorized_matched
                else None
            ),
        },
    }


def make_handler(ob: OpenBooks, *, cors: str | None, static_dir: str):
    """Build the request handler class around a shared OpenBooks instance.

    A single lock serializes access to the underlying connection —
    neither DuckDB connections nor psycopg connections are safely
    shareable across threads without one. Queries are millisecond-fast,
    so serialization is not a practical bottleneck at this scale.
    """
    db_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "OpenBooks/1.0"

        # ── routing ────────────────────────────────────────────────────

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            params = parse_qs(parsed.query)

            if path in ("", "/index.html"):
                return self._static("index.html")

            def _leaderboard() -> Any:
                metric = _first(params, "metric", "total_questioned_cost") or "total_questioned_cost"
                try:
                    return ob.rank_ag_findings(metric, limit=_int_param(params, "limit", 15, hi=100))
                except ValueError as exc:
                    raise BadRequest(str(exc)) from exc

            routes: dict[str, Callable[[], Any]] = {
                "/api/entity": lambda: ob.entity(_first(params, "q", "") or ""),
                "/api/leads": lambda: ob.leads(
                    tier=_int_param(params, "tier", 1, lo=1, hi=5),
                    status=_first(params, "status") or None,
                    agency=_first(params, "agency") or None,
                    limit=_int_param(params, "limit", 50),
                ),
                "/api/agency": lambda: ob.agency_card(_first(params, "q", "") or ""),
                "/api/explain": lambda: ob.explain(_first(params, "q", "") or ""),
                "/api/search": lambda: ob.search(_first(params, "q", "") or "", limit=20),
                "/api/findings": lambda: ob.search_findings(
                    _first(params, "q", "") or "", limit=_int_param(params, "limit", 20, hi=100)
                ),
                "/api/ag-leaderboard": _leaderboard,
                "/api/waterfall": lambda: ob.waterfall(),
                "/api/pending": lambda: ob.verdicts_pending(
                    limit=_int_param(params, "limit", 50)
                ),
                "/api/health": lambda: {"status": "ok"},
                "/api/budget": _load_budget_json,
                "/api/triangulation": lambda: _triangulation_data(ob),
                "/api/unattributed": lambda: ob.unattributed_spend(
                    _first(params, "agency") or None,
                    fiscal_year=(
                        _int_param(params, "fy", 0, lo=1990, hi=2100)
                        if _first(params, "fy") else None
                    ),
                    limit=_int_param(params, "limit", 25, hi=200),
                ),
                "/api/finding-transactions": lambda: ob.finding_transactions(
                    _first(params, "id", "") or "",
                    limit=_int_param(params, "limit", 50, hi=200),
                    window_years=_int_param(params, "window", 1, lo=0, hi=3),
                ),
            }
            fn = routes.get(path)
            if fn is None:
                return self._err(404, "not found")
            self._dispatch(fn)

        def do_POST(self) -> None:
            path = self.path.rstrip("/")
            if path == "/api/ask":
                try:
                    body = self._read_json_body()
                except BadRequest as exc:
                    return self._err(400, str(exc))
                question = (body.get("question") or "").strip()
                if not question:
                    return self._err(400, "missing 'question'")
                # NL answering makes a network call to the LLM provider and
                # may take a few seconds; it runs OUTSIDE db_lock for the
                # HTTP round-trip, but ask() re-acquires db_lock around each
                # tool dispatch so warehouse reads stay serialized.
                from openbooks.ask import AskError
                from openbooks.ask import ask as _ask

                try:
                    # ``db_lock`` is acquired per tool dispatch inside ask()
                    # (not around the slow LLM HTTP call), serializing DB
                    # access without blocking other requests during the model
                    # round-trip.
                    result = _ask(ob, question, lock=db_lock)
                except AskError as exc:
                    return self._err(502, str(exc))
                except Exception:
                    log.exception("error handling /api/ask")
                    return self._err(500, "internal error")
                return self._json(result)

            if path != "/api/verdict":
                return self._err(404, "not found")
            try:
                body = self._read_json_body()
            except BadRequest as exc:
                return self._err(400, str(exc))
            self._dispatch(
                lambda: ob.set_verdict(
                    body.get("entity_key", ""),
                    body.get("verdict", ""),
                    overtaker_interest=body.get("overtaker_interest", 0),
                    public_context=body.get("public_context", ""),
                    recommended_action=body.get("recommended_action", ""),
                )
            )

        # ── plumbing ───────────────────────────────────────────────────

        def _dispatch(self, fn: Callable[[], Any]) -> None:
            """Run a route under the DB lock with full error containment."""
            try:
                with db_lock:
                    result = fn()
            except BadRequest as exc:
                return self._err(400, str(exc))
            except Exception:
                log.exception("error handling %s", self.path)
                return self._err(500, "internal error")
            if result is None:
                return self._err(404, "no match")
            self._json(result)

        def _read_json_body(self) -> dict:
            try:
                length = int(self.headers.get("content-length", 0))
            except ValueError as exc:
                raise BadRequest("invalid content-length") from exc
            if length > MAX_BODY_BYTES:
                raise BadRequest("request body too large")
            if length <= 0:
                return {}
            try:
                parsed = json.loads(self.rfile.read(length))
            except json.JSONDecodeError as exc:
                raise BadRequest("request body is not valid JSON") from exc
            if not isinstance(parsed, dict):
                raise BadRequest("request body must be a JSON object")
            return parsed

        def _send(self, code: int, content_type: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if cors:
                self.send_header("Access-Control-Allow-Origin", cors)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: Any, code: int = 200) -> None:
            self._send(code, "application/json", json.dumps(data, default=str).encode())

        def _err(self, code: int, msg: str) -> None:
            self._json({"error": msg}, code=code)

        def _static(self, filename: str) -> None:
            # Only named files from static_dir are served — no path traversal
            # surface because `filename` never comes from the request.
            path = os.path.join(static_dir, filename)
            if not os.path.exists(path):
                return self._err(404, "not found")
            with open(path, "rb") as f:
                self._send(200, "text/html; charset=utf-8", f.read())

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            log.debug("%s — %s", self.address_string(), format % args)

    return Handler


def serve(
    ob: OpenBooks,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    cors: str | None = None,
    static_dir: str = _REPO_ROOT,
) -> None:
    """Block serving HTTP until interrupted."""
    handler = make_handler(ob, cors=cors, static_dir=static_dir)
    httpd = ThreadingHTTPServer((host, port), handler)
    log.info("OpenBooks API on http://%s:%d", host, port)
    print(f"\n  OpenBooks → http://{host}:{port}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        httpd.server_close()
        ob.close()


def main() -> None:  # pragma: no cover - CLI wrapper
    parser = argparse.ArgumentParser(description="OpenBooks HTTP API server")
    parser.add_argument("--db", default=os.environ.get("OPENBOOKS_DB"),
                        help="path to warehouse.duckdb (default: repo root)")
    parser.add_argument("--postgres", default=os.environ.get("OPENBOOKS_PG_DSN"),
                        help="postgresql:// DSN (overrides --db)")
    parser.add_argument("--host", default=os.environ.get("OPENBOOKS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("OPENBOOKS_PORT", "8765")))
    parser.add_argument("--cors", default=os.environ.get("OPENBOOKS_CORS"),
                        help="Access-Control-Allow-Origin value (omit to disable)")
    parser.add_argument("--static-dir",
                        default=os.environ.get("OPENBOOKS_STATIC", _REPO_ROOT))
    parser.add_argument("--writable", action="store_true",
                        help="open DuckDB read-write (enables /api/verdict)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    if args.postgres:
        try:
            ob = OpenBooks.from_postgres(args.postgres)
            # Probe the connection so an unreachable/misconfigured Postgres
            # falls back to DuckDB instead of crashing the server at boot.
            ob._query("SELECT 1")
            logging.getLogger("openbooks.server").info(
                "serving from Postgres: %s", args.postgres.rsplit("@", 1)[-1]
            )
        except Exception as exc:
            fallback = args.db or os.path.join(_REPO_ROOT, "warehouse.duckdb")
            logging.getLogger("openbooks.server").warning(
                "Postgres unavailable (%s); falling back to DuckDB at %s",
                exc, fallback,
            )
            ob = OpenBooks(fallback, writable=args.writable)
    else:
        db_path = args.db or os.path.join(_REPO_ROOT, "warehouse.duckdb")
        ob = OpenBooks(db_path, writable=args.writable)

    serve(ob, args.host, args.port, cors=args.cors, static_dir=args.static_dir)


if __name__ == "__main__":  # pragma: no cover
    main()
