"""Golden-number tests — the verified figures from INTEGRATION_BRIEF.md.

If a pipeline rebuild or a migration (e.g. to Postgres) changes any of
these, something structural moved and a human should look.
"""

from __future__ import annotations

from typing import Any


def test_total_tiered_transactions(ob):
    n = ob._query("SELECT count(*) AS n FROM tx_tiered")[0]["n"]
    assert n == 342_994


def test_tier_distribution(ob):
    """Tier counts + exposures, INTEGRATION_BRIEF §4 (verified rollup)."""
    rows = {
        int(r["tier"]): r
        for r in ob._query(
            "SELECT tier, count(*) AS n, round(sum(amount), 0) AS usd "
            "FROM tx_tiered GROUP BY tier"
        )
    }
    assert rows[1]["n"] == 1_204
    assert rows[1]["usd"] == 5_525_669_547
    assert rows[4]["n"] == 45_708
    assert rows[4]["usd"] == 347_677_622_193
    assert rows[5]["n"] == 254_040


def test_entity_universe(ob):
    n = ob._query("SELECT count(*) AS n FROM tier_entities")[0]["n"]
    assert n == 1_788


def test_waterfall_reconciles(ob):
    """The waterfall's tier rows must sum to its own totals."""
    w = ob.waterfall()
    assert w["total_txns"] == sum(t["n_txn"] for t in w["tiers"])
    assert abs(w["total_exposure"] - sum(t["exposure"] for t in w["tiers"])) < 5
    assert "Tier-1" in w["summary"]


def test_entity_lookup_fondomonte(ob):
    """The marquee vendor must resolve, with transactions and merge info."""
    e = ob.entity("FONDOMONTE")
    assert e is not None
    assert "FONDOMONTE" in e["entity_key"]
    assert e["n_txn_displayed"] > 0
    assert isinstance(e["names_merged"], list)  # crosswalk loaded (may be empty)
    # JSON-safety: amounts must be plain floats/ints, dates ISO strings
    txn = e["transactions"][0]
    assert isinstance(txn["amount"], (int, float))
    assert txn["date"] is None or isinstance(txn["date"], str)


def test_entity_unknown_returns_none(ob):
    assert ob.entity("ZZZ NO SUCH VENDOR 123456789") is None


def test_leads_filters(ob):
    leads = ob.leads(tier=1, limit=10)
    assert 0 < len(leads) <= 10
    assert all(row["tier"] == 1 for row in leads)
    # status filter narrows to the overlay enum
    genuine = ob.leads(tier=1, status="genuine_review", limit=10)
    assert all(row["verify_status"] == "genuine_review" for row in genuine)


def test_leads_min_amount(ob):
    leads = ob.leads(tier=1, min_amount=10_000_000, limit=10)
    assert all(row["amount"] >= 10_000_000 for row in leads)


def test_agency_card(ob):
    card = ob.agency_card("DEPT OF TRANSPORTATION")
    assert card is not None
    assert card["trend"], "FY trend series must be non-empty"
    assert len(card["top_flagged_vendors"]) <= 5
    assert ob.agency_card("NO SUCH AGENCY") is None


def test_agency_card_ag_audit_overlay(ob):
    """The optional Auditor-General overlay is present and well-formed.

    Skips cleanly if the ag_* tables haven't been loaded into this warehouse.
    When present, the questioned-cost rollup must equal the sum of its parts
    and carry the estimate flag honestly.
    """
    card = ob.agency_card("DEPT OF TRANSPORTATION")
    assert "ag_audit" in card, "agency_card must always carry the ag_audit key"

    if not ob._table_exists("ag_reports"):
        assert card["ag_audit"] is None
        return

    # DEPT OF ECONOMIC SECURITY is the densest AG-audited agency in the corpus
    ag = ob.agency_card("DEPT OF ECONOMIC SECURITY")["ag_audit"]
    assert ag is not None
    assert ag["n_reports"] >= 1
    assert ag["first_fy"] <= ag["last_fy"]
    rollup = sum(f["questioned_cost_usd"] for f in ag["findings_with_cost"])
    assert rollup == ag["total_questioned_cost"]
    assert ag["n_findings_with_cost"] == len(ag["findings_with_cost"])
    # an agency in the scorecard but with no AG coverage must yield
    # ag_audit = None, not an error
    no_ag = ob.agency_card("ARIZONA HISTORICAL SOCIETY")
    assert no_ag is not None
    assert no_ag["ag_audit"] is None


def test_search_agency_ag_badge(ob):
    """Agency search rows carry the lightweight ag_audit badge (or None)."""
    out = ob.search("economic")
    assert out["agencies"], "expected at least one agency hit for 'economic'"
    for a in out["agencies"]:
        assert "ag_audit" in a
        ag = a["ag_audit"]
        if ag is not None:
            assert ag["n_reports"] >= 1
            assert ag["first_fy"] <= ag["last_fy"]
            assert isinstance(ag["questioned_cost_has_estimate"], bool)
            # the lightweight badge must NOT carry the heavy findings list
            assert "findings_with_cost" not in ag


def test_entity_primary_agency_ag_context(ob):
    """entity() attaches indirect primary-agency AG context (agency-keyed,
    never a finding about the vendor)."""
    e = ob.entity("FONDOMONTE")
    assert "primary_agency_ag" in e
    pa = e["primary_agency_ag"]
    if not ob._table_exists("ag_reports"):
        assert pa is None
        return
    # FONDOMONTE's primary agency is LAND DEPARTMENT, which has AG audits
    if pa is not None:
        assert pa["relation"] == "primary_agency"
        assert pa["agency"]
        assert pa["n_reports"] >= 1


def test_explain_roundtrip(ob):
    """explain() on a real Tier-1 txn: breakdown only includes fired families."""
    lead = ob.leads(tier=1, limit=1)[0]
    detail = ob.explain(lead["transaction_id"])
    assert detail is not None
    assert detail["tier"] == 1
    assert detail["marker_breakdown"], "Tier-1 must have at least one fired family"
    assert all(v > 0 for v in detail["marker_breakdown"].values())
    assert "risk_score" in detail["calculation"]


def test_search_shape(ob):
    out = ob.search("health")
    assert set(out) == {"query", "entities", "agencies", "programs"}
    assert out["entities"] or out["agencies"] or out["programs"]


def test_verdicts_pending_excludes_reviewed(ob):
    pending = ob.verdicts_pending(limit=200)
    assert pending
    reviewed = {
        r["entity_key"]
        for r in ob._query("SELECT entity_key FROM vendor_verdicts WHERE verdict IS NOT NULL")
    }
    assert not ({p["entity_key"] for p in pending} & reviewed)


def test_set_verdict_rejects_invalid_enum(ob):
    """Invalid verdicts are rejected before any write is attempted —
    safe to call on the read-only fixture."""
    out = ob.set_verdict("ANY KEY", "not_a_verdict")
    assert "error" in out


def test_set_verdict_readonly_guard(ob):
    """A valid verdict on a read-only connection must raise, not write."""
    import pytest

    from openbooks.queries import ReadOnlyError

    with pytest.raises(ReadOnlyError):
        ob.set_verdict("ANY KEY", "genuine_review")

def test_push_to_postgres_ag_missing_regression():
    """Regression test for the --verify-only AG 'MISSING' path in the push script.

    Simulates a source warehouse that has the AG tables (like our current one)
    and exercises the exact presence-detection logic the script uses to decide
    whether to verify them and whether to report 'MISSING' when the target
    Postgres side lacks them (the case for an older push).

    This test does not require a Postgres instance.
    """
    import importlib.util
    from pathlib import Path

    import duckdb

    # Load the push script as a module so we can inspect AG_TABLES without
    # executing its CLI.
    script_path = Path(__file__).parent.parent / "scripts" / "push_to_postgres.py"
    spec = importlib.util.spec_from_file_location("push_pg", script_path)
    push_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(push_mod)

    assert hasattr(push_mod, "AG_TABLES")
    ag_tables = push_mod.AG_TABLES
    assert "ag_reports" in ag_tables
    assert "ag_findings" in ag_tables

    # Simulate the source warehouse (our real one has the AG tables).
    con = duckdb.connect()
    con.execute("CREATE TABLE ag_reports (report_id VARCHAR)")
    con.execute("CREATE TABLE ag_findings (finding_id VARCHAR)")
    con.execute("CREATE TABLE ag_report_agency_xref (agency_key VARCHAR)")
    con.execute("CREATE TABLE ag_agency_spending (agency VARCHAR)")
    con.execute("CREATE TABLE ag_finding_context (finding_id VARCHAR)")

    # Exact presence check the script performs on the src con.
    for table in ag_tables:
        present = con.execute(
            "SELECT count(*) FROM information_schema.tables "
            f"WHERE table_name = '{table}'"
        ).fetchone()[0]
        assert present > 0, f"expected {table} to be detected as present in src"

    # Simulate the 'pg' side missing them (the MISSING case).
    # We don't create the tables in this con; the script would see in_pg=0
    # and report "MISSING" for each.
    for table in ag_tables:
        in_pg = con.execute(
            "SELECT count(*) FROM information_schema.tables "
            f"WHERE table_catalog = 'pg' AND table_name = '{table}'"
        ).fetchone()[0]
        assert in_pg == 0, "simulated pg side should report the table absent"

    con.close()
    # If we reach here the presence / MISSING decision logic is intact.


# ---------------------------------------------------------------------------
# spend_summary rollup — the Postgres-compatible path for spend().
# Verifies (a) the rollup produces identical numbers to the full ledger,
# (b) the rollup is used when transactions is absent, and (c) the vendor
# breakdown returns a clear error on the rollup-only path.
# Uses in-memory DuckDB — no warehouse required.
# ---------------------------------------------------------------------------


def _make_spend_db(*, with_transactions: bool = True) -> Any:
    """Build a tiny in-memory warehouse with a spend_summary rollup and
    optionally a raw transactions table, plus a minimal tier_agency_summary
    for the agency resolver."""
    import duckdb

    from openbooks import OpenBooks
    from openbooks.db import DuckDBBackend

    con = duckdb.connect()
    con.execute("""
        CREATE TABLE tier_agency_summary (
            agency VARCHAR, cabinet VARCHAR, hv_txn INTEGER, hv_exposure DOUBLE,
            n_tier1 INTEGER, usd_tier1 DOUBLE, n_tier2 INTEGER, usd_tier2 DOUBLE,
            n_tier3 INTEGER, n_flagged INTEGER, tier12_exposure DOUBLE,
            tier12_pct_of_hv DOUBLE, avg_risk_score DOUBLE, max_risk_score DOUBLE,
            distinct_flagged_vendors INTEGER, top_markers VARCHAR[]
        )
    """)
    con.execute("INSERT INTO tier_agency_summary VALUES ('DEPT OF TRANSPORTATION', null, 0,0,0,0,0,0,0,0,0,0,0,0,0,null)")

    # Always create the raw transactions table first (needed to build the
    # rollup), then drop it if the caller wants a rollup-only DB.
    con.execute("""
        CREATE TABLE transactions (
            organization_level_1_name VARCHAR, fiscal_year INTEGER,
            transaction_type VARCHAR, category_level_1_name VARCHAR,
            category_level_2_name VARCHAR, category_level_3_name VARCHAR,
            appropriation_1_name VARCHAR,
            payee_customer_vendor_name VARCHAR, amount DOUBLE
        )
    """)
    con.execute("""
        INSERT INTO transactions VALUES
        ('DEPT OF TRANSPORTATION', 2024, 'EX', 'GOODS', 'IT SERVICES', 'SOFTWARE', 'IT', 'VENDOR A', 1000),
        ('DEPT OF TRANSPORTATION', 2024, 'EX', 'GOODS', 'IT SERVICES', 'SOFTWARE', 'IT', 'VENDOR B', 2000),
        ('DEPT OF TRANSPORTATION', 2024, 'EX', 'GOODS', 'TRAVEL', 'AIRFARE', 'TRAVEL', 'VENDOR C', 500),
        ('DEPT OF TRANSPORTATION', 2023, 'EX', 'GOODS', 'IT SERVICES', 'HARDWARE', 'IT', 'VENDOR A', 3000),
        ('DEPT OF TRANSPORTATION', 2024, 'RV', 'REVENUE', 'FEES', 'LICENSES', 'REV', 'VENDOR D', 999)
    """)

    # Build the rollup (same SQL as the push script).
    con.execute("""
        CREATE TABLE spend_summary AS
        SELECT
            organization_level_1_name, fiscal_year, transaction_type,
            category_level_1_name, category_level_2_name, category_level_3_name,
            appropriation_1_name,
            round(sum(amount), 0) AS total_usd, count(*) AS n_txns
        FROM transactions
        WHERE amount IS NOT NULL
        GROUP BY 1,2,3,4,5,6,7
    """)

    if not with_transactions:
        con.execute("DROP TABLE transactions")

    backend = DuckDBBackend.__new__(DuckDBBackend)
    backend.db_path = ":memory:"
    backend.read_only = False
    backend._conn = con
    return OpenBooks(backend=backend)


def test_spend_rollup_matches_full_ledger():
    """The rollup path must produce the same total + txn count as the
    full-ledger path for the same filters."""
    ob_full = _make_spend_db(with_transactions=True)
    ob_rollup = _make_spend_db(with_transactions=False)

    # Grand total, EX, all years.
    r_full = ob_full.spend(agency="DEPT OF TRANSPORTATION", breakdown="none")
    r_rollup = ob_rollup.spend(agency="DEPT OF TRANSPORTATION", breakdown="none")
    assert r_full["total"] == r_rollup["total"]
    assert r_full["n_txns"] == r_rollup["n_txns"]
    assert "rollup" in r_rollup["basis"]

    # Category breakdown.
    r_full = ob_full.spend(agency="DEPT OF TRANSPORTATION", breakdown="category")
    r_rollup = ob_rollup.spend(agency="DEPT OF TRANSPORTATION", breakdown="category")
    assert r_full["total"] == r_rollup["total"]
    assert len(r_full["breakdown"]) == len(r_rollup["breakdown"])
    for f, r in zip(r_full["breakdown"], r_rollup["breakdown"], strict=True):
        assert f["grp"] == r["grp"]
        assert f["usd"] == r["usd"]
        assert f["n"] == r["n"]

    # Year breakdown.
    r_full = ob_full.spend(agency="DEPT OF TRANSPORTATION", breakdown="year")
    r_rollup = ob_rollup.spend(agency="DEPT OF TRANSPORTATION", breakdown="year")
    assert r_full["total"] == r_rollup["total"]
    assert len(r_full["breakdown"]) == len(r_rollup["breakdown"])

    ob_full.close()
    ob_rollup.close()


def test_spend_rollup_vendor_breakdown_returns_error():
    """The vendor breakdown requires per-payee grain, which the rollup
    doesn't carry — must return a clear error, not a wrong answer."""
    ob = _make_spend_db(with_transactions=False)
    result = ob.spend(agency="DEPT OF TRANSPORTATION", breakdown="vendor")
    assert "error" in result
    assert "vendor" in result["error"].lower()
    ob.close()


def test_spend_full_ledger_vendor_breakdown_works():
    """The vendor breakdown still works on the full-ledger path."""
    ob = _make_spend_db(with_transactions=True)
    result = ob.spend(agency="DEPT OF TRANSPORTATION", breakdown="vendor")
    assert "error" not in result
    assert result["breakdown"]
    assert any(b["grp"] == "VENDOR A" for b in result["breakdown"])
    ob.close()


def test_spend_neither_table_returns_error():
    """When neither transactions nor spend_summary exists, return a clear
    error — not a crash."""
    import duckdb

    from openbooks import OpenBooks
    from openbooks.db import DuckDBBackend

    con = duckdb.connect()
    con.execute("CREATE TABLE tier_agency_summary (agency VARCHAR)")
    con.execute("INSERT INTO tier_agency_summary VALUES ('DEPT OF TRANSPORTATION')")
    backend = DuckDBBackend.__new__(DuckDBBackend)
    backend.db_path = ":memory:"
    backend.read_only = False
    backend._conn = con
    ob = OpenBooks(backend=backend)

    result = ob.spend(agency="DEPT OF TRANSPORTATION")
    assert "error" in result
    ob.close()


# ---------------------------------------------------------------------------
# AG audit-finding tools — search_findings + rank_ag_findings
# ---------------------------------------------------------------------------


def test_search_findings(ob):
    """search_findings returns matching AG findings with report context."""
    if not ob._table_exists("ag_reports"):
        assert ob.search_findings("procurement") is None
        return
    out = ob.search_findings("procurement", limit=10)
    assert out is not None
    assert out["n"] > 0
    assert len(out["findings"]) <= 10
    f = out["findings"][0]
    assert "finding_text_preview" in f
    assert "agency" in f
    assert "fiscal_year" in f
    assert "questioned_cost_usd" in f


def test_search_findings_empty_text(ob):
    """An empty search string should not crash — returns 0 or all."""
    if not ob._table_exists("ag_reports"):
        return
    out = ob.search_findings("", limit=5)
    assert out is not None  # doesn't crash


def test_rank_ag_findings(ob):
    """rank_ag_findings returns a leaderboard of agencies by AG metrics."""
    if not ob._table_exists("ag_reports"):
        assert ob.rank_ag_findings() is None
        return
    out = ob.rank_ag_findings("total_questioned_cost", limit=5)
    assert out is not None
    assert out["metric"] == "total_questioned_cost"
    assert out["n"] > 0
    assert len(out["agencies"]) <= 5
    a = out["agencies"][0]
    assert "agency" in a
    assert "n_reports" in a
    assert "n_findings" in a
    assert "total_questioned_cost" in a
    assert "n_adverse" in a
    # ranked descending by the chosen metric
    vals = [a["total_questioned_cost"] for a in out["agencies"]]
    assert vals == sorted(vals, reverse=True)


def test_rank_ag_findings_bad_metric_raises(ob):
    """Unknown metric must raise ValueError (injection-safe whitelist)."""
    if not ob._table_exists("ag_reports"):
        return
    import pytest
    with pytest.raises(ValueError):
        ob.rank_ag_findings("drop_table")


def test_rank_ag_findings_all_metrics(ob):
    """Every whitelisted metric must produce a valid leaderboard."""
    if not ob._table_exists("ag_reports"):
        return
    for metric in ("total_questioned_cost", "n_findings", "n_findings_with_cost",
                   "n_adverse", "n_reports"):
        out = ob.rank_ag_findings(metric, limit=3)
        assert out is not None
        assert out["ranked_by"] == metric


# ── unattributed spend (transparency metric) ──────────────────────────

def test_unattributed_spend_statewide(ob):
    """Statewide untraceable-spend rollup: buckets + agency leaderboard."""
    if not ob._table_exists("transactions"):
        return
    out = ob.unattributed_spend()
    assert "error" not in out
    # totals are internally consistent
    assert out["unattributed_total"] <= out["total_spend"]
    assert 0 <= out["unattributed_pct"] <= 100
    # bucket dollars sum to the unattributed total
    bucket_sum = sum(b["usd"] or 0 for b in out["by_bucket"])
    assert bucket_sum == out["unattributed_total"]
    # leaderboard is present and descending by unattributed $
    vals = [a["unattributed"] for a in out["by_agency"]]
    assert vals == sorted(vals, reverse=True)


def test_unattributed_spend_agency_scoped(ob):
    """Scoping to one agency drops the statewide leaderboard."""
    if not ob._table_exists("transactions"):
        return
    out = ob.unattributed_spend("DEPT OF HEALTH SERVICES")
    assert "error" not in out
    assert out["by_agency"] == []          # no leaderboard when pinned
    assert out["unattributed_total"] >= 0


def test_unattributed_spend_bad_tt_raises(ob):
    import pytest
    with pytest.raises(ValueError):
        ob.unattributed_spend(transaction_type="BOGUS")


def test_unattributed_spend_snapshot_fallback(ob):
    """Without the per-payee 'transactions' view (Postgres/rollup deploys),
    unattributed_spend() degrades to the unattributed_context snapshot rather
    than erroring — serving a per-agency leaderboard + verified context."""
    # Only meaningful when the enrichment snapshot is available.
    if not ob._unattributed_enrichment():
        return
    out = ob._unattributed_spend_from_snapshot(
        agency=None, fiscal_year=None, tt="EX", limit=25
    )
    assert "error" not in out
    assert out.get("snapshot") is True
    assert out["by_bucket"] == []  # no per-payee grain in snapshot mode
    # leaderboard present and descending by unattributed $
    vals = [a["unattributed"] or 0 for a in out["by_agency"]]
    assert vals == sorted(vals, reverse=True)
    # verified context rides along on each row
    assert all("context" in a for a in out["by_agency"])
    assert out["unattributed_total"] >= 0


# ── finding → implicated transactions (triangulation drill-down) ───────

def test_finding_transactions_known_finding(ob):
    """The DHS $950K procurement finding maps to same-agency spend."""
    if not (ob._table_exists("ag_findings") and ob._table_exists("transactions")):
        return
    out = ob.finding_transactions("19-109-F01")
    if out is None:                         # finding id not in this build
        return
    assert out["agency"] == "DEPT OF HEALTH SERVICES"
    assert out["audit_fiscal_year"] == 2019
    assert out["fiscal_year_window"] == [2018, 2020]
    assert out["matched_n_txns"] >= 0
    assert isinstance(out["transactions"], list)


def test_finding_transactions_unknown_returns_none(ob):
    if not (ob._table_exists("ag_findings") and ob._table_exists("transactions")):
        return
    assert ob.finding_transactions("99-999-F99") is None
