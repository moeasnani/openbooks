"""Command-line interface: ``python -m openbooks <command> [args]``.

Mirrors the old ``python query.py <command>`` testing interface, with
explicit flags and JSON output.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from openbooks.queries import OpenBooks

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="openbooks", description="Query an OpenBooks warehouse from the command line."
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("OPENBOOKS_DB", os.path.join(_REPO_ROOT, "warehouse.duckdb")),
        help="path to warehouse.duckdb",
    )
    parser.add_argument(
        "--postgres",
        default=os.environ.get("OPENBOOKS_PG_DSN"),
        help="postgresql:// DSN (overrides --db)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("entity", help="vendor summary + transactions")
    p.add_argument("name")

    p = sub.add_parser("leads", help="the action queue")
    p.add_argument("--tier", type=int, default=1)
    p.add_argument("--status", default=None)
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("agency", help="agency scorecard + trend")
    p.add_argument("name")

    p = sub.add_parser("explain", help="why this tier")
    p.add_argument("transaction_id")

    p = sub.add_parser("search", help="fuzzy search")
    p.add_argument("query")

    sub.add_parser("waterfall", help="tier distribution")

    p = sub.add_parser("rank-agencies", help="agency leaderboard by metric")
    p.add_argument("--metric", default="usd_tier1")
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("rank-vendors", help="vendor leaderboard by metric")
    p.add_argument("--metric", default="usd_tier1")
    p.add_argument("--agency", default=None)
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("rank-programs", help="program leaderboard by metric")
    p.add_argument("--metric", default="tier12_exposure")
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("spend", help="total spend from the COMPLETE ledger (all txn sizes)")
    p.add_argument("--agency", default=None)
    p.add_argument("--fiscal-year", type=int, default=None)
    p.add_argument("--category", default=None, help="keyword, e.g. 'information technology'")
    p.add_argument("--type", dest="transaction_type", default="EX",
                   help="EX (spend, default), RV (revenue), or ALL")
    p.add_argument("--breakdown", default="category",
                   choices=["category", "year", "vendor", "none"])
    p.add_argument("--limit", type=int, default=25)

    p = sub.add_parser("pending", help="reviewer queue")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("search-findings", help="search AG audit findings by keyword")
    p.add_argument("text")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("rank-ag-findings", help="agency leaderboard by AG audit metrics")
    p.add_argument("--metric", default="total_questioned_cost")
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("ask", help="natural-language question (via LLM tool-calling)")
    p.add_argument("question")

    p = sub.add_parser("unattributed", help="spend that cannot be traced to a named payee")
    p.add_argument("agency", nargs="?", default=None, help="optional agency filter")
    p.add_argument("--fy", type=int, default=None, help="restrict to a fiscal year")
    p.add_argument("--limit", type=int, default=25)

    p = sub.add_parser("finding-transactions",
                       help="checkbook transactions implicated by an AG finding")
    p.add_argument("finding_id", help="e.g. 19-109-F01")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--window", type=int, default=1, help="fiscal-year window (+/-)")

    args = parser.parse_args(argv)

    if args.postgres:
        ob = OpenBooks.from_postgres(args.postgres)
    else:
        ob = OpenBooks(args.db)

    try:
        if args.command == "entity":
            result = ob.entity(args.name)
        elif args.command == "leads":
            result = ob.leads(tier=args.tier, status=args.status, limit=args.limit)
        elif args.command == "agency":
            result = ob.agency_card(args.name)
        elif args.command == "explain":
            result = ob.explain(args.transaction_id)
        elif args.command == "search":
            result = ob.search(args.query)
        elif args.command == "waterfall":
            result = ob.waterfall()
        elif args.command == "rank-agencies":
            result = ob.rank_agencies(metric=args.metric, limit=args.limit)
        elif args.command == "rank-vendors":
            result = ob.rank_vendors(metric=args.metric, agency=args.agency, limit=args.limit)
        elif args.command == "rank-programs":
            result = ob.rank_programs(metric=args.metric, limit=args.limit)
        elif args.command == "spend":
            result = ob.spend(
                agency=args.agency,
                fiscal_year=args.fiscal_year,
                category=args.category,
                transaction_type=args.transaction_type,
                breakdown=args.breakdown,
                limit=args.limit,
            )
        elif args.command == "ask":
            from openbooks.ask import ask as _ask

            result = _ask(ob, args.question)
        elif args.command == "search-findings":
            result = ob.search_findings(args.text, limit=args.limit)
        elif args.command == "rank-ag-findings":
            result = ob.rank_ag_findings(metric=args.metric, limit=args.limit)
        elif args.command == "unattributed":
            result = ob.unattributed_spend(
                args.agency, fiscal_year=args.fy, limit=args.limit,
            )
        elif args.command == "finding-transactions":
            result = ob.finding_transactions(
                args.finding_id, limit=args.limit, window_years=args.window,
            )
        else:  # pending
            result = ob.verdicts_pending(limit=args.limit)
    finally:
        ob.close()

    json.dump(result, sys.stdout, indent=2, default=str)
    print()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
