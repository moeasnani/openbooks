#!/usr/bin/env python3
"""
Match questioned amounts from Auditor General reports against OpenBooks data.

Parses agency/questioned_amounts.md, extracts agencies + dollar figures,
then queries the OpenBooks warehouse (DuckDB by default) for matching entities,
spending patterns, and potential leads.

Usage:
    python3 match_to_openbooks.py
    python3 match_to_openbooks.py --db /path/to/warehouse.duckdb
"""

import re
import sys
from pathlib import Path
from openbooks import OpenBooks

AGENCY_DIR = Path(__file__).parent
MD_FILE = AGENCY_DIR / "questioned_amounts.md"
DEFAULT_DB = Path.cwd().parent / "warehouse.duckdb"   # adjust if needed

def parse_questioned_amounts(md_path: Path):
    """Very lightweight parser for the generated markdown."""
    if not md_path.exists():
        return []

    entries = []
    current_report = None
    for line in md_path.read_text().splitlines():
        if line.startswith("## "):
            current_report = line[3:].strip()
        elif line.startswith("- ") and current_report:
            # crude extraction of dollar amounts + context
            m = re.search(r"\$?([\d,]+(?:\.\d+)?)\s*(million|thousand)?", line, re.I)
            if m:
                entries.append({
                    "report": current_report,
                    "amount_raw": m.group(0),
                    "context": line[2:].strip()[:200]
                })
    return entries

def main():
    db_path = Path(sys.argv[sys.argv.index("--db") + 1]) if "--db" in sys.argv else DEFAULT_DB
    print(f"Loading OpenBooks from {db_path}")
    ob = OpenBooks(str(db_path))

    entries = parse_questioned_amounts(MD_FILE)
    print(f"Found {len(entries)} questioned-amount mentions across reports")

    matches = []
    for e in entries[:20]:  # start small
        # Try to extract an agency name from the report id or context
        agency_guess = e["report"].split()[0] if e["report"] else ""
        if not agency_guess:
            continue

        try:
            results = ob.search(agency_guess)
            if results:
                matches.append({
                    "report": e["report"],
                    "amount": e["amount_raw"],
                    "context": e["context"],
                    "openbooks_hits": len(results) if hasattr(results, "__len__") else "yes"
                })
        except Exception as ex:
            print(f"  search failed for {agency_guess}: {ex}")

    print(f"\n=== Potential Matches ({len(matches)}) ===")
    for m in matches:
        print(f"- {m['report']}: {m['amount']} → {m['openbooks_hits']} OpenBooks hits")
        print(f"  Context: {m['context'][:120]}...")

    if not matches:
        print("No strong matches yet (need more PDFs or better entity extraction).")

if __name__ == "__main__":
    main()