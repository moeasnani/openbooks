"""SQL ↔ Python parity for the entity-key normalization.

This is the load-bearing join contract (INTEGRATION_BRIEF §3.2): the key
computed in SQL from `tx_tiered.payee` must equal the key computed in
Python, and both must match `tier_entities.entity_key`.
"""

from __future__ import annotations

import pytest

from openbooks._sql import entity_key_sql, normalize_entity_key

CASES = [
    "Fondomonte Arizona LLC",
    "  leading and trailing  ",
    "TABS\tAND\nNEWLINES",
    "double  spaces   collapse",
    "already UPPER",
    "punctuation, & co. (kept) - intact",
    "MIXED case With  Ünïcode",
]


def test_python_normalization_basics():
    assert normalize_entity_key("  a  b ") == "A B"
    assert normalize_entity_key("x\t\ny") == "X Y"
    assert normalize_entity_key("ALREADY DONE") == "ALREADY DONE"


@pytest.mark.parametrize("raw", CASES)
def test_sql_python_parity(raw):
    """The SQL expression and Python function must agree byte-for-byte."""
    duckdb = pytest.importorskip("duckdb")
    # Bind the value as a column; entity_key_sql() renders around it.
    got = duckdb.sql(
        f"SELECT {entity_key_sql('v')} AS k FROM (SELECT ? AS v)",
        params=[raw],
    ).fetchone()[0]
    assert got == normalize_entity_key(raw)


def test_entity_keys_in_warehouse_are_normalized(ob):
    """Every entity_key in tier_entities must be a fixed point of the
    normalization — otherwise the on-the-fly key from payee can't match."""
    rows = ob._query("SELECT entity_key FROM tier_entities LIMIT 500")
    for row in rows:
        key = row["entity_key"]
        assert key == normalize_entity_key(key), f"non-normalized key: {key!r}"
