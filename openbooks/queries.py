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
    ob.rank_agencies("usd_tier1")  # agency leaderboard by a metric
    ob.rank_vendors("usd_tier1")   # vendor leaderboard (optional agency filter)
    ob.rank_programs()             # program/appropriation leaderboard
    ob.verdicts_pending()          # reviewer queue
    ob.set_verdict(key, verdict)   # curation (writable connections only)

The database must be bootstrapped once before use (creates the
``vendor_verdicts`` table and ``tx_with_verdict`` view)::

    python -m openbooks.bootstrap --duckdb warehouse.duckdb
"""

from __future__ import annotations

import csv
import json
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
        # Directory holding committed enrichment JSONs (alongside the DB by
        # default). Used for the Grok-adjudicated untraceable-spend context.
        self._enrich_dir = base_dir
        self._unattr_enrich: dict | None = None  # lazy cache
        self._entity_enrich: dict | None = None  # lazy cache (Grok entity ctx)

    @classmethod
    def from_postgres(cls, dsn: str, *, mart_dir: str | None = None) -> OpenBooks:
        """Connect to a Postgres deployment of the warehouse tables."""
        return cls(backend=PostgresBackend(dsn), mart_dir=mart_dir)

    # ── internal helpers ───────────────────────────────────────────────

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        return self.db.query(sql, params)

    def _table_exists(self, name: str) -> bool:
        """True if a base table/view ``name`` is present in the warehouse.

        Used to make the optional Auditor-General overlay degrade gracefully
        on databases built before the ``ag_*`` tables were loaded.
        """
        try:
            rows = self._query(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = ? LIMIT 1",
                (name,),
            )
            return bool(rows)
        except Exception:
            return False

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

        # Indirect AG context: the vendor's primary agency (most Tier-1+2
        # dollars) and that AGENCY's AG-audit rollup. This is agency-level
        # corroboration, NOT a finding about the vendor — the data contract
        # forbids accusing any entity. Labeled accordingly in the payload and
        # UI. None when no tx, no primary agency, or agency has no AG coverage.
        s["primary_agency_ag"] = None
        primary = self._query(
            """
            SELECT agency, round(sum(amount), 0) AS usd
            FROM tx_with_verdict
            WHERE entity_key = ? AND tier IN (1, 2) AND agency IS NOT NULL
            GROUP BY agency
            ORDER BY usd DESC
            LIMIT 1
            """,
            (ek,),
        )
        if primary:
            agency_name = primary[0]["agency"]
            summary = self._ag_audit_summary(agency_name)
            if summary:
                summary["agency"] = agency_name
                summary["relation"] = "primary_agency"
                s["primary_agency_ag"] = summary

        # Grok-verified real-world context (identity, arizona_role, verdict,
        # reason, confidence, real source citations). Decision-support overlay
        # built by scripts/enrich_entities.py via xAI's Agent Tools API; absent
        # for un-enriched entities and on pre-enrichment deployments.
        s["grok_context"] = self._entity_enrichment().get(ek)
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

    def _ag_audit_summary(self, agency: str) -> dict | None:
        """Lightweight AG-audit rollup for an agency — counts, questioned
        costs, and actual spending context. Used for search-result badges,
        vendor-card agency context, and as the core of :meth:`_ag_audit`.

        Returns ``None`` if the ag_* tables are absent or the agency has no
        AG coverage. Prefers the enriched ``ag_finding_context`` table when
        available (adds spending comparison + program areas); falls back to
        the raw ``ag_reports``/``ag_findings`` join otherwise.
        """
        if not (self._table_exists("ag_reports")
                and self._table_exists("ag_findings")):
            return None

        # Enriched path: use ag_finding_context + ag_agency_spending
        if self._table_exists("ag_agency_spending"):
            rows = self._query(
                """
                SELECT n_audit_reports, first_audit_fy, last_audit_fy,
                       n_findings, n_findings_with_cost, n_adverse_findings,
                       total_questioned_cost, actual_spend_during_audit_period,
                       most_recent_adverse_fy
                FROM ag_agency_spending WHERE agency = ?
                """,
                (agency,),
            )
            if not rows:
                return None
            r = rows[0]
            # Check for estimate flags in finding context
            has_estimate = False
            if self._table_exists("ag_finding_context"):
                est = self._query(
                    """SELECT bool_or(
                       lower(coalesce(questioned_cost_confidence, '')) IN ('medium', 'low')
                       OR lower(coalesce(questioned_cost_basis, '')) LIKE '%projection%'
                       OR lower(coalesce(questioned_cost_basis, '')) LIKE '%projected%'
                       OR lower(coalesce(questioned_cost_basis, '')) LIKE '%estimate%'
                       OR lower(coalesce(questioned_cost_basis, '')) LIKE '%contingent%'
                       ) AS has_estimate
                       FROM ag_finding_context WHERE agency = ?""",
                    (agency,),
                )
                has_estimate = bool(est[0]["has_estimate"]) if est else False
            return {
                "n_reports": r["n_audit_reports"],
                "first_fy": r["first_audit_fy"],
                "last_fy": r["last_audit_fy"],
                "total_questioned_cost": r["total_questioned_cost"],
                "n_findings_with_cost": r["n_findings_with_cost"],
                "n_adverse_findings": r["n_adverse_findings"],
                "most_recent_adverse_fy": r["most_recent_adverse_fy"],
                "actual_spend_during_audit_period": r["actual_spend_during_audit_period"],
                "questioned_cost_has_estimate": has_estimate,
            }

        # Fallback: raw join without spending context
        rows = self._query(
            """
            SELECT count(DISTINCT r.report_id) AS n_reports,
                   min(r.fiscal_year) AS first_fy,
                   max(r.fiscal_year) AS last_fy,
                   coalesce(sum(f.questioned_cost_usd), 0) AS total_qc,
                   count(f.questioned_cost_usd) AS n_findings_with_cost,
                   bool_or(
                       lower(coalesce(f.questioned_cost_confidence, '')) IN ('medium', 'low')
                       OR lower(coalesce(f.questioned_cost_basis, '')) LIKE '%projection%'
                       OR lower(coalesce(f.questioned_cost_basis, '')) LIKE '%projected%'
                       OR lower(coalesce(f.questioned_cost_basis, '')) LIKE '%estimate%'
                       OR lower(coalesce(f.questioned_cost_basis, '')) LIKE '%contingent%'
                   ) AS has_estimate
            FROM ag_reports r
            LEFT JOIN ag_findings f ON f.report_id = r.report_id
            WHERE r.agency_checkbook = ?
            """,
            (agency,),
        )
        if not rows or not rows[0]["n_reports"]:
            return None
        r = rows[0]
        return {
            "n_reports": r["n_reports"],
            "first_fy": r["first_fy"],
            "last_fy": r["last_fy"],
            "total_questioned_cost": r["total_qc"],
            "n_findings_with_cost": r["n_findings_with_cost"],
            "questioned_cost_has_estimate": bool(r["has_estimate"]),
        }

    def _ag_audit(self, agency: str) -> dict | None:
        """Arizona Auditor-General audit overlay for an agency (optional).

        Joins the ``ag_reports`` / ``ag_findings`` layer (extracted from AG
        performance-audit PDFs) by ``agency_checkbook`` to the checkbook
        agency name. Returns audit count, FY span, questioned-cost rollup,
        spending comparison, and a per-finding list with program context —
        the external-corroboration surface for the agency card.

        When the ``ag_finding_context`` table is available, each finding
        includes: actual agency expenditure for the audit FY, questioned
        cost as % of spend, matched program area, and matched fund —
        enabling the "auditors questioned $X, actual spending was $Y"
        comparison.

        Degrades to ``None`` when the ``ag_*`` tables are absent (DBs built
        before this layer was loaded), so the agency card never breaks.

        Note: AG findings are *audited findings*, distinct from the
        warehouse's tier "leads". Questioned costs are cash-basis figures and
        some are estimates/projections — ``questioned_cost_confidence``
        travels with each so the UI can label them, never present a
        projection as a confirmed loss.
        """
        if not (self._table_exists("ag_reports")
                and self._table_exists("ag_findings")):
            return None

        reports = self._query(
            """
            SELECT report_id, fiscal_year, report_type, title, report_date
            FROM ag_reports
            WHERE agency_checkbook = ?
            ORDER BY fiscal_year DESC, report_id DESC
            """,
            (agency,),
        )
        if not reports:
            return None

        # Enriched findings path: use ag_finding_context for spending comparison
        if self._table_exists("ag_finding_context"):
            findings = self._query(
                """
                SELECT finding_id, report_id,
                       substring(finding_text, 1, 600) AS finding_text_preview,
                       questioned_cost_usd, questioned_cost_confidence,
                       questioned_cost_basis, has_adverse_findings,
                       fiscal_year, program_area, fund_keyword,
                       actual_agency_expenditure, actual_fund_expenditure,
                       questioned_pct_of_agency_spend, questioned_pct_of_fund_spend
                FROM ag_finding_context
                WHERE agency = ?
                ORDER BY questioned_cost_usd DESC NULLS LAST,
                         fiscal_year DESC, finding_id
                """,
                (agency,),
            )
        else:
            findings = self._query(
                """
                SELECT f.finding_id, f.report_id,
                       substring(f.finding_text, 1, 600) AS finding_text_preview,
                       f.questioned_cost_usd, f.questioned_cost_confidence,
                       f.questioned_cost_basis, f.has_adverse_findings,
                       r.fiscal_year
                FROM ag_findings f
                JOIN ag_reports r ON r.report_id = f.report_id
                WHERE r.agency_checkbook = ?
                ORDER BY f.questioned_cost_usd DESC NULLS LAST,
                         f.report_id DESC, f.finding_no
                """,
                (agency,),
            )

        qc_rows = [f for f in findings if f.get("questioned_cost_usd")]
        total_qc = sum(f["questioned_cost_usd"] for f in qc_rows)
        # surface whether any QC figure is an estimate/projection/contingent
        # so the UI can disclaim the headline number rather than imply a
        # confirmed loss. Checked via both confidence AND basis wording.
        def _is_soft(f):
            conf = (f.get("questioned_cost_confidence") or "")
            basis = (f.get("questioned_cost_basis") or "").lower()
            return (conf in ("medium", "low")
                    or "projection" in basis or "projected" in basis
                    or "estimate" in basis or "contingent" in basis)
        has_estimate = any(_is_soft(f) for f in qc_rows)
        years = [r["fiscal_year"] for r in reports if r.get("fiscal_year")]

        # Spending context from ag_agency_spending (if available)
        actual_spend = None
        n_adverse = None
        most_recent_adverse = None
        if self._table_exists("ag_agency_spending"):
            sp = self._query(
                """SELECT actual_spend_during_audit_period,
                          n_adverse_findings, most_recent_adverse_fy
                   FROM ag_agency_spending WHERE agency = ?""",
                (agency,),
            )
            if sp:
                actual_spend = sp[0]["actual_spend_during_audit_period"]
                n_adverse = sp[0]["n_adverse_findings"]
                most_recent_adverse = sp[0]["most_recent_adverse_fy"]

        return {
            "n_reports": len(reports),
            "first_fy": min(years) if years else None,
            "last_fy": max(years) if years else None,
            "total_questioned_cost": total_qc,
            "n_findings_with_cost": len(qc_rows),
            "questioned_cost_has_estimate": has_estimate,
            "n_adverse_findings": n_adverse,
            "most_recent_adverse_fy": most_recent_adverse,
            "actual_spend_during_audit_period": actual_spend,
            "reports": reports,
            "findings_with_cost": qc_rows,
        }

    #: Allowed sort metrics for rank_ag_findings → aggregate aliases in the
    #: ranking query. Like the other _*_METRICS dicts, this whitelist is what
    #: makes the ORDER BY safe — the alias can never come from free text.
    _AG_FINDING_METRICS = {
        "total_questioned_cost": "total_questioned_cost",
        "n_findings": "n_findings",
        "n_findings_with_cost": "n_findings_with_cost",
        "n_adverse": "n_adverse",
        "n_reports": "n_reports",
    }

    def search_findings(self, text: str, limit: int = 20) -> dict | None:
        """Full-text search across Arizona Auditor-General audit findings.

        Searches ``finding_text``, ``recommendation_text``, and the report
        ``title`` — case-insensitive contains. Returns matching findings with
        their report context (agency, fiscal year, questioned cost, etc.).

        Returns ``None`` when the ``ag_*`` tables are absent (degrades
        gracefully like the other AG methods).
        """
        if not (self._table_exists("ag_reports")
                and self._table_exists("ag_findings")):
            return None
        limit = max(1, min(int(limit), 100))
        q = f"%{(text or '').upper()}%"
        rows = self._query(
            """
            SELECT f.finding_id, f.finding_no,
                   substring(f.finding_text, 1, 500) AS finding_text_preview,
                   f.questioned_cost_usd, f.questioned_cost_confidence,
                   f.questioned_cost_basis, f.has_adverse_findings,
                   r.report_id, r.fiscal_year, r.report_type,
                   r.agency_checkbook AS agency, r.title
            FROM ag_findings f
            JOIN ag_reports r ON r.report_id = f.report_id
            WHERE upper(coalesce(f.finding_text, '')) LIKE ?
               OR upper(coalesce(f.recommendation_text, '')) LIKE ?
               OR upper(coalesce(r.title, '')) LIKE ?
            ORDER BY f.questioned_cost_usd DESC NULLS LAST,
                     r.fiscal_year DESC, f.finding_no
            LIMIT ?
            """,
            (q, q, q, limit),
        )
        return {"query": text, "n": len(rows), "findings": rows}

    def rank_ag_findings(
        self, metric: str = "total_questioned_cost", limit: int = 10
    ) -> dict | None:
        """Leaderboard of agencies by Auditor-General audit metrics.

        Answers cross-agency questions like *"which agencies had the most
        questioned costs?"*, *"most audit findings?"*, *"most adverse
        findings?"*. Backed by the pre-joined ``ag_reports`` +
        ``ag_findings`` layer, grouped by ``agency_checkbook``.

        ``metric`` is whitelisted via :attr:`_AG_FINDING_METRICS`.
        Returns ``None`` when the ``ag_*`` tables are absent.
        """
        if not (self._table_exists("ag_reports")
                and self._table_exists("ag_findings")):
            return None
        col = self._AG_FINDING_METRICS.get(metric)
        if col is None:
            raise ValueError(
                f"unknown metric {metric!r}; choose from "
                f"{sorted(self._AG_FINDING_METRICS)}"
            )
        limit = max(1, min(int(limit), 100))
        rows = self._query(
            f"""
            SELECT r.agency_checkbook AS agency,
                   count(DISTINCT r.report_id) AS n_reports,
                   count(f.finding_id) AS n_findings,
                   coalesce(sum(f.questioned_cost_usd), 0) AS total_questioned_cost,
                   count(f.questioned_cost_usd) AS n_findings_with_cost,
                   count(*) FILTER (WHERE f.has_adverse_findings) AS n_adverse,
                   min(r.fiscal_year) AS first_fy,
                   max(r.fiscal_year) AS last_fy
            FROM ag_reports r
            LEFT JOIN ag_findings f ON f.report_id = r.report_id
            WHERE r.agency_checkbook IS NOT NULL
            GROUP BY r.agency_checkbook
            ORDER BY {col} DESC
            LIMIT ?
            """,
            (limit,),
        )
        return {"metric": metric, "ranked_by": col, "n": len(rows), "agencies": rows}

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
            SELECT entity_key,
                   mode() WITHIN GROUP (ORDER BY payee) AS entity_name,
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
        result["ag_audit"] = self._ag_audit(agency)
        return result

    def explain(self, transaction_id: str) -> dict | None:
        """"Why this tier?" — marker-family breakdown + score calculation.

        Reads from ``tx_with_verdict`` so the verification overlay
        (verify_status) travels with the explanation — a flag is never
        surfaced without its verdict (data contract §6.2).
        """
        rows = self._query(
            """
            SELECT transaction_id, fiscal_year, agency, payee, category1,
                   amount, mag_class, risk_band, risk_score, tier,
                   f_round, f_dup, f_conc, f_newvendor, f_yearend,
                   f_peer, f_account, f_rail, f_entity,
                   marker_sum, category_multiplier,
                   fired_markers, n_markers,
                   verify_status, overtaker_interest, public_context
            FROM tx_with_verdict
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
        # AG-corroboration badge: attach the lightweight audit rollup to each
        # agency hit (None when uncovered or the ag_* layer isn't loaded).
        for a in agencies:
            a["ag_audit"] = self._ag_audit_summary(a["agency"])
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

    # ── ranking / aggregation ──────────────────────────────────────────
    # These answer "which is biggest / most / top-N" questions directly

    #: Common abbreviation expansions for agency-name resolution. The
    #: warehouse stores abbreviated names ("DEPT OF …"); users and LLMs
    #: often type the long form ("Department of …"). Normalize both sides.
    _AGENCY_ABBREV = {
        "department": "dept",
        "DEPARTMENT": "DEPT",
        "&": "and",
    }

    def _canonical_agencies(self) -> list[str]:
        """All distinct agency names, cached for the connection's lifetime."""
        cached = getattr(self, "_agency_cache", None)
        if cached is None:
            rows = self._query("SELECT DISTINCT agency FROM tier_agency_summary")
            cached = [r["agency"] for r in rows if r.get("agency")]
            self._agency_cache = cached
        return cached

    @staticmethod
    def _norm_agency(s: str) -> str:
        """Normalize an agency string for comparison: upper, expand abbrevs."""
        s = (s or "").upper().strip()
        s = s.replace("DEPARTMENT", "DEPT").replace("&", "AND")
        # collapse punctuation/whitespace to single spaces
        out = []
        for ch in s:
            out.append(ch if (ch.isalnum() or ch == " ") else " ")
        return " ".join("".join(out).split())

    def resolve_agency(self, name: str) -> dict:
        """Map a fuzzy agency name to the canonical warehouse name.

        Returns ``{"input", "match", "confidence", "candidates"}``:

        * ``match`` — best canonical name, or ``None`` if nothing plausible.
        * ``confidence`` — ``exact`` | ``strong`` | ``weak`` | ``none``.
        * ``candidates`` — up to 5 ranked alternates (for disambiguation).

        Scoring is token-overlap on normalized strings (handles
        "Department of Corrections" → "DEPT OF CORRECTIONS",
        "ADOT"/"transportation" → "DEPT OF TRANSPORTATION", etc.). No LLM,
        no network — pure string work over the 100-odd agency names.
        """
        agencies = self._canonical_agencies()
        qn = self._norm_agency(name)
        if not qn:
            return {"input": name, "match": None, "confidence": "none", "candidates": []}

        q_tokens = set(qn.split())
        scored: list[tuple[float, str]] = []
        for canon in agencies:
            cn = self._norm_agency(canon)
            if cn == qn:
                return {"input": name, "match": canon, "confidence": "exact",
                        "candidates": [canon]}
            c_tokens = set(cn.split())
            if not c_tokens:
                continue
            overlap = q_tokens & c_tokens
            if not overlap:
                # substring rescue: "transportation" inside "DEPT OF TRANSPORTATION"
                if qn in cn or cn in qn:
                    scored.append((0.5, canon))
                continue
            # Jaccard-ish: reward shared tokens, normalized by the smaller set
            score = len(overlap) / max(1, min(len(q_tokens), len(c_tokens)))
            # small bonus if the query is fully contained
            if q_tokens <= c_tokens:
                score += 0.25
            scored.append((score, canon))

        if not scored:
            return {"input": name, "match": None, "confidence": "none", "candidates": []}

        scored.sort(key=lambda x: (-x[0], len(x[1])))
        best_score, best = scored[0]
        candidates = [c for _, c in scored[:5]]
        confidence = "strong" if best_score >= 0.75 else "weak"
        return {"input": name, "match": best, "confidence": confidence,
                "candidates": candidates}

    # Sort keys
    # from the pre-aggregated summary tables, rather than forcing a caller
    # (or an LLM tool layer) to sample transactions and guess. Sort keys
    # are whitelisted per table — the ``metric`` argument can never reach
    # SQL as free text, so there's no injection surface.

    #: Allowed sort metrics for rank_agencies → real tier_agency_summary columns.
    _AGENCY_METRICS = {
        "usd_tier1": "usd_tier1",
        "tier12_exposure": "tier12_exposure",
        "hv_exposure": "hv_exposure",
        "n_tier1": "n_tier1",
        "n_flagged": "n_flagged",
        "tier12_pct_of_hv": "tier12_pct_of_hv",
        "avg_risk_score": "avg_risk_score",
        "distinct_flagged_vendors": "distinct_flagged_vendors",
    }

    #: Allowed sort metrics for rank_vendors → real tier_entities columns.
    _VENDOR_METRICS = {
        "usd_tier1": "usd_tier1",
        "flagged_exposure": "flagged_exposure",
        "hv_exposure": "hv_exposure",
        "n_tier1": "n_tier1",
        "n_flagged": "n_flagged",
        "max_risk_score": "max_risk_score",
    }

    #: Allowed sort metrics for rank_programs → real tier_program_summary columns.
    _PROGRAM_METRICS = {
        "tier12_exposure": "tier12_exposure",
        "hv_exposure": "hv_exposure",
        "n_tier1": "n_tier1",
        "max_risk_score": "max_risk_score",
        "distinct_vendors": "distinct_vendors",
    }

    def rank_agencies(
        self, metric: str = "usd_tier1", limit: int = 10
    ) -> dict:
        """Top agencies by a chosen metric — the agency leaderboard.

        Answers "which agency has the most tier-1 exposure / flags / …".
        ``metric`` must be one of :attr:`_AGENCY_METRICS` (defaults to
        Tier-1 dollar exposure); anything else raises ``ValueError`` so an
        upstream caller can correct it rather than get a wrong answer.
        """
        col = self._AGENCY_METRICS.get(metric)
        if col is None:
            raise ValueError(
                f"unknown metric {metric!r}; choose from "
                f"{sorted(self._AGENCY_METRICS)}"
            )
        limit = max(1, min(int(limit), 100))
        rows = self._query(
            f"""
            SELECT agency, cabinet, n_tier1, usd_tier1, n_tier2,
                   tier12_exposure, hv_exposure, tier12_pct_of_hv,
                   n_flagged, distinct_flagged_vendors, avg_risk_score
            FROM tier_agency_summary
            WHERE {col} IS NOT NULL
            ORDER BY {col} DESC
            LIMIT ?
            """,
            (limit,),
        )
        return {"metric": metric, "ranked_by": col, "n": len(rows), "agencies": rows}

    def rank_vendors(
        self, metric: str = "usd_tier1", agency: str | None = None, limit: int = 10
    ) -> dict:
        """Top vendor entities by a chosen metric, optionally within an agency.

        Answers "which vendors have the most tier-1 dollars / flags …".
        ``metric`` is whitelisted via :attr:`_VENDOR_METRICS`. When
        ``agency`` is given, ranks only entities whose ``agencies`` array
        contains that agency (case-insensitive contains match).
        """
        col = self._VENDOR_METRICS.get(metric)
        if col is None:
            raise ValueError(
                f"unknown metric {metric!r}; choose from "
                f"{sorted(self._VENDOR_METRICS)}"
            )
        limit = max(1, min(int(limit), 100))
        where = [f"{col} IS NOT NULL"]
        params: list[Any] = []
        resolved_agency = None
        if agency:
            # Resolve fuzzy/long-form names ("Department of Corrections") to
            # the canonical warehouse name ("DEPT OF CORRECTIONS") so the
            # contains-match hits on the first try.
            res = self.resolve_agency(agency)
            where.append(
                "upper(array_to_string(agencies, '||')) LIKE ?"
            )
            if res["confidence"] in ("exact", "strong"):
                # resolved_agency is the exact canonical name from the
                # warehouse — match it raw (it's what's stored in agencies).
                resolved_agency = res["match"]
                params.append(f"%{resolved_agency.upper()}%")
            else:
                params.append(f"%{agency.upper()}%")
        params.append(limit)
        rows = self._query(
            f"""
            SELECT entity_key, entity_name, n_tier1, usd_tier1, n_flagged,
                   flagged_exposure, hv_exposure, top_tier, max_risk_score,
                   n_agencies, agencies, verify_verdict
            FROM tier_entities
            WHERE {' AND '.join(where)}
            ORDER BY {col} DESC
            LIMIT ?
            """,
            tuple(params),
        )
        return {
            "metric": metric,
            "ranked_by": col,
            "agency_filter": resolved_agency or agency,
            "n": len(rows),
            "vendors": rows,
        }

    def rank_programs(
        self, metric: str = "tier12_exposure", limit: int = 10
    ) -> dict:
        """Top appropriations/programs by a chosen metric.

        ``metric`` is whitelisted via :attr:`_PROGRAM_METRICS`.
        """
        col = self._PROGRAM_METRICS.get(metric)
        if col is None:
            raise ValueError(
                f"unknown metric {metric!r}; choose from "
                f"{sorted(self._PROGRAM_METRICS)}"
            )
        limit = max(1, min(int(limit), 100))
        rows = self._query(
            f"""
            SELECT appropriation, lead_agency, n_tier1, tier12_exposure,
                   hv_exposure, distinct_vendors, max_risk_score
            FROM tier_program_summary
            WHERE {col} IS NOT NULL
            ORDER BY {col} DESC
            LIMIT ?
            """,
            (limit,),
        )
        return {"metric": metric, "ranked_by": col, "n": len(rows), "programs": rows}

    # ── complete-spend aggregation (full transactions table) ────────────
    #
    # The rank_*/leads/entity methods read the TIERED tables, which only
    # contain high-value transactions (>= $100K) selected for forensic
    # review. "How much did agency X spend on Y" questions need the COMPLETE
    # ledger — every check, including the many sub-$100K invoices that make
    # up most operational spend. That lives in the `transactions` view
    # (parquet-backed, ~115M rows, DuckDB-only). This method is the only
    # read path over it.
    #
    # transaction_type: 'EX' = expenditures (actual outflows), 'RV' =
    # revenue/receipts. "Spend" means EX; default to it. The view already
    # restricts to those two types.

    #: Category-name columns searched by the spend() `category` keyword,
    #: most-specific first. All are real columns on `transactions` AND on
    #: `spend_summary` (the Postgres-compatible rollup).
    _SPEND_CATEGORY_COLS = (
        "category_level_2_name",
        "category_level_3_name",
        "category_level_1_name",
        "appropriation_1_name",
    )

    def _spend_filters(
        self,
        agency: str | None,
        fiscal_year: int | None,
        category: str | None,
        tt: str,
    ) -> tuple[list[str], list[Any], str | None]:
        """Build the WHERE clause shared by the full-ledger and rollup paths.

        Returns ``(where_clauses, params, resolved_agency)``. The agency
        column name is ``organization_level_1_name`` on both tables, so the
        same clauses apply identically.
        """
        where: list[str] = []
        params: list[Any] = []

        if tt != "ALL":
            where.append("transaction_type = ?")
            params.append(tt)

        resolved_agency = None
        if agency:
            res = self.resolve_agency(agency)
            if res["confidence"] in ("exact", "strong"):
                resolved_agency = res["match"]
                where.append("organization_level_1_name = ?")
                params.append(resolved_agency)
            else:
                # weak/none: fall back to a contains-match so we still try
                where.append("upper(organization_level_1_name) LIKE ?")
                params.append(f"%{agency.upper()}%")

        if fiscal_year is not None:
            where.append("fiscal_year = ?")
            params.append(int(fiscal_year))

        if category:
            # OR across the category hierarchy + appropriation name.
            cat_clause = " OR ".join(
                f"upper({col}) LIKE ?" for col in self._SPEND_CATEGORY_COLS
            )
            where.append(f"({cat_clause})")
            kw = f"%{category.upper()}%"
            params.extend([kw] * len(self._SPEND_CATEGORY_COLS))

        return where, params, resolved_agency

    def spend(
        self,
        agency: str | None = None,
        fiscal_year: int | None = None,
        category: str | None = None,
        *,
        transaction_type: str = "EX",
        breakdown: str = "category",
        limit: int = 25,
    ) -> dict:
        """Total spending from the COMPLETE transaction ledger.

        Unlike the tiered/forensic methods (which see only >= $100K
        transactions), this aggregates the full ``transactions`` view, so it
        answers "how much did <agency> spend (on <category>) (in FY<year>)"
        accurately — including the long tail of small invoices.

        Parameters
        ----------
        agency:
            Agency name; fuzzy/long-form names are auto-resolved to the
            canonical warehouse name via :meth:`resolve_agency`.
        fiscal_year:
            Restrict to one fiscal year (e.g. 2024). None = all years.
        category:
            Free-text keyword matched (case-insensitive contains) across the
            category hierarchy and appropriation name — e.g. "information
            technology", "travel", "software". None = all categories.
        transaction_type:
            ``EX`` expenditures (default, = "spend"), ``RV`` revenue, or
            ``ALL`` for both. Anything else raises ``ValueError``.
        breakdown:
            ``category`` (level-2 buckets, default), ``year`` (per-FY trend),
            ``vendor`` (top payees — full ledger only), or ``none`` (grand
            total only).
        limit:
            Max breakdown rows (clamped to [1, 200]).

        Returns a dict with the grand ``total``, ``n_txns``, the resolved
        filters, and a ``breakdown`` list. Returns an ``error`` key if
        neither the full ledger nor the spend rollup is available.
        """
        tt = (transaction_type or "EX").upper()
        if tt not in ("EX", "RV", "ALL"):
            raise ValueError(
                f"transaction_type must be EX, RV, or ALL; got {tt!r}"
            )
        if breakdown not in ("category", "year", "vendor", "none"):
            raise ValueError(
                "breakdown must be one of: category, year, vendor, none"
            )
        limit = max(1, min(int(limit), 200))

        has_full = self._table_exists("transactions")
        has_rollup = self._table_exists("spend_summary")

        if not has_full and not has_rollup:
            return {
                "error": (
                    "neither the complete-ledger 'transactions' view nor a "
                    "'spend_summary' rollup is available in this deployment; "
                    "spend totals cannot be computed here."
                ),
                "agency": agency, "fiscal_year": fiscal_year, "category": category,
            }

        # The vendor breakdown requires per-payee grain, which the rollup
        # doesn't carry. On a rollup-only (Postgres) deployment, return a
        # clear error rather than a wrong answer.
        if breakdown == "vendor" and not has_full:
            return {
                "error": (
                    "vendor breakdown requires the full 'transactions' ledger "
                    "(DuckDB+parquet only); the 'spend_summary' rollup on this "
                    "deployment doesn't carry per-vendor detail. Use "
                    "breakdown='category' or 'year' instead."
                ),
                "agency": agency, "fiscal_year": fiscal_year, "category": category,
            }

        where, params, resolved_agency = self._spend_filters(
            agency, fiscal_year, category, tt,
        )
        # The full-ledger path needs amount IS NOT NULL (the rollup already
        # excludes nulls at build time).
        if has_full:
            where.insert(0, "amount IS NOT NULL")
        where_sql = " AND ".join(where)

        # Choose the table and aggregate expressions for each path. The
        # rollup stores pre-summed total_usd / n_txns per grain row, so the
        # grand total is sum(total_usd) / sum(n_txns) — numerically identical
        # to sum(amount) / count(*) over the raw ledger.
        if has_full:
            table = "transactions"
            sum_expr = "round(sum(amount), 0)"
            count_expr = "count(*)"
        else:
            table = "spend_summary"
            sum_expr = "round(sum(total_usd), 0)"
            count_expr = "sum(n_txns)"

        # Grand total (single aggregate over the filtered set).
        total_row = self._query(
            f"SELECT {sum_expr} AS total, {count_expr} AS n "
            f"FROM {table} WHERE {where_sql}",
            tuple(params),
        )[0]

        # Breakdown.
        breakdown_rows: list[dict] = []
        if breakdown != "none" and total_row["n"]:
            group_col = {
                "category": "category_level_2_name",
                "year": "fiscal_year",
                "vendor": "payee_customer_vendor_name",
            }[breakdown]
            order_sql = "grp" if breakdown == "year" else "usd DESC"
            breakdown_rows = self._query(
                f"""
                SELECT {group_col} AS grp,
                       {sum_expr} AS usd,
                       {count_expr} AS n
                FROM {table}
                WHERE {where_sql}
                GROUP BY {group_col}
                ORDER BY {order_sql}
                LIMIT ?
                """,
                tuple(params) + (limit,),
            )

        basis = "complete ledger (all transaction sizes), cash-basis"
        if not has_full:
            basis += " — via spend_summary rollup"

        return {
            "agency": resolved_agency or agency,
            "fiscal_year": fiscal_year,
            "category": category,
            "transaction_type": tt,
            "basis": basis,
            "total": total_row["total"],
            "n_txns": total_row["n"],
            "breakdown_by": breakdown,
            "breakdown": breakdown_rows,
        }

    #: SQL CASE expression that classifies a payee column into an
    #: "attributed" vs "unattributed" bucket. Centralized so the
    #: agency-level and statewide paths stay numerically identical.
    #: ``{col}`` is substituted with the payee column name per table.
    _UNATTRIBUTED_BUCKET_SQL = (
        "CASE "
        "WHEN {col} IS NULL OR trim({col}) = '' THEN 'blank' "
        "WHEN upper({col}) IN ('N/A','NA','NONE','NOT APPLICABLE','UNKNOWN') THEN 'na' "
        "WHEN upper({col}) LIKE '%REDACT%' THEN 'redacted' "
        "WHEN upper({col}) LIKE '%CONFIDENTIAL%' THEN 'confidential' "
        "ELSE 'named' END"
    )

    #: Human-readable labels for each unattributed sub-bucket.
    _UNATTRIBUTED_LABELS = {
        "blank": "Blank / null payee",
        "na": "Marked 'N/A'",
        "redacted": "Redacted",
        "confidential": "Confidential",
    }

    def unattributed_spend(
        self,
        agency: str | None = None,
        *,
        fiscal_year: int | None = None,
        transaction_type: str = "EX",
        limit: int = 25,
    ) -> dict:
        '''Quantify spending that cannot be traced to a named payee.

        For a transparency / due-diligence product, spend booked to a blank,
        "N/A", redacted, or confidential payee *is itself a finding* -- it is
        money that left the treasury without a publicly attributable
        recipient. This method makes that "dark spend" a first-class metric
        rather than noise at the top of vendor lists.

        Parameters
        ----------
        agency:
            Restrict to one agency (fuzzy-resolved). None = statewide, with a
            per-agency leaderboard in ``by_agency``.
        fiscal_year:
            Restrict to one fiscal year. None = all years.
        transaction_type:
            ``EX`` (default), ``RV``, or ``ALL``.
        limit:
            Max agency rows in the statewide leaderboard (clamped [1, 200]).

        Returns a dict with the grand ``total_spend``, ``unattributed_total``,
        ``unattributed_pct``, a ``by_bucket`` split (blank/na/redacted/...), and
        -- when no single agency is requested -- a ``by_agency`` leaderboard
        ranked by unattributed dollars. Returns an ``error`` key when the full
        ledger is unavailable.
        '''
        tt = (transaction_type or "EX").upper()
        if tt not in ("EX", "RV", "ALL"):
            raise ValueError(
                f"transaction_type must be EX, RV, or ALL; got {tt!r}"
            )
        limit = max(1, min(int(limit), 200))

        if not self._table_exists("transactions"):
            return {
                "error": (
                    "the complete-ledger 'transactions' view is not available "
                    "in this deployment; unattributed-spend analysis requires "
                    "per-payee grain (DuckDB+parquet)."
                ),
                "agency": agency, "fiscal_year": fiscal_year,
            }

        col = "payee_customer_vendor_name"
        bucket_sql = self._UNATTRIBUTED_BUCKET_SQL.format(col=col)

        where = ["amount IS NOT NULL"]
        params: list[Any] = []
        if tt != "ALL":
            where.append("transaction_type = ?")
            params.append(tt)
        resolved_agency = None
        if agency:
            res = self.resolve_agency(agency)
            if res["confidence"] in ("exact", "strong"):
                resolved_agency = res["match"]
                where.append("organization_level_1_name = ?")
                params.append(resolved_agency)
            else:
                where.append("upper(organization_level_1_name) LIKE ?")
                params.append(f"%{agency.upper()}%")
        if fiscal_year is not None:
            where.append("fiscal_year = ?")
            params.append(int(fiscal_year))
        where_sql = " AND ".join(where)

        # Per-bucket split (named vs each unattributed reason).
        bucket_rows = self._query(
            f"""
            SELECT {bucket_sql} AS bucket,
                   count(*) AS n, round(sum(amount), 0) AS usd
            FROM transactions WHERE {where_sql}
            GROUP BY bucket
            """,
            tuple(params),
        )
        total_spend = sum(r["usd"] or 0 for r in bucket_rows)
        total_n = sum(r["n"] or 0 for r in bucket_rows)
        by_bucket = [
            {
                "bucket": r["bucket"],
                "label": self._UNATTRIBUTED_LABELS.get(r["bucket"], "Named payee"),
                "n_txns": r["n"],
                "usd": r["usd"],
            }
            for r in bucket_rows
            if r["bucket"] != "named"
        ]
        by_bucket.sort(key=lambda x: x["usd"] or 0, reverse=True)
        unattributed_total = sum(b["usd"] or 0 for b in by_bucket)
        unattributed_n = sum(b["n_txns"] or 0 for b in by_bucket)

        # Statewide leaderboard (only when no single agency is pinned).
        by_agency: list[dict] = []
        if not resolved_agency:
            by_agency = self._query(
                f"""
                SELECT organization_level_1_name AS agency,
                       round(sum(amount), 0) AS total_spend,
                       round(sum(CASE WHEN {bucket_sql} <> 'named'
                                      THEN amount ELSE 0 END), 0) AS unattributed,
                       count(*) AS n_txns
                FROM transactions WHERE {where_sql}
                GROUP BY organization_level_1_name
                HAVING sum(amount) > 0
                ORDER BY unattributed DESC
                LIMIT ?
                """,
                tuple(params) + (limit,),
            )
            for r in by_agency:
                ts = r.get("total_spend") or 0
                r["unattributed_pct"] = round(
                    100.0 * (r.get("unattributed") or 0) / ts, 1
                ) if ts else 0.0

        # Attach Grok-adjudicated, statute-grounded context where available.
        enrich = self._unattributed_enrichment()
        if enrich:
            for r in by_agency:
                ctx = enrich.get(r.get("agency"))
                if ctx:
                    r["context"] = ctx
        agency_context = None
        if resolved_agency and enrich:
            agency_context = enrich.get(resolved_agency)

        return {
            "agency": resolved_agency or agency,
            "fiscal_year": fiscal_year,
            "transaction_type": tt,
            "total_spend": total_spend,
            "n_txns": total_n,
            "unattributed_total": unattributed_total,
            "unattributed_n_txns": unattributed_n,
            "unattributed_pct": round(
                100.0 * unattributed_total / total_spend, 1
            ) if total_spend else 0.0,
            "by_bucket": by_bucket,
            "by_agency": by_agency,
            "agency_context": agency_context,
            "context_meta": (self._unattr_enrich or {}).get("_meta")
            if self._unattr_enrich else None,
            "basis": (
                "complete ledger (all transaction sizes), cash-basis. "
                "Unattributed = payee blank, 'N/A', redacted, or confidential. "
                "Some redaction is statutory (e.g. benefits to individuals); "
                "this is a transparency metric, NOT an allegation of wrongdoing."
            ),
        }

    def _unattributed_enrichment(self) -> dict:
        """Lazy-load the committed Grok untraceable-spend adjudications.

        Returns a dict mapping agency name -> verified context
        (classification, confidence, reason, statutory_basis, notes).
        Degrades to an empty dict when the JSON is absent (so the metric
        still works on deployments built before enrichment ran).
        """
        if self._unattr_enrich is None:
            path = os.path.join(self._enrich_dir, "unattributed_enrichment.json")
            try:
                with open(path) as f:
                    self._unattr_enrich = json.load(f)
            except (OSError, ValueError):
                self._unattr_enrich = {}
        return (self._unattr_enrich or {}).get("agencies", {})

    def _entity_enrichment(self) -> dict:
        """Lazy-load the Grok entity-context adjudications.

        Returns a dict mapping entity_key -> verified real-world context
        (identity, arizona_role, verdict, reason, confidence, citations),
        built by scripts/enrich_entities.py against xAI's Agent Tools API.

        Prefers the materialized ``entity_grok_context`` warehouse table
        (built by scripts/load_entity_enrichment.py; carried to Postgres by
        the push script) so deployments serve it without the JSON on disk.
        Falls back to the committed ``entity_enrichment.json`` for databases
        built before the table existed. Degrades to an empty dict when
        neither is present.
        """
        if self._entity_enrich is None:
            self._entity_enrich = {"entities": self._entity_enrichment_from_db()}
            if not self._entity_enrich["entities"]:
                path = os.path.join(self._enrich_dir, "entity_enrichment.json")
                try:
                    with open(path) as f:
                        self._entity_enrich = json.load(f)
                except (OSError, ValueError):
                    self._entity_enrich = {}
        return (self._entity_enrich or {}).get("entities", {})

    def _entity_enrichment_from_db(self) -> dict:
        """Read the ``entity_grok_context`` table into the JSON-shaped dict.

        Returns ``{}`` when the table is absent (older warehouse) so the
        caller falls back to the committed JSON. List columns (markers,
        agencies, citations) are stored as JSON strings and parsed back here.
        """
        if not self._table_exists("entity_grok_context"):
            return {}
        try:
            rows = self._query(
                """
                SELECT entity_key, entity_name, verdict, confidence, identity,
                       arizona_role, reason, notes, flagged_exposure, top_tier,
                       markers, agencies, citations
                FROM entity_grok_context
                """
            )
        except Exception:
            return {}
        out: dict = {}
        for r in rows:
            def _parse(v):
                if isinstance(v, (list, dict)):
                    return v
                try:
                    return json.loads(v) if v else []
                except (TypeError, ValueError):
                    return []
            out[r["entity_key"]] = {
                "entity_name": r.get("entity_name"),
                "verdict": r.get("verdict"),
                "confidence": r.get("confidence"),
                "identity": r.get("identity"),
                "arizona_role": r.get("arizona_role"),
                "reason": r.get("reason"),
                "notes": r.get("notes"),
                "flagged_exposure": r.get("flagged_exposure"),
                "top_tier": r.get("top_tier"),
                "markers": _parse(r.get("markers")),
                "agencies": _parse(r.get("agencies")),
                "citations": _parse(r.get("citations")),
            }
        return out

    def finding_transactions(
        self,
        finding_id: str,
        *,
        limit: int = 50,
        window_years: int = 1,
    ) -> dict | None:
        '''Surface checkbook transactions implicated by an AG finding.

        This is the core triangulation payoff: given an Auditor-General
        finding (e.g. "$950K across 309 contracts lacked procurement
        documentation"), pull the matching checkbook transactions so a
        reviewer can go from the audited finding straight to the underlying
        spend.

        The match is deterministic and *contextual*, not an accusation:
          1. Same agency (via ``agency_checkbook`` on the report).
          2. Fiscal-year window: the audit FY +/- ``window_years`` (audits
             review a period, and posting dates straddle FY boundaries).
          3. Optional narrowing by the finding's ``fund_keyword`` and
             ``program_area`` when present (matched against the fund and
             category columns).

        Returns ``None`` when the AG tables are absent or the finding id is
        unknown. The returned ``transactions`` are *context for the finding*,
        never themselves "flagged" -- the disclaimer travels in ``basis``.
        '''
        if not (self._table_exists("ag_findings")
                and self._table_exists("ag_reports")
                and self._table_exists("transactions")):
            return None
        limit = max(1, min(int(limit), 200))
        window_years = max(0, min(int(window_years), 3))

        # Resolve the finding + its report context. Prefer ag_finding_context
        # (carries program_area / fund_keyword) and fall back to the raw join.
        ctx = None
        if self._table_exists("ag_finding_context"):
            rows = self._query(
                """
                SELECT finding_id, report_id, agency, fiscal_year,
                       substring(finding_text, 1, 600) AS finding_text_preview,
                       questioned_cost_usd, questioned_cost_confidence,
                       questioned_cost_basis, has_adverse_findings,
                       program_area, fund_keyword
                FROM ag_finding_context WHERE finding_id = ?
                """,
                (finding_id,),
            )
            ctx = rows[0] if rows else None
        if ctx is None:
            rows = self._query(
                """
                SELECT f.finding_id, f.report_id,
                       r.agency_checkbook AS agency, r.fiscal_year,
                       substring(f.finding_text, 1, 600) AS finding_text_preview,
                       f.questioned_cost_usd, f.questioned_cost_confidence,
                       f.questioned_cost_basis, f.has_adverse_findings,
                       NULL AS program_area, NULL AS fund_keyword
                FROM ag_findings f
                JOIN ag_reports r ON r.report_id = f.report_id
                WHERE f.finding_id = ?
                """,
                (finding_id,),
            )
            ctx = rows[0] if rows else None
        if ctx is None or not ctx.get("agency"):
            return None

        agency = ctx["agency"]
        fy = ctx.get("fiscal_year")
        fund_kw = (ctx.get("fund_keyword") or "").strip()
        prog = (ctx.get("program_area") or "").strip()

        where = ["amount IS NOT NULL", "transaction_type = 'EX'",
                 "organization_level_1_name = ?"]
        params: list[Any] = [agency]

        if fy:
            where.append("fiscal_year BETWEEN ? AND ?")
            params.extend([int(fy) - window_years, int(fy) + window_years])

        # Narrow by fund keyword when the finding names a specific fund.
        narrowed_by = []
        if fund_kw:
            where.append(
                "(upper(fund_1_name) LIKE ? OR upper(fund_2_name) LIKE ?)"
            )
            kw = f"%{fund_kw.upper()}%"
            params.extend([kw, kw])
            narrowed_by.append(f"fund~'{fund_kw}'")

        where_sql = " AND ".join(where)

        total_row = self._query(
            f"SELECT round(sum(amount), 0) AS total, count(*) AS n "
            f"FROM transactions WHERE {where_sql}",
            tuple(params),
        )[0]

        txns = self._query(
            f"""
            SELECT posting_date, fiscal_year,
                   payee_customer_vendor_name AS payee,
                   category_level_2_name AS category,
                   fund_1_name AS fund,
                   contract_number, contract_name,
                   round(amount, 0) AS amount,
                   transaction_id
            FROM transactions WHERE {where_sql}
            ORDER BY amount DESC
            LIMIT ?
            """,
            tuple(params) + (limit,),
        )

        return {
            "finding_id": finding_id,
            "report_id": ctx.get("report_id"),
            "agency": agency,
            "audit_fiscal_year": fy,
            "fiscal_year_window": (
                [int(fy) - window_years, int(fy) + window_years] if fy else None
            ),
            "finding_text_preview": ctx.get("finding_text_preview"),
            "questioned_cost_usd": ctx.get("questioned_cost_usd"),
            "questioned_cost_confidence": ctx.get("questioned_cost_confidence"),
            "questioned_cost_basis": ctx.get("questioned_cost_basis"),
            "has_adverse_findings": ctx.get("has_adverse_findings"),
            "program_area": prog or None,
            "fund_keyword": fund_kw or None,
            "narrowed_by": narrowed_by,
            "matched_total": total_row["total"],
            "matched_n_txns": total_row["n"],
            "transactions": txns,
            "basis": (
                "Transactions are checkbook spend by the SAME AGENCY within the "
                "audit's fiscal-year window (cash-basis), provided as CONTEXT "
                "for the audited finding. They are NOT themselves audit findings "
                "or allegations -- the AG finding concerns the agency's "
                "operations, not any individual payee listed here."
            ),
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
