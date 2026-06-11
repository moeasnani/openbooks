"""Golden-number tests — the verified figures from INTEGRATION_BRIEF.md.

If a pipeline rebuild or a migration (e.g. to Postgres) changes any of
these, something structural moved and a human should look.
"""

from __future__ import annotations


def test_total_tiered_transactions(ob):
    n = ob._query("SELECT count(*) AS n FROM tx_tiered")[0]["n"]
    assert n == 306_604


def test_tier_distribution(ob):
    """Tier counts + exposures, INTEGRATION_BRIEF §4 (verified rollup)."""
    rows = {
        int(r["tier"]): r
        for r in ob._query(
            "SELECT tier, count(*) AS n, round(sum(amount), 0) AS usd "
            "FROM tx_tiered GROUP BY tier"
        )
    }
    assert rows[1]["n"] == 1_094
    assert rows[1]["usd"] == 5_053_439_234
    assert rows[4]["n"] == 40_918
    assert rows[4]["usd"] == 304_264_118_883
    assert rows[5]["n"] == 227_032


def test_entity_universe(ob):
    n = ob._query("SELECT count(*) AS n FROM tier_entities")[0]["n"]
    assert n == 1_700


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
