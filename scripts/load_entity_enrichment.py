#!/usr/bin/env python3
"""Materialize Grok enrichment JSON into queryable warehouse tables.

Loads two committed enrichment JSONs (the determinism-contract source of
truth) into DuckDB tables so they are queryable and reach Postgres via the
push script (warehouse tables are what get pushed; a JSON-only overlay is
invisible in production):

  entity_enrichment.json       -> entity_grok_context     (166 flagged vendors)
  unattributed_enrichment.json -> unattributed_context    (untraceable-spend
                                                            statutory context)

Idempotent: drops and rebuilds each table from its JSON every run. Safe to
re-run after any enrichment refresh. List fields (citations, markers,
agencies, top_categories) are stored as JSON-string columns for backend
portability (DuckDB + Postgres both round-trip them).

Usage:
    .venv/bin/python scripts/load_entity_enrichment.py    # both, warehouse.duckdb
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


UNATTR_DDL = """
CREATE TABLE unattributed_context (
    agency           VARCHAR PRIMARY KEY,
    unattributed_usd DOUBLE,
    unattributed_pct DOUBLE,
    classification   VARCHAR,
    confidence       VARCHAR,
    reason           VARCHAR,
    statutory_basis  VARCHAR,
    notes            VARCHAR,
    top_categories   VARCHAR,   -- JSON array string
    model            VARCHAR,
    provider         VARCHAR,
    generated_at     VARCHAR
)
"""

UNATTR_INSERT = """
INSERT INTO unattributed_context VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def load_unattributed(db_path: str, json_path: str) -> int:
    with open(json_path) as f:
        doc = json.load(f)
    meta = doc.get("_meta", {})
    agencies = doc.get("agencies", {})
    model = meta.get("model")
    provider = meta.get("provider")
    generated_at = meta.get("generated_at")

    con = duckdb.connect(db_path)  # writable
    try:
        con.execute("DROP TABLE IF EXISTS unattributed_context")
        con.execute(UNATTR_DDL)
        rows = 0
        for agency, v in agencies.items():
            con.execute(
                UNATTR_INSERT,
                [
                    agency,
                    v.get("unattributed_usd"),
                    v.get("unattributed_pct"),
                    v.get("classification"),
                    v.get("confidence"),
                    v.get("reason"),
                    v.get("statutory_basis"),
                    v.get("notes"),
                    json.dumps(v.get("top_categories") or []),
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
    ap.add_argument(
        "--unattributed-json",
        default=os.path.join(ROOT, "unattributed_enrichment.json"),
        help="untraceable-spend statutory-context JSON -> unattributed_context",
    )
    args = ap.parse_args()
    if not os.path.exists(args.json):
        print(f"error: {args.json} not found", file=sys.stderr)
        return 1
    n = load(args.db, args.json)
    print(f"loaded {n} rows into entity_grok_context ({args.db})")
    if os.path.exists(args.unattributed_json):
        m = load_unattributed(args.db, args.unattributed_json)
        print(f"loaded {m} rows into unattributed_context ({args.db})")
    else:
        print(f"skip: {args.unattributed_json} not found (unattributed_context unchanged)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
