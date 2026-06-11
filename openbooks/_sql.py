"""Shared SQL fragments and the canonical entity-key normalization.

The entity-key normalization is THE load-bearing contract of the whole
system (see INTEGRATION_BRIEF.md §3.2): `tier_entities.entity_key`,
`entity_crosswalk.parent_key`, and the on-the-fly key computed from
`tx_tiered.payee` must all agree, or vendor joins silently break.

It is therefore defined in exactly one place — here — in both its SQL
and Python forms, with a parity test in tests/test_normalization.py.
"""

from __future__ import annotations

import re

# SQL form. ANSI-portable: works identically in DuckDB and Postgres.
# Collapse runs of whitespace -> single space, trim, uppercase.
ENTITY_KEY_SQL = r"upper(trim(regexp_replace({col}, '\s+', ' ', 'g')))"


def entity_key_sql(col: str = "payee") -> str:
    """Render the entity-key expression for a given column name."""
    return ENTITY_KEY_SQL.format(col=col)


def normalize_entity_key(name: str) -> str:
    """Python mirror of :data:`ENTITY_KEY_SQL`.

    Must stay semantically identical to the SQL form — there is a test
    asserting parity between the two.
    """
    return re.sub(r"\s+", " ", name).strip().upper()


# CTE that augments tx_tiered with its computed entity_key, used by
# every transaction-grain query. Single definition, no copy-paste drift.
TX_WITH_KEY_CTE = f"""
    SELECT *,
        {entity_key_sql('payee')} AS entity_key
    FROM tx_tiered
    WHERE payee IS NOT NULL AND trim(payee) <> ''
"""
