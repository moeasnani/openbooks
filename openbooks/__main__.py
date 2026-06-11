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

    p = sub.add_parser("pending", help="reviewer queue")
    p.add_argument("--limit", type=int, default=20)

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
        else:  # pending
            result = ob.verdicts_pending(limit=args.limit)
    finally:
        ob.close()

    json.dump(result, sys.stdout, indent=2, default=str)
    print()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
