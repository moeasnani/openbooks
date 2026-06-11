"""One-time schema bootstrap — explicitly invoked, never run on import.

The original ``query.py`` created tables/views inside ``OpenBooks.__init__``,
which meant every instantiation was a write operation. That blocks
concurrent readers (DuckDB allows only one writable handle) and is a
surprising side effect when embedding the class in another application.

Now: :class:`openbooks.queries.OpenBooks` opens read-only by default and
``bootstrap(backend)`` is called once per database — by an operator, a
deploy script, or ``python -m openbooks.bootstrap``.

Postgres note
-------------
The DDL below is ANSI and runs unchanged on Postgres, with one exception:
seeding uses ``INSERT … ON CONFLICT DO NOTHING`` which both engines
support (DuckDB ≥ 0.7, Postgres ≥ 9.5).
"""

from __future__ import annotations

from openbooks._sql import TX_WITH_KEY_CTE
from openbooks.db import Backend

VENDOR_VERDICTS_DDL = """
    CREATE TABLE IF NOT EXISTS vendor_verdicts (
        entity_key VARCHAR PRIMARY KEY,
        verdict VARCHAR,
        overtaker_interest INTEGER,
        public_context VARCHAR,
        recommended_action VARCHAR,
        reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""

# Seed verdicts from the tier_entities build columns, first run only.
VENDOR_VERDICTS_SEED = """
    INSERT INTO vendor_verdicts (entity_key, verdict, overtaker_interest, public_context)
    SELECT entity_key, verify_verdict, overtaker_interest, public_context
    FROM tier_entities
    WHERE verify_verdict IS NOT NULL
    ON CONFLICT (entity_key) DO NOTHING
"""

# Read-only view: transactions + computed entity_key + verdict overlay.
TX_WITH_VERDICT_VIEW = f"""
    CREATE OR REPLACE VIEW tx_with_verdict AS
    WITH t AS ({TX_WITH_KEY_CTE})
    SELECT t.*,
           coalesce(v.verdict, 'screened_unreviewed') AS verify_status,
           v.overtaker_interest,
           v.public_context
    FROM t
    LEFT JOIN vendor_verdicts v USING (entity_key)
"""


def bootstrap(backend: Backend) -> None:
    """Create the verdicts table (seeding it on first run) and views.

    Idempotent: safe to re-run on an already-bootstrapped database.
    Requires a writable connection.
    """
    backend.execute(VENDOR_VERDICTS_DDL)
    count = backend.query("SELECT count(*) AS n FROM vendor_verdicts")[0]["n"]
    if count == 0:
        backend.execute(VENDOR_VERDICTS_SEED)
    backend.execute(TX_WITH_VERDICT_VIEW)


def main() -> None:  # pragma: no cover - thin CLI wrapper
    import argparse

    from openbooks.db import DuckDBBackend, PostgresBackend

    parser = argparse.ArgumentParser(description="Bootstrap an OpenBooks database.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--duckdb", metavar="PATH", help="path to warehouse.duckdb")
    target.add_argument("--postgres", metavar="DSN", help="postgresql:// connection string")
    args = parser.parse_args()

    backend: Backend
    if args.duckdb:
        backend = DuckDBBackend(args.duckdb, read_only=False)
    else:
        backend = PostgresBackend(args.postgres)
    try:
        bootstrap(backend)
        print("bootstrap complete")
    finally:
        backend.close()


if __name__ == "__main__":  # pragma: no cover
    main()
