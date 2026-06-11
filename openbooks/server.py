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
                "/api/waterfall": lambda: ob.waterfall(),
                "/api/pending": lambda: ob.verdicts_pending(
                    limit=_int_param(params, "limit", 50)
                ),
                "/api/health": lambda: {"status": "ok"},
            }
            fn = routes.get(path)
            if fn is None:
                return self._err(404, "not found")
            self._dispatch(fn)

        def do_POST(self) -> None:
            if self.path.rstrip("/") != "/api/verdict":
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
        ob = OpenBooks.from_postgres(args.postgres)
    else:
        db_path = args.db or os.path.join(_REPO_ROOT, "warehouse.duckdb")
        ob = OpenBooks(db_path, writable=args.writable)

    serve(ob, args.host, args.port, cors=args.cors, static_dir=args.static_dir)


if __name__ == "__main__":  # pragma: no cover
    main()
