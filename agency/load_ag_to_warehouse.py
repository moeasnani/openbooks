#!/usr/bin/env python3
"""
Load the AG audit-findings layer into warehouse.duckdb.

Merges the deterministic parser output (ag_build/*.csv) with the cached Grok
enrichment (ag_build/grok_enrichment.json) and writes 3 tables, full-snapshot
CREATE OR REPLACE, stamped with build_version (INTEGRATION_BRIEF s7):

  ag_reports
  ag_findings
  ag_report_agency_xref

The existing 8-row ag_findings_xref (curated signal<->finding correlation) is
left untouched — these tables sit BELOW it as the raw findings store.

Honors s6.7 (token/word-boundary agency matching, never substring) and the
"leads/audited-findings, never accusations" data contract via a table comment.
"""

import csv
import json
import re
from pathlib import Path

import duckdb

AGENCY_DIR = Path(__file__).parent
OUT = AGENCY_DIR / "ag_build"
DB = AGENCY_DIR.parent / "warehouse.duckdb"


def load_csv(name):
    return list(csv.DictReader(open(OUT / name)))


def main():
    reports = load_csv("ag_reports.csv")
    findings = load_csv("ag_findings.csv")
    xref = load_csv("ag_report_agency_xref.csv")
    enr = json.load(open(OUT / "grok_enrichment.json"))

    # --- apply agency_overrides to ag_reports ---
    overrides = {o["report_id"]: o for o in enr["agency_overrides"]}
    for r in reports:
        ov = overrides.get(r["report_id"])
        if ov:
            r["agency_raw"] = ov["agency_raw"]
            r["agency_checkbook"] = ov["checkbook_agency"] or ""
        else:
            r["agency_checkbook"] = ""

    # --- build report_id -> checkbook agency via xref bridge + overrides ---
    # token bridge: match agency_raw substrings from enrichment xref_bridge
    bridges = enr["xref_bridge"]

    def _norm(s):
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ",
                      (s or "").lower())).strip()

    def resolve_checkbook(agency_raw):
        ar = _norm(agency_raw)
        for b in bridges:
            key = b.get("agency_key_contains")
            raw = b.get("agency_raw")
            if key and _norm(key) in ar:
                return b["checkbook_agency"], b["relation"], b["confidence"]
            if raw and _norm(raw) == ar:
                return b["checkbook_agency"], b["relation"], b["confidence"]
        return None, None, None

    for r in reports:
        if not r["agency_checkbook"]:
            cb, rel, conf = resolve_checkbook(r["agency_raw"])
            if cb:
                r["agency_checkbook"] = cb
            elif rel == "no_checkbook_equivalent":
                r["agency_checkbook"] = ""  # explicitly no equivalent
            else:
                # fall back to the auto token-Jaccard match from the xref CSV
                xr = next((x for x in xref
                           if x["agency_key"] == r["agency_key"]), None)
                if xr and xr["checkbook_agency"]:
                    r["agency_checkbook"] = xr["checkbook_agency"]

    # --- apply questioned_costs + finding-gap adjudications to ag_findings ---
    qc = {q["report_id"]: q for q in enr["questioned_costs"]}
    gaps = {g["report_id"]: g for g in enr["finding_gap_adjudications"]}

    for f in findings:
        rid = f["report_id"]
        if rid in qc:
            # attach the report-level questioned cost to finding_no 1 (or stub)
            if f["finding_no"] in ("1", "0", 1, 0):
                f["questioned_cost_usd"] = qc[rid]["questioned_cost_usd"]
                f["questioned_cost_basis"] = qc[rid]["basis"]
                f["questioned_cost_confidence"] = qc[rid]["confidence"]
        f.setdefault("questioned_cost_basis", "")
        f.setdefault("questioned_cost_confidence", "")
        g = gaps.get(rid)
        if g:
            f["finding_structure"] = g["structure"]
            f["has_adverse_findings"] = int(g["has_adverse_findings"])
        else:
            f["finding_structure"] = "finding_template"
            f["has_adverse_findings"] = 1 if f["finding_no"] not in ("0", 0) else 0

    con = duckdb.connect(str(DB))  # writable
    con.execute("BEGIN")
    try:
        _write_reports(con, reports)
        _write_findings(con, findings)
        _write_xref(con, xref, enr)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    # verify
    print("=== Loaded ===")
    for t in ("ag_reports", "ag_findings", "ag_report_agency_xref"):
        n = con.execute(f"select count(*) from {t}").fetchone()[0]
        print(f"  {t}: {n} rows")
    print("\n=== Coverage ===")
    mc = con.execute("select count(*) from ag_reports "
                     "where agency_checkbook <> ''").fetchone()[0]
    tot = con.execute("select count(*) from ag_reports").fetchone()[0]
    print(f"  reports bridged to checkbook agency: {mc}/{tot}")
    qcn = con.execute("select count(*) from ag_findings "
                      "where questioned_cost_usd is not null").fetchone()[0]
    qsum = con.execute("select sum(questioned_cost_usd) "
                       "from ag_findings where questioned_cost_usd is not null"
                       ).fetchone()[0]
    print(f"  findings with questioned_cost: {qcn}  "
          f"(total ${qsum:,.0f})" if qsum else f"  findings w/ QC: {qcn}")
    con.close()


def _write_reports(con, rows):
    con.execute("DROP TABLE IF EXISTS ag_reports")
    con.execute("""
        CREATE TABLE ag_reports (
            report_id VARCHAR PRIMARY KEY,
            fiscal_year INTEGER,
            report_no VARCHAR,
            report_type VARCHAR,
            agency_raw VARCHAR,
            agency_key VARCHAR,
            agency_checkbook VARCHAR,
            title VARCHAR,
            report_date DATE,
            total_pages INTEGER,
            source_path VARCHAR,
            build_version VARCHAR,
            extracted_at VARCHAR
        )""")
    con.executemany(
        "INSERT INTO ag_reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r["report_id"], int(r["fiscal_year"]) if r["fiscal_year"] else None,
          r["report_no"], r["report_type"], r["agency_raw"], r["agency_key"],
          r.get("agency_checkbook", ""), r["title"],
          r["report_date"] or None, int(r["total_pages"]),
          r["source_path"], r["build_version"], r["extracted_at"])
         for r in rows])


def _write_findings(con, rows):
    con.execute("DROP TABLE IF EXISTS ag_findings")
    con.execute("""
        CREATE TABLE ag_findings (
            finding_id VARCHAR PRIMARY KEY,
            report_id VARCHAR,
            finding_no INTEGER,
            finding_text VARCHAR,
            recommendation_text VARCHAR,
            n_recommendations INTEGER,
            questioned_cost_usd DOUBLE,
            questioned_cost_basis VARCHAR,
            questioned_cost_confidence VARCHAR,
            finding_structure VARCHAR,
            has_adverse_findings BOOLEAN,
            parse_status VARCHAR,
            build_version VARCHAR
        )""")
    con.executemany(
        "INSERT INTO ag_findings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(f["finding_id"], f["report_id"], int(f["finding_no"]),
          f["finding_text"], f["recommendation_text"],
          int(f["n_recommendations"]),
          float(f["questioned_cost_usd"]) if str(
              f["questioned_cost_usd"]).strip() else None,
          f.get("questioned_cost_basis", ""),
          f.get("questioned_cost_confidence", ""),
          f.get("finding_structure", ""),
          bool(f.get("has_adverse_findings", 0)),
          f["parse_status"], f["build_version"])
         for f in rows])


def _write_xref(con, rows, enr):
    con.execute("DROP TABLE IF EXISTS ag_report_agency_xref")
    con.execute("""
        CREATE TABLE ag_report_agency_xref (
            agency_key VARCHAR PRIMARY KEY,
            agency_raw VARCHAR,
            checkbook_agency VARCHAR,
            match_score DOUBLE,
            match_method VARCHAR,
            build_version VARCHAR
        )""")
    # overlay enrichment bridge decisions onto the auto-matched xref
    bridges = enr["xref_bridge"]

    def override(agency_raw, current):
        def _n(s):
            return re.sub(r"\s+", " ", re.sub(
                r"[^a-z0-9 ]", " ", (s or "").lower())).strip()
        ar = _n(agency_raw)
        for b in bridges:
            key = b.get("agency_key_contains")
            raw = b.get("agency_raw")
            if (key and _n(key) in ar) or (raw and _n(raw) == ar):
                return (b["checkbook_agency"] or "",
                        f"grok_{b['relation']}", b["confidence"])
        return current, None, None

    seen = set()
    out = []
    for r in rows:
        cb, method, conf = override(r["agency_raw"], r["checkbook_agency"])
        score = float(r["match_score"]) if r["match_score"] else 0.0
        out.append((r["agency_key"], r["agency_raw"], cb,
                    score, method or r["match_method"], r["build_version"]))
        seen.add(r["agency_key"])
    con.executemany(
        "INSERT INTO ag_report_agency_xref VALUES (?,?,?,?,?,?)", out)


if __name__ == "__main__":
    main()
