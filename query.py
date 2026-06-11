#!/usr/bin/env python3
"""
OpenBooks Query Module — the "talking to the money" interface.

Talks to warehouse.duckdb (tx_tiered table, 306K rows).  No pre-computation,
no CSV exports, no intermediate files.  Every function is one SQL query.

Usage:
    from query import OpenBooks
    ob = OpenBooks("warehouse.duckdb")

    ob.entity("FONDOMONTE")           # one vendor: nested entity + txns + crosswalk
    ob.leads(tier=1, status="genuine_review")  # action queue
    ob.agency_card("DEPT OF TRANSPORTATION")   # scorecard + FY trend
    ob.explain(transaction_id)         # why this tier: amount + markers x multiplier
    ob.search("healthcare")            # fuzzy search across entities/agencies
    ob.waterfall()                     # Tier 1→5 distribution
    ob.verdicts_pending()              # Tier-1 entities without a verdict
    ob.set_verdict(entity_key, verdict, interest, context)  # conversational curation
"""

import json
import os
import csv
from typing import Optional

import duckdb

try:
    import yaml
except ImportError:
    yaml = None


class OpenBooks:
    """Thin query layer over OpenBooks DuckDB warehouse."""

    def __init__(self, db_path: str = "warehouse.duckdb", config_path: str = "tier_config.yaml"):
        self.db_path = os.path.abspath(db_path)
        self.db = duckdb.connect(self.db_path, read_only=False)
        self._ensure_verdicts_table()
        self._ensure_views()

        self.config = {}
        if yaml and os.path.exists(config_path):
            with open(config_path) as f:
                self.config = yaml.safe_load(f)

    # ── internal helpers ───────────────────────────────────────────────

    def _ensure_verdicts_table(self):
        """Create the writable verdicts table if it doesn't exist."""
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS vendor_verdicts (
                entity_key VARCHAR PRIMARY KEY,
                verdict VARCHAR,
                overtaker_interest INTEGER,
                public_context VARCHAR,
                recommended_action VARCHAR,
                reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Seed from the pre-existing tier_entities verdict columns if empty
        cnt = self.db.execute("SELECT count(*) FROM vendor_verdicts").fetchone()[0]
        if cnt == 0:
            self.db.execute("""
                INSERT OR IGNORE INTO vendor_verdicts (entity_key, verdict, overtaker_interest, public_context)
                SELECT entity_key, verify_verdict, overtaker_interest, public_context
                FROM tier_entities
                WHERE verify_verdict IS NOT NULL
            """)

    def _ensure_views(self):
        """Create read-only views — never mutate source tables."""
        # tx_with_verdict: computes entity_key on the fly from payee (same
        # normalization as build_entities.sql), then LEFT JOINs verdicts.
        self.db.execute("""
            CREATE OR REPLACE VIEW tx_with_verdict AS
            WITH t AS (
                SELECT *,
                    upper(trim(regexp_replace(payee, '\\s+', ' ', 'g'))) AS entity_key
                FROM tx_tiered
                WHERE payee IS NOT NULL AND trim(payee) <> ''
            )
            SELECT t.*,
                   coalesce(v.verdict, 'screened_unreviewed') AS verify_status,
                   v.overtaker_interest,
                   v.public_context
            FROM t
            LEFT JOIN vendor_verdicts v USING(entity_key)
        """)

    def _row_to_dict(self, row, columns):
        """Convert a DuckDB row tuple to dict."""
        d = dict(zip(columns, row))
        # JSON-serialize date and Decimal types
        for k, v in d.items():
            if hasattr(v, 'isoformat'):
                d[k] = v.isoformat()
            elif hasattr(v, '__float__') and not isinstance(v, (int, float, str, type(None))):
                d[k] = float(v)
        return d

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Run a query and return list of dicts."""
        result = self.db.execute(sql, params)
        columns = [d[0] for d in result.description]
        return [self._row_to_dict(row, columns) for row in result.fetchall()]

    # ── public API ─────────────────────────────────────────────────────

    def entity(self, name_or_key: str) -> Optional[dict]:
        """
        Return one vendor entity: summary + top-30 transactions + crosswalk info.
        The primary entry point for "tell me about vendor X."
        """
        q = name_or_key.upper().strip()

        # Try entity_key first, then fuzzy payee match
        summary = self._query("""
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
        """, (q, q))

        if not summary:
            return None

        s = summary[0]
        ek = s['entity_key']

        # Top 30 transactions, nested
        txns = self._query("""
            SELECT fiscal_year AS fy, posting_date AS date, agency,
                   category1 AS cat, amount, risk_score AS score, tier,
                   array_to_string(fired_markers, ', ') AS markers
            FROM tx_with_verdict
            WHERE entity_key = ? AND tier IN (1, 2)
            ORDER BY risk_score DESC, amount DESC
            LIMIT 30
        """, (ek,))

        s['transactions'] = txns
        s['n_txn_displayed'] = len(txns)

        # Crosswalk: other names merged into this entity (load from CSV since it's not in DuckDB)
        import csv, os
        crosswalk_path = os.path.join(os.path.dirname(self.db_path), 'mart', 'entity_crosswalk.csv')
        crosswalk = []
        if os.path.exists(crosswalk_path):
            with open(crosswalk_path) as f:
                for row in csv.DictReader(f):
                    if row.get('parent_key', '') == ek and row.get('entity_key', '') != ek:
                        crosswalk.append(row)
        s['names_merged'] = crosswalk
        s['n_names_merged'] = len(crosswalk)

        return s

    def leads(
        self,
        tier: int = 1,
        status: Optional[str] = None,
        agency: Optional[str] = None,
        min_amount: float = 0,
        limit: int = 50
    ) -> list[dict]:
        """
        The action queue — filterable Tier-1 (or any tier) transactions.
        status: 'genuine_review', 'screened_unreviewed', 'explained_benign', etc.
        This is the conversation tool's main workhorse.
        """
        where = ["t.tier = ?"]
        params = [tier]

        if status:
            where.append("coalesce(v.verdict, 'screened_unreviewed') = ?")
            params.append(status)
        if agency:
            where.append("t.agency = ?")
            params.append(agency)
        if min_amount:
            where.append("t.amount >= ?")
            params.append(min_amount)

        sql = f"""
            WITH t AS (
                SELECT *,
                    upper(trim(regexp_replace(payee, '\\s+', ' ', 'g'))) AS entity_key
                FROM tx_tiered
                WHERE payee IS NOT NULL AND trim(payee) <> ''
            )
            SELECT t.transaction_id, t.fiscal_year, t.posting_date, t.agency,
                   t.category1, t.payee, t.amount, t.risk_score, t.tier,
                   array_to_string(t.fired_markers, ', ') AS markers,
                   coalesce(v.verdict, 'screened_unreviewed') AS verify_status,
                   v.overtaker_interest, v.public_context
            FROM t
            LEFT JOIN vendor_verdicts v ON v.entity_key = t.entity_key
            WHERE {' AND '.join(where)}
            ORDER BY t.risk_score DESC, t.amount DESC
            LIMIT {limit}
        """
        return self._query(sql, tuple(params))

    def agency_card(self, agency: str) -> Optional[dict]:
        """
        Agency scorecard + FY trend series.
        Bondholder question: "How exposed & how well-governed is my obligor?"
        """
        scorecard = self._query("""
            SELECT agency, cabinet, hv_txn, hv_exposure,
                   n_tier1, usd_tier1, n_tier2, usd_tier2, n_tier3, n_flagged,
                   tier12_exposure, tier12_pct_of_hv, avg_risk_score, max_risk_score,
                   distinct_flagged_vendors, top_markers
            FROM tier_agency_summary
            WHERE agency = ?
        """, (agency,))

        if not scorecard:
            return None

        trend = self._query("""
            SELECT fiscal_year, hv_txn, hv_exposure,
                   n_tier1, usd_tier1, n_tier12, usd_tier12,
                   tier12_pct_of_hv, avg_risk_score
            FROM tier_agency_year
            WHERE agency = ?
            ORDER BY fiscal_year
        """, (agency,))

        result = scorecard[0]
        result['trend'] = trend

        # Top-5 flagged vendors for this agency
        vendors = self._query("""
            SELECT entity_key, mode(payee) AS entity_name,
                   count(*) FILTER (tier=1) AS n_tier1,
                   round(sum(amount) FILTER (tier=1), 0) AS usd_tier1,
                   round(max(risk_score), 2) AS max_score
            FROM tx_with_verdict
            WHERE agency = ? AND tier IN (1, 2)
            GROUP BY entity_key
            ORDER BY usd_tier1 DESC
            LIMIT 5
        """, (agency,))
        result['top_flagged_vendors'] = vendors

        return result

    def explain(self, transaction_id: str) -> Optional[dict]:
        """
        "Why this tier?" — breakdown: amount → magnitude band, category →
        multiplier, each marker family contribution, risk_score calculation.
        """
        row = self._query("""
            SELECT transaction_id, fiscal_year, agency, payee, category1,
                   amount, mag_class, risk_band, risk_score, tier,
                   f_round, f_dup, f_conc, f_newvendor, f_yearend,
                   f_peer, f_account, f_rail, f_entity,
                   marker_sum, category_multiplier,
                   fired_markers, n_markers
            FROM tx_tiered
            WHERE transaction_id = ?
        """, (transaction_id,))

        if not row:
            return None

        r = row[0]
        marker_families = {
            'round (negotiated amounts)': r['f_round'],
            'duplicate (same vendor+amount, distinct docs)': r['f_dup'],
            'concentration (vendor share of agency)': r['f_conc'],
            'new vendor (first appearance by name)': r['f_newvendor'],
            'year-end (closing entry timing)': r['f_yearend'],
            'peer outlier (dwarfs agency x category norm)': r['f_peer'],
            'accountability (blank payee / missing contract)': r['f_account'],
            'manual rail (JV/INTERNAL/NULL payment)': r['f_rail'],
            'entity name (individual-looking / masked payee)': r['f_entity'],
        }
        # Only show families that contributed
        r['marker_breakdown'] = {k: v for k, v in marker_families.items() if v > 0}

        r['calculation'] = (
            f"marker_sum = {r['marker_sum']} × "
            f"category_multiplier({r['category1']}) = {r['category_multiplier']} → "
            f"risk_score = {r['risk_score']} → {r['risk_band']} → "
            f"mag_class = {r['mag_class']} → Tier {r['tier']}"
        )

        return r

    def search(self, query: str, limit: int = 20) -> dict:
        """
        Fuzzy search across entities, agencies, appropriations.
        Returns {entities: [...], agencies: [...], programs: [...]}.
        """
        q = f"%{query.upper()}%"

        entities = self._query("""
            SELECT entity_key, entity_name, n_tier1, usd_tier1, top_tier,
                   max_risk_score, verify_verdict, overtaker_interest
            FROM tier_entities
            WHERE upper(entity_name) LIKE ?
               OR (agencies IS NOT NULL AND upper(array_to_string(agencies, ' ')) LIKE ?)
               OR (top_markers IS NOT NULL AND upper(array_to_string(top_markers, ' ')) LIKE ?)
            ORDER BY usd_tier1 DESC
            LIMIT ?
        """, (q, q, q, limit))

        agencies = self._query("""
            SELECT agency, cabinet, hv_exposure, usd_tier1, tier12_pct_of_hv
            FROM tier_agency_summary
            WHERE upper(agency) LIKE ?
            ORDER BY usd_tier1 DESC
            LIMIT ?
        """, (q, limit))

        programs = self._query("""
            SELECT appropriation, lead_agency, tier12_exposure, n_tier1
            FROM tier_program_summary
            WHERE upper(appropriation) LIKE ?
            ORDER BY tier12_exposure DESC
            LIMIT ?
        """, (q, limit))

        return {
            'query': query,
            'entities': entities,
            'agencies': agencies,
            'programs': programs,
        }

    def waterfall(self) -> dict:
        """
        Tier 1 → 5 distribution.  The "does the model clear most dollars?" story.
        """
        rows = self._query("""
            SELECT tier,
                   count(*) AS n_txn,
                   round(sum(amount), 0) AS exposure,
                   round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct_txn,
                   round(100.0 * sum(amount) / sum(sum(amount)) OVER (), 2) AS pct_exposure,
                   round(avg(risk_score), 2) AS avg_score
            FROM tx_tiered
            GROUP BY tier
            ORDER BY tier
        """)

        total = self._query("""
            SELECT count(*) AS n, round(sum(amount), 0) AS exposure FROM tx_tiered
        """)[0]

        return {
            'total_txns': total['n'],
            'total_exposure': total['exposure'],
            'tiers': rows,
            'summary': (
                f"{rows[0]['n_txn']} Tier-1 txns (${rows[0]['exposure']/1e9:.2f}B) → "
                f"{rows[3]['n_txn']} Tier-4 clean (${rows[3]['exposure']/1e9:.1f}B) → "
                f"{rows[4]['n_txn']} unranked (${rows[4]['exposure']/1e9:.1f}B). "
                f"{rows[0]['pct_txn']}% of txns get the top review tier."
            ) if rows else 'No tiered data available.'
        }

    def verdicts_pending(self, tier: int = 1, limit: int = 50) -> list[dict]:
        """
        Tier-1 entities that have NOT been reviewed yet (screened_unreviewed).
        This is the reviewer's queue — entities that need human curation.
        """
        return self._query("""
            SELECT e.entity_key, e.entity_name,
                   e.n_tier1, e.usd_tier1, e.max_risk_score, e.top_markers,
                   e.first_year, e.last_year
            FROM tier_entities e
            LEFT JOIN vendor_verdicts v ON v.entity_key = e.entity_key
            WHERE e.n_tier1 > 0 AND v.verdict IS NULL
            ORDER BY e.usd_tier1 DESC
            LIMIT ?
        """, (limit,))

    def set_verdict(
        self,
        entity_key: str,
        verdict: str,
        overtaker_interest: int = 0,
        public_context: str = "",
        recommended_action: str = ""
    ) -> dict:
        """
        Assign or update a verification verdict conversationally.
        "Mark Fondomonte as genuine_review with overtaker_interest 5."
        """
        valid_verdicts = {'genuine_review', 'explained_benign', 'false_positive_marker', 'mixed'}
        if verdict not in valid_verdicts:
            return {'error': f'Invalid verdict. Must be one of: {", ".join(sorted(valid_verdicts))}'}

        self.db.execute("""
            INSERT OR REPLACE INTO vendor_verdicts
                (entity_key, verdict, overtaker_interest, public_context, recommended_action, reviewed_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (entity_key.upper().strip(), verdict, overtaker_interest,
               public_context, recommended_action or None))

        # Return the updated entity
        return self.entity(entity_key) or {'status': 'ok', 'entity_key': entity_key.upper().strip(), 'verdict': verdict}

    def close(self):
        """Close the DuckDB connection."""
        self.db.close()


# ── CLI entry point for quick testing ──────────────────────────────────
if __name__ == "__main__":
    import sys

    ob = OpenBooks()

    if len(sys.argv) < 2:
        print("Usage: python query.py <command> [args]")
        print("Commands: entity, leads, agency, explain, search, waterfall, pending")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "entity":
        result = ob.entity(sys.argv[2] if len(sys.argv) > 2 else "FONDOMONTE")
    elif cmd == "leads":
        status = sys.argv[2] if len(sys.argv) > 2 else None
        result = ob.leads(tier=1, status=status, limit=10)
    elif cmd == "agency":
        result = ob.agency_card(sys.argv[2] if len(sys.argv) > 2 else "DEPT OF TRANSPORTATION")
    elif cmd == "explain":
        result = ob.explain(sys.argv[2]) if len(sys.argv) > 2 else {"error": "provide transaction_id"}
    elif cmd == "search":
        result = ob.search(sys.argv[2] if len(sys.argv) > 2 else "health")
    elif cmd == "waterfall":
        result = ob.waterfall()
    elif cmd == "pending":
        result = ob.verdicts_pending(limit=20)
    else:
        result = {"error": f"unknown command: {cmd}"}

    print(json.dumps(result, indent=2, default=str))

    ob.close()
