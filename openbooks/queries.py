"""OpenBooks query layer — the "talking to the money" interface.

Engine-agnostic: every method emits ANSI-portable SQL through a
:class:`~openbooks.db.Backend` (DuckDB locally, Postgres on a server).

Usage::

    from openbooks import OpenBooks

    ob = OpenBooks("warehouse.duckdb")                  # read-only DuckDB
    ob = OpenBooks("warehouse.duckdb", writable=True)   # + verdict curation
    ob = OpenBooks.from_postgres("postgresql://app@db/openbooks")

    ob.entity("FONDOMONTE")        # one vendor: summary + txns + crosswalk
    ob.leads(tier=1, status="genuine_review")   # the action queue
    ob.agency_card("DEPT OF TRANSPORTATION")    # scorecard + FY trend
    ob.explain(transaction_id)     # why this tier
    ob.search("healthcare")        # fuzzy entity/agency/program search
    ob.waterfall()                 # tier distribution
    ob.verdicts_pending()          # reviewer queue
    ob.set_verdict(key, verdict)   # curation (writable connections only)

The database must be bootstrapped once before use (creates the
``vendor_verdicts`` table and ``tx_with_verdict`` view)::

    python -m openbooks.bootstrap --duckdb warehouse.duckdb
"""

from __future__ import annotations

import csv
import os
from typing import Any

from openbooks._sql import TX_WITH_KEY_CTE, normalize_entity_key
from openbooks.db import Backend, DuckDBBackend, PostgresBackend

#: Valid curation verdicts (see tier_config.yaml `verification.verdicts`).
VALID_VERDICTS = frozenset(
    {"genuine_review", "explained_benign", "false_positive_marker", "mixed"}
)

#: Human-readable labels for the ten marker families in `explain()`.
MARKER_FAMILY_LABELS = {
    "f_round": "round (negotiated amounts)",
    "f_dup": "duplicate (same vendor+amount, distinct docs)",
    "f_conc": "concentration (vendor share of agency)",
    "f_newvendor": "new vendor (first appearance by name)",
    "f_yearend": "year-end (closing entry timing)",
    "f_peer": "peer outlier (dwarfs agency x category norm)",
    "f_account": "accountability (blank payee / missing contract)",
    "f_rail": "manual rail (JV/INTERNAL/NULL payment)",
    "f_entity": "entity name (individual-looking / masked payee)",
}


class ReadOnlyError(RuntimeError):
    """Raised when a write operation is attempted on a read-only connection."""


class OpenBooks:
    """Query interface over an OpenBooks warehouse.

    Parameters
    ----------
    db_path:
        Path to a DuckDB warehouse file. Ignored when ``backend`` is given.
    mart_dir:
        Directory holding build artifacts (``entity_crosswalk.csv``).
        Defaults to ``<db dir>/mart`` to match the repo layout, but pass
        it explicitly when embedding in an app with a different layout.
    writable:
        Open the connection read-write (required only for ``set_verdict``).
        Read-only is the default so many processes can share the file.
    backend:
        Bring-your-own engine (e.g. :class:`PostgresBackend`). When set,
        ``db_path``/``writable`` are not used.
    """

    def __init__(
        self,
        db_path: str = "warehouse.duckdb",
        *,
        mart_dir: str | None = None,
        writable: bool = False,
        backend: Backend | None = None,
    ):
        if backend is not None:
            self.db: Backend = backend
            self.writable = True  # caller controls; assume capable
            base_dir = os.getcwd()
        else:
            db_path = os.path.abspath(db_path)
            self.db = DuckDBBackend(db_path, read_only=not writable)
            self.writable = writable
            base_dir = os.path.dirname(db_path)

        self.mart_dir = mart_dir if mart_dir is not None else os.path.join(base_dir, "mart")

    @classmethod
    def from_postgres(cls, dsn: str, *, mart_dir: str | None = None) -> OpenBooks:
        """Connect to a Postgres deployment of the warehouse tables."""
        return cls(backend=PostgresBackend(dsn), mart_dir=mart_dir)

    # ── internal helpers ───────────────────────────────────────────────

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        return self.db.query(sql, params)

    def _crosswalk_rows(self, parent_key: str) -> list[dict]:
        """Names merged into ``parent_key`` per the entity crosswalk CSV.

        Missing crosswalk file degrades gracefully to an empty list —
        the crosswalk is a build artifact, not a runtime requirement.
        """
        path = os.path.join(self.mart_dir, "entity_crosswalk.csv")
        if not os.path.exists(path):
            return []
        with open(path, newline="") as f:
            return [
                row
                for row in csv.DictReader(f)
                if row.get("parent_key", "") == parent_key
                and row.get("entity_key", "") != parent_key
            ]

    # ── public API ─────────────────────────────────────────────────────

    def entity(self, name_or_key: str) -> dict | None:
        """One vendor entity: summary + top-30 transactions + crosswalk.

        The primary entry point for "tell me about vendor X". Accepts an
        exact ``entity_key`` or a fuzzy fragment of the entity name.
        """
        q = normalize_entity_key(name_or_key)

        summary = self._query(
            """
            SELECT entity_key, entity_name, n_vendor_ids AS n_ids,
                   hv_txn, hv_exposure, n_flagged, flagged_exposure,
                   n_tier1, usd_tier1, n_tier2, top_tier, max_risk_score,
                   first_year, last_year, n_agencies,
                   agencies, top_markers,
                   verify_verdict, overtaker_interest, public_context
            FROM tier_entities
            WHERE entity_key = ?
               OR upper(entity_name) LIKE '%' || ? || '%'
            ORDER BY usd_tier1 DESC
            LIMIT 1
            """,
            (q, q),
        )
        if not summary:
            return None

        s = summary[0]
        ek = s["entity_key"]

        s["transactions"] = self._query(
            """
            SELECT fiscal_year AS fy, posting_date AS date, agency,
                   category1 AS cat, amount, risk_score AS score, tier,
                   array_to_string(fired_markers, ', ') AS markers
            FROM tx_with_verdict
            WHERE entity_key = ? AND tier IN (1, 2)
            ORDER BY risk_score DESC, amount DESC
            LIMIT 30
            """,
            (ek,),
        )
        s["n_txn_displayed"] = len(s["transactions"])

        crosswalk = self._crosswalk_rows(ek)
        s["names_merged"] = crosswalk
        s["n_names_merged"] = len(crosswalk)
        return s

    def leads(
        self,
        tier: int = 1,
        status: str | None = None,
        agency: str | None = None,
        min_amount: float = 0,
        limit: int = 50,
    ) -> list[dict]:
        """The action queue — filterable transactions at a given tier.

        ``status`` filters the verification overlay: ``genuine_review``,
        ``screened_unreviewed``, ``explained_benign``, etc.
        """
        where = ["t.tier = ?"]
        params: list[Any] = [tier]

        if status:
            where.append("coalesce(v.verdict, 'screened_unreviewed') = ?")
            params.append(status)
        if agency:
            where.append("t.agency = ?")
            params.append(agency)
        if min_amount:
            where.append("t.amount >= ?")
            params.append(min_amount)

        params.append(int(limit))
        sql = f"""
            WITH t AS ({TX_WITH_KEY_CTE})
            SELECT t.transaction_id, t.fiscal_year, t.posting_date, t.agency,
                   t.category1, t.payee, t.amount, t.risk_score, t.tier,
                   array_to_string(t.fired_markers, ', ') AS markers,
                   coalesce(v.verdict, 'screened_unreviewed') AS verify_status,
                   v.overtaker_interest, v.public_context
            FROM t
            LEFT JOIN vendor_verdicts v ON v.entity_key = t.entity_key
            WHERE {' AND '.join(where)}
            ORDER BY t.risk_score DESC, t.amount DESC
            LIMIT ?
        """
        return self._query(sql, tuple(params))

    def agency_card(self, agency: str) -> dict | None:
        """Agency scorecard + FY trend + top flagged vendors.

        The bondholder question: "How exposed and how well-governed is
        my obligor?"
        """
        scorecard = self._query(
            """
            SELECT agency, cabinet, hv_txn, hv_exposure,
                   n_tier1, usd_tier1, n_tier2, usd_tier2, n_tier3, n_flagged,
                   tier12_exposure, tier12_pct_of_hv, avg_risk_score, max_risk_score,
                   distinct_flagged_vendors, top_markers
            FROM tier_agency_summary
            WHERE agency = ?
            """,
            (agency,),
        )
        if not scorecard:
            return None

        result = scorecard[0]
        result["trend"] = self._query(
            """
            SELECT fiscal_year, hv_txn, hv_exposure,
                   n_tier1, usd_tier1, n_tier12, usd_tier12,
                   tier12_pct_of_hv, avg_risk_score
            FROM tier_agency_year
            WHERE agency = ?
            ORDER BY fiscal_year
            """,
            (agency,),
        )
        result["top_flagged_vendors"] = self._query(
            """
            SELECT entity_key, mode(payee) AS entity_name,
                   count(*) FILTER (WHERE tier = 1) AS n_tier1,
                   round(sum(amount) FILTER (WHERE tier = 1), 0) AS usd_tier1,
                   round(max(risk_score), 2) AS max_score
            FROM tx_with_verdict
            WHERE agency = ? AND tier IN (1, 2)
            GROUP BY entity_key
            ORDER BY usd_tier1 DESC
            LIMIT 5
            """,
            (agency,),
        )
        return result

    def explain(self, transaction_id: str) -> dict | None:
        """"Why this tier?" — marker-family breakdown + score calculation."""
        rows = self._query(
            """
            SELECT transaction_id, fiscal_year, agency, payee, category1,
                   amount, mag_class, risk_band, risk_score, tier,
                   f_round, f_dup, f_conc, f_newvendor, f_yearend,
                   f_peer, f_account, f_rail, f_entity,
                   marker_sum, category_multiplier,
                   fired_markers, n_markers
            FROM tx_tiered
            WHERE transaction_id = ?
            """,
            (transaction_id,),
        )
        if not rows:
            return None

        r = rows[0]
        r["marker_breakdown"] = {
            label: r[col]
            for col, label in MARKER_FAMILY_LABELS.items()
            if r.get(col, 0) and r[col] > 0
        }
        r["calculation"] = (
            f"marker_sum = {r['marker_sum']} × "
            f"category_multiplier({r['category1']}) = {r['category_multiplier']} → "
            f"risk_score = {r['risk_score']} → {r['risk_band']} → "
            f"mag_class = {r['mag_class']} → Tier {r['tier']}"
        )
        return r

    def search(self, query: str, limit: int = 20) -> dict:
        """Fuzzy search across entities, agencies, and programs."""
        q = f"%{query.upper()}%"

        entities = self._query(
            """
            SELECT entity_key, entity_name, n_tier1, usd_tier1, top_tier,
                   max_risk_score, verify_verdict, overtaker_interest
            FROM tier_entities
            WHERE upper(entity_name) LIKE ?
               OR (agencies IS NOT NULL AND upper(array_to_string(agencies, ' ')) LIKE ?)
               OR (top_markers IS NOT NULL AND upper(array_to_string(top_markers, ' ')) LIKE ?)
            ORDER BY usd_tier1 DESC
            LIMIT ?
            """,
            (q, q, q, limit),
        )
        agencies = self._query(
            """
            SELECT agency, cabinet, hv_exposure, usd_tier1, tier12_pct_of_hv
            FROM tier_agency_summary
            WHERE upper(agency) LIKE ?
            ORDER BY usd_tier1 DESC
            LIMIT ?
            """,
            (q, limit),
        )
        programs = self._query(
            """
            SELECT appropriation, lead_agency, tier12_exposure, n_tier1
            FROM tier_program_summary
            WHERE upper(appropriation) LIKE ?
            ORDER BY tier12_exposure DESC
            LIMIT ?
            """,
            (q, limit),
        )
        return {"query": query, "entities": entities, "agencies": agencies, "programs": programs}

    def waterfall(self) -> dict:
        """Tier distribution — the "does the model clear most dollars?" story."""
        rows = self._query(
            """
            SELECT tier,
                   count(*) AS n_txn,
                   round(sum(amount), 0) AS exposure,
                   round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct_txn,
                   round(100.0 * sum(amount) / sum(sum(amount)) OVER (), 2) AS pct_exposure,
                   round(avg(risk_score), 2) AS avg_score
            FROM tx_tiered
            GROUP BY tier
            ORDER BY tier
            """
        )
        total = self._query(
            "SELECT count(*) AS n, round(sum(amount), 0) AS exposure FROM tx_tiered"
        )[0]

        by_tier = {int(r["tier"]): r for r in rows}
        t1, t4, t5 = by_tier.get(1), by_tier.get(4), by_tier.get(5)
        if t1 and t4 and t5:
            summary = (
                f"{t1['n_txn']} Tier-1 txns (${t1['exposure'] / 1e9:.2f}B) → "
                f"{t4['n_txn']} Tier-4 clean (${t4['exposure'] / 1e9:.1f}B) → "
                f"{t5['n_txn']} unranked (${t5['exposure'] / 1e9:.1f}B). "
                f"{t1['pct_txn']}% of txns get the top review tier."
            )
        else:
            summary = "Tier distribution incomplete — see `tiers` rows."

        return {
            "total_txns": total["n"],
            "total_exposure": total["exposure"],
            "tiers": rows,
            "summary": summary if rows else "No tiered data available.",
        }

    def verdicts_pending(self, tier: int = 1, limit: int = 50) -> list[dict]:
        """Tier-1 entities awaiting human curation (the reviewer's queue)."""
        return self._query(
            """
            SELECT e.entity_key, e.entity_name,
                   e.n_tier1, e.usd_tier1, e.max_risk_score, e.top_markers,
                   e.first_year, e.last_year
            FROM tier_entities e
            LEFT JOIN vendor_verdicts v ON v.entity_key = e.entity_key
            WHERE e.n_tier1 > 0 AND v.verdict IS NULL
            ORDER BY e.usd_tier1 DESC
            LIMIT ?
            """,
            (limit,),
        )

    def set_verdict(
        self,
        entity_key: str,
        verdict: str,
        overtaker_interest: int = 0,
        public_context: str = "",
        recommended_action: str = "",
    ) -> dict:
        """Assign or update a verification verdict.

        Requires a writable connection (``writable=True`` or a Postgres
        backend). Returns the updated entity, or an ``error`` dict for
        an invalid verdict.
        """
        if verdict not in VALID_VERDICTS:
            return {
                "error": f"Invalid verdict. Must be one of: {', '.join(sorted(VALID_VERDICTS))}"
            }
        if not self.writable:
            raise ReadOnlyError(
                "set_verdict requires a writable connection; "
                "construct OpenBooks(..., writable=True)."
            )

        key = normalize_entity_key(entity_key)
        self.db.execute(
            """
            INSERT INTO vendor_verdicts
                (entity_key, verdict, overtaker_interest, public_context,
                 recommended_action, reviewed_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (entity_key) DO UPDATE SET
                verdict = excluded.verdict,
                overtaker_interest = excluded.overtaker_interest,
                public_context = excluded.public_context,
                recommended_action = excluded.recommended_action,
                reviewed_at = CURRENT_TIMESTAMP
            """,
            (key, verdict, overtaker_interest, public_context, recommended_action or None),
        )
        return self.entity(key) or {"status": "ok", "entity_key": key, "verdict": verdict}

    def close(self) -> None:
        """Close the underlying connection."""
        self.db.close()

    def __enter__(self) -> OpenBooks:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
