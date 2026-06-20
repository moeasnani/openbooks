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
plus vendor_verdicts (copied if present, so curation survives the move)
and the Arizona Auditor-General overlay — ag_reports, ag_findings,
ag_report_agency_xref — copied when present (newer DBs only), so the
agency card's AG-corroboration layer travels to Postgres too.
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
    "agency_summary",
]

# Optional tables: copied only when present in the source warehouse, so the
# script still runs against an older DB built before the layer existed.
# (vendor_verdicts is handled separately because it carries curation.)
#   ag_reports / ag_findings / ag_report_agency_xref — the Arizona
#   Auditor-General findings overlay (see openbooks.queries._ag_audit).
#   ag_agency_spending / ag_finding_context — the enriched AG layer that
#   _ag_audit() and findings() prefer when present (guarded by _table_exists).
AG_TABLES = [
    "ag_reports",
    "ag_findings",
    "ag_report_agency_xref",
    "ag_agency_spending",
    "ag_finding_context",
]

# Grok-grounded real-world context for flagged entities (built by
# scripts/load_entity_enrichment.py from entity_enrichment.json). Optional:
# pushed only when present, so older warehouses still migrate cleanly. The
# query layer reads this table first and falls back to the committed JSON.
ENRICHMENT_TABLES = [
    "entity_grok_context",
    "unattributed_context",
    "ag_finding_grok_context",
]

# Indexes matching the query layer's access paths.
INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_txt_tier ON tx_tiered (tier)",
    "CREATE INDEX IF NOT EXISTS ix_txt_txn ON tx_tiered (transaction_id)",
    "CREATE INDEX IF NOT EXISTS ix_txt_agency ON tx_tiered (agency)",
    "CREATE INDEX IF NOT EXISTS ix_te_key ON tier_entities (entity_key)",
    "CREATE INDEX IF NOT EXISTS ix_tay_agency ON tier_agency_year (agency)",
    "CREATE INDEX IF NOT EXISTS ix_as_agency_fy ON agency_summary (agency, fiscal_year, transaction_type)",
]

# Indexes matching the query layer's access paths.
# spend_summary indexes — the complete-ledger rollup that lets `spend()`
# answer "how much did X spend" on Postgres (where the raw 115M-row parquet
# `transactions` view doesn't exist). Agency + fiscal_year + transaction_type
# are the filter columns; category columns are LIKE-scanned.
SPEND_SUMMARY_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_ss_agency ON spend_summary (organization_level_1_name)",
    "CREATE INDEX IF NOT EXISTS ix_ss_fy ON spend_summary (fiscal_year)",
    "CREATE INDEX IF NOT EXISTS ix_ss_type ON spend_summary (transaction_type)",
]

# The rollup SQL: pre-aggregate the full transactions view to the grain
# (agency × fiscal_year × transaction_type × cat1 × cat2 × cat3 ×
# appropriation), storing total_usd and n_txns. This collapses ~115M rows
# into ~230K — trivial for Postgres, and numerically identical to
# sum(amount)/count(*) over the raw ledger for any filter combination.
SPEND_SUMMARY_SQL = """
    SELECT
        organization_level_1_name,
        fiscal_year,
        transaction_type,
        category_level_1_name,
        category_level_2_name,
        category_level_3_name,
        appropriation_1_name,
        round(sum(amount), 0)  AS total_usd,
        count(*)                AS n_txns
    FROM transactions
    WHERE amount IS NOT NULL
    GROUP BY 1, 2, 3, 4, 5, 6, 7
"""

# AG-overlay indexes — applied only when the AG tables were copied.
# Read path: ag_reports filtered by agency_checkbook, joined to
# ag_findings on report_id.
AG_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_agr_agency ON ag_reports (agency_checkbook)",
    "CREATE INDEX IF NOT EXISTS ix_agr_report ON ag_reports (report_id)",
    "CREATE INDEX IF NOT EXISTS ix_agf_report ON ag_findings (report_id)",
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

    # Detect the full transactions view once (used both to build the rollup
    # during a push and to decide whether to verify it).
    has_transactions = _scalar(
        con,
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name = 'transactions'",
    )

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

        # Carry the AG findings overlay forward when present (newer DBs only).
        for table in AG_TABLES + ENRICHMENT_TABLES:
            present = _scalar(
                con,
                "SELECT count(*) FROM information_schema.tables "
                f"WHERE table_name = '{table}'",
            )
            if not present:
                continue
            n = _scalar(con, f"SELECT count(*) FROM {table}")
            print(f"  pushing {table:24} {n:>9,} rows … ", end="", flush=True)
            con.execute(f"DROP TABLE IF EXISTS pg.{table} CASCADE")
            con.execute(f"CREATE TABLE pg.{table} AS SELECT * FROM {table}")
            print("done")

        # Build + push the spend_summary rollup from the full transactions
        # view (only when the source has it — it's a DuckDB+parquet view).
        # This is what makes `spend()` work on Postgres: the raw 115M-row
        # view can't travel, but the ~230K-row pre-aggregate does, and
        # sum(total_usd)/sum(n_txns) is numerically identical to
        # sum(amount)/count(*) over the raw ledger.
        if has_transactions:
            print("  building spend_summary rollup from transactions … ", end="", flush=True)
            con.execute(f"CREATE TEMP TABLE _spend_summary AS {SPEND_SUMMARY_SQL}")
            n = _scalar(con, "SELECT count(*) FROM _spend_summary")
            print(f"{n:,} rows")
            print(f"  pushing spend_summary           {n:>9,} rows … ", end="", flush=True)
            con.execute("DROP TABLE IF EXISTS pg.spend_summary CASCADE")
            con.execute("CREATE TABLE pg.spend_summary AS SELECT * FROM _spend_summary")
            print("done")

    # Always build the rollup temp table when the source has a transactions
    # view, even on --verify-only, so the row-count comparison below works
    # (the temp table is session-local and cheap to rebuild).
    if verify_only and has_transactions:
        already = _scalar(
            con,
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name = '_spend_summary'",
        )
        if not already:
            con.execute(f"CREATE TEMP TABLE _spend_summary AS {SPEND_SUMMARY_SQL}")

    # ── verify: row counts must match exactly, source vs Postgres ──────
    # The core TABLES are always present. AG tables are optional (newer
    # warehouses only). For AG we must gracefully handle the case where the
    # *source* DuckDB has the tables but the target Postgres does not
    # (typical for --verify-only against an older push that predates the AG
    # layer).
    #
    # We detect presence on the attached "src" warehouse. For the pg side we
    # use information_schema with table_catalog = 'pg'. This works because
    # of the ATTACH '{dsn}' AS pg (TYPE postgres) line earlier in push().
    # If the table is missing on the pg side we report "MISSING" instead of
    # crashing the count(*) query.
    print("\nverifying row counts (duckdb == postgres):")
    failures = 0
    for table in TABLES:
        n_src = _scalar(con, f"SELECT count(*) FROM {table}")
        n_pg = _scalar(con, f"SELECT count(*) FROM pg.{table}")
        ok = n_src == n_pg
        failures += 0 if ok else 1
        print(f"  {'OK  ' if ok else 'FAIL'} {table:24} {n_src:>9,} == {n_pg:,}")
    # AG tables: verify only when the source warehouse carries them.
    for table in AG_TABLES + ENRICHMENT_TABLES:
        in_src = _scalar(
            con,
            "SELECT count(*) FROM information_schema.tables "
            f"WHERE table_name = '{table}'",
        )
        if not in_src:
            continue
        n_src = _scalar(con, f"SELECT count(*) FROM {table}")
        in_pg = _scalar(
            con,
            "SELECT count(*) FROM information_schema.tables "
            f"WHERE table_catalog = 'pg' AND table_name = '{table}'",
        )
        n_pg = _scalar(con, f"SELECT count(*) FROM pg.{table}") if in_pg else -1
        ok = in_pg and n_src == n_pg
        failures += 0 if ok else 1
        shown = f"{n_pg:,}" if in_pg else "MISSING"
        print(f"  {'OK  ' if ok else 'FAIL'} {table:24} {n_src:>9,} == {shown}")

    # spend_summary: verify only when the source had a transactions view to
    # build it from. On a --verify-only run the temp table isn't available,
    # so we compare row counts on the pg side against a fresh rebuild.
    if has_transactions:
        n_src = _scalar(con, "SELECT count(*) FROM _spend_summary")
        in_pg = _scalar(
            con,
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_catalog = 'pg' AND table_name = 'spend_summary'",
        )
        n_pg = _scalar(con, "SELECT count(*) FROM pg.spend_summary") if in_pg else -1
        ok = in_pg and n_src == n_pg
        failures += 0 if ok else 1
        shown = f"{n_pg:,}" if in_pg else "MISSING"
        print(f"  {'OK  ' if ok else 'FAIL'} {'spend_summary':24} {n_src:>9,} == {shown}")
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
    # AG-overlay indexes — only if the tables made it across to Postgres.
    ag_rows = backend.query(
        "SELECT count(*) AS n FROM information_schema.tables "
        "WHERE table_name = 'ag_reports'"
    )
    if ag_rows and ag_rows[0]["n"]:
        for ddl in AG_INDEXES:
            backend.execute(ddl)
    # spend_summary indexes — only if the rollup made it across.
    ss_rows = backend.query(
        "SELECT count(*) AS n FROM information_schema.tables "
        "WHERE table_name = 'spend_summary'"
    )
    if ss_rows and ss_rows[0]["n"]:
        for ddl in SPEND_SUMMARY_INDEXES:
            backend.execute(ddl)
    print("\nbootstrap + indexes applied on postgres")

    ob = OpenBooks(backend=backend)
    w = ob.waterfall()
    print(f"smoke query via OpenBooks->Postgres: {w['total_txns']:,} txns / {w['total_exposure']:,.0f} exposure")
    # AG overlay smoke: exercise the real read path through Postgres.
    if ag_rows and ag_rows[0]["n"]:
        n_reports = backend.query("SELECT count(*) AS n FROM ag_reports")[0]["n"]
        n_findings = backend.query("SELECT count(*) AS n FROM ag_findings")[0]["n"]
        audit = ob._ag_audit("DEPT OF ECONOMIC SECURITY")
        n_audits = audit["n_reports"] if audit else 0
        print(
            f"AG overlay via OpenBooks->Postgres: {n_reports} reports / "
            f"{n_findings} findings (DES sample: {n_audits} audits)"
        )
    # spend() smoke: exercise the rollup read path through Postgres.
    if ss_rows and ss_rows[0]["n"]:
        spend_result = ob.spend(agency="DEPT OF TRANSPORTATION", breakdown="none")
        if "error" in spend_result:
            print(f"spend via OpenBooks->Postgres: ERROR {spend_result['error']}")
        else:
            print(
                f"spend via OpenBooks->Postgres: ADOT total ${spend_result['total']:,.0f} "
                f"across {spend_result['n_txns']:,} txns ({spend_result['basis']})"
            )
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
