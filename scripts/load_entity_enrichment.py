#!/usr/bin/env python3
"""Materialize entity_enrichment.json into the warehouse `entity_grok_context` table.

The committed JSON (scripts/enrich_entities.py output) is the source of truth
under the determinism contract. This script loads it into a queryable DuckDB
table so that:

  1. The query layer can JOIN it (and Postgres deployments can serve it),
     instead of every process re-reading a 200KB+ JSON file.
  2. `scripts/push_to_postgres.py` carries the verified context to production
     (warehouse tables are what get pushed; a JSON-only overlay would be
     invisible there).

Idempotent: drops and rebuilds the table from the JSON every run. Safe to
re-run after any enrichment refresh. Citations (a list) are stored as a JSON
string column for backend portability (DuckDB + Postgres both round-trip it).

Usage:
    .venv/bin/python scripts/load_entity_enrichment.py                 # warehouse.duckdb
    .venv/bin/python scripts/load_entity_enrichment.py --db path.duckdb --json file.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DDL = """
CREATE TABLE entity_grok_context (
    entity_key       VARCHAR PRIMARY KEY,
    entity_name      VARCHAR,
    verdict          VARCHAR,
    confidence       VARCHAR,
    identity         VARCHAR,
    arizona_role     VARCHAR,
    reason           VARCHAR,
    notes            VARCHAR,
    flagged_exposure DOUBLE,
    top_tier         INTEGER,
    markers          VARCHAR,   -- JSON array string
    agencies         VARCHAR,   -- JSON array string
    citations        VARCHAR,   -- JSON array string
    n_citations      INTEGER,
    web_calls        INTEGER,
    x_calls          INTEGER,
    model            VARCHAR,
    provider         VARCHAR,
    generated_at     VARCHAR
)
"""

INSERT = """
INSERT INTO entity_grok_context VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
"""


def _arr(v) -> str:
    return json.dumps(v if isinstance(v, list) else ([] if v is None else [v]))


def load(db_path: str, json_path: str) -> int:
    with open(json_path) as f:
        doc = json.load(f)
    meta = doc.get("_meta", {})
    entities = doc.get("entities", {})
    model = meta.get("model")
    provider = meta.get("provider")
    generated_at = meta.get("generated_at")

    con = duckdb.connect(db_path)  # writable
    try:
        con.execute("DROP TABLE IF EXISTS entity_grok_context")
        con.execute(DDL)
        rows = 0
        for key, v in entities.items():
            cites = v.get("citations") or []
            con.execute(
                INSERT,
                [
                    key,
                    v.get("entity_name"),
                    v.get("verdict"),
                    v.get("confidence"),
                    v.get("identity"),
                    v.get("arizona_role"),
                    v.get("reason"),
                    v.get("notes"),
                    v.get("flagged_exposure"),
                    v.get("top_tier"),
                    _arr(v.get("markers")),
                    _arr(v.get("agencies")),
                    json.dumps(cites),
                    len(cites),
                    v.get("_web_calls"),
                    v.get("_x_calls"),
                    model,
                    provider,
                    generated_at,
                ],
            )
            rows += 1
        con.commit()
        return rows
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "warehouse.duckdb"))
    ap.add_argument("--json", default=os.path.join(ROOT, "entity_enrichment.json"))
    args = ap.parse_args()
    if not os.path.exists(args.json):
        print(f"error: {args.json} not found", file=sys.stderr)
        return 1
    n = load(args.db, args.json)
    print(f"loaded {n} rows into entity_grok_context ({args.db})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
