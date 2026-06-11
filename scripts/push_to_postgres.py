#!/usr/bin/env python3
"""Push the OpenBooks read-path tables from DuckDB to Postgres.

One command, idempotent (drop-and-recreate per table inside the attach):

    python scripts/push_to_postgres.py --pg "$OPENBOOKS_PG_DSN" \
        [--db warehouse.duckdb] [--verify-only]

Uses DuckDB's postgres extension: each table is copied directly over the
wire with types mapped automatically (VARCHAR[] -> text[], DECIMAL ->
numeric, etc.). After copying it bootstraps the verdicts table + view and
verifies golden row counts on the Postgres side.

The five read-path tables (everything openbooks.queries touches):
    tx_tiered, tier_entities, tier_agency_summary,
    tier_agency_year, tier_program_summary
plus vendor_verdicts (copied if present, so curation survives the move).
"""

from __future__ import annotations

import argparse
import os
import sys

TABLES = [
    "tx_tiered",
    "tier_entities",
    "tier_agency_summary",
    "tier_agency_year",
    "tier_program_summary",
]

# Indexes matching the query layer's access paths.
INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_txt_tier ON tx_tiered (tier)",
    "CREATE INDEX IF NOT EXISTS ix_txt_txn ON tx_tiered (transaction_id)",
    "CREATE INDEX IF NOT EXISTS ix_txt_agency ON tx_tiered (agency)",
    "CREATE INDEX IF NOT EXISTS ix_te_key ON tier_entities (entity_key)",
    "CREATE INDEX IF NOT EXISTS ix_tay_agency ON tier_agency_year (agency)",
]


def _scalar(con, sql: str) -> int:
    """First column of the first row; queries here always return one."""
    row = con.execute(sql).fetchone()
    assert row is not None, f"query returned no rows: {sql}"
    return row[0]


def push(db_path: str, dsn: str, verify_only: bool) -> int:
    import duckdb

    # In-memory session: attach the warehouse read-only and Postgres
    # writable. (A read-only primary connection would force every
    # attached database read-only as well — including pg.)
    con = duckdb.connect()
    con.execute("INSTALL postgres; LOAD postgres;")
    con.execute(f"ATTACH '{db_path}' AS src (READ_ONLY)")
    con.execute(f"ATTACH '{dsn}' AS pg (TYPE postgres)")
    con.execute("USE src")

    if not verify_only:
        for table in TABLES:
            n_src = _scalar(con, f"SELECT count(*) FROM {table}")
            print(f"  pushing {table:24} {n_src:>9,} rows … ", end="", flush=True)
            con.execute(f"DROP TABLE IF EXISTS pg.{table} CASCADE")
            con.execute(f"CREATE TABLE pg.{table} AS SELECT * FROM {table}")
            print("done")

        # Carry curation forward if the source DB has it.
        has_verdicts = _scalar(
            con,
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name = 'vendor_verdicts'",
        )
        if has_verdicts:
            n = _scalar(con, "SELECT count(*) FROM vendor_verdicts")
            print(f"  pushing vendor_verdicts        {n:>9,} rows … ", end="", flush=True)
            con.execute("DROP TABLE IF EXISTS pg.vendor_verdicts CASCADE")
            con.execute("CREATE TABLE pg.vendor_verdicts AS SELECT * FROM vendor_verdicts")
            print("done")

    # ── verify: row counts must match exactly, source vs Postgres ──────
    print("\nverifying row counts (duckdb == postgres):")
    failures = 0
    for table in TABLES:
        n_src = _scalar(con, f"SELECT count(*) FROM {table}")
        n_pg = _scalar(con, f"SELECT count(*) FROM pg.{table}")
        ok = n_src == n_pg
        failures += 0 if ok else 1
        print(f"  {'OK  ' if ok else 'FAIL'} {table:24} {n_src:>9,} == {n_pg:,}")
    con.close()

    if failures:
        print(f"\n{failures} table(s) mismatched — do not point the app at this database.")
        return 1

    # ── finish on the Postgres side: bootstrap + indexes + smoke query ─
    from openbooks.bootstrap import bootstrap
    from openbooks.db import PostgresBackend
    from openbooks.queries import OpenBooks

    backend = PostgresBackend(dsn)
    bootstrap(backend)
    for ddl in INDEXES:
        backend.execute(ddl)
    print("\nbootstrap + indexes applied on postgres")

    ob = OpenBooks(backend=backend)
    w = ob.waterfall()
    print(f"smoke query via OpenBooks->Postgres: {w['total_txns']:,} txns / {w['total_exposure']:,.0f} exposure")
    ob.close()
    print("\npush complete — point the server at it with:")
    print("  openbooks-server --postgres <dsn>")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Push the OpenBooks read-path tables from DuckDB to Postgres."
    )
    parser.add_argument("--db", default=os.environ.get("OPENBOOKS_DB", "warehouse.duckdb"))
    parser.add_argument("--pg", default=os.environ.get("OPENBOOKS_PG_DSN"), required=False)
    parser.add_argument("--verify-only", action="store_true",
                        help="skip the copy; only compare row counts")
    args = parser.parse_args()
    if not args.pg:
        parser.error("provide --pg DSN or set OPENBOOKS_PG_DSN")
    if not os.path.exists(args.db):
        parser.error(f"duckdb file not found: {args.db}")
    return push(args.db, args.pg, args.verify_only)


if __name__ == "__main__":
    sys.exit(main())
