#!/usr/bin/env python3
"""
Extract compact candidate snippets for the targeted Grok enrichment pass.

Produces ag_build/grok_candidates.json — a small, reviewable payload containing
ONLY the residual cases that need LLM judgment, so we never load full 144K-char
reports into context:

  A) finding_gaps   : reports flagged needs_grok=1 — title-page + section
                      headers + first lines of any 'Finding'/'Chapter' blocks,
                      so Grok can confirm clean-vs-missed and recover boundaries.
  B) cost_snippets  : every sentence across all reports containing a $ figure
                      near cost/fund/question/improper keywords — candidates for
                      questioned_cost_usd (deterministic harvest, Grok adjudicates).
  C) agency_residual: the 1 unparsed agency (23-109) + unmatched/low-confidence
                      xref rows — title-page lines for Grok to name + bridge.

The Grok output is cached to ag_build/grok_enrichment.json (committed artifact)
so warehouse refreshes stay deterministic (INTEGRATION_BRIEF §7).
"""

import csv
import json
import re
from pathlib import Path

AGENCY_DIR = Path(__file__).parent
EXTRACTED = AGENCY_DIR / "extracted"
OUT = AGENCY_DIR / "ag_build"

DOLLAR_RE = re.compile(
    r"[^.]*?\$[\d,]+(?:\.\d+)?\s*(?:million|billion|thousand)?[^.]*\.")
COST_KEYWORDS = re.compile(
    r"\b(questioned|unsupported|improper|unallowable|unreconciled|"
    r"misspent|recover|disallow|overpa|fraud|unaccounted|"
    r"lacked? (?:support|documentation)|without (?:support|documentation))\b",
    re.IGNORECASE)


def load_findings_needing_grok():
    rows = list(csv.DictReader(open(OUT / "ag_findings.csv")))
    return sorted({r["report_id"] for r in rows if r["needs_grok"] == "1"})


def load_xref_residual():
    rows = list(csv.DictReader(open(OUT / "ag_report_agency_xref.csv")))
    out = []
    for r in rows:
        score = float(r["match_score"] or 0)
        if not r["checkbook_agency"] or score < 0.5:
            out.append({
                "agency_raw": r["agency_raw"],
                "agency_key": r["agency_key"],
                "auto_match": r["checkbook_agency"],
                "auto_score": score,
            })
    return out


def report_first_page(rid):
    d = json.load(open(EXTRACTED / f"{rid}_Report.json"))
    return d["pages"][0]["text"] if d["pages"] else ""


def harvest_cost_snippets(rid, full, cap=6):
    snips = []
    for m in DOLLAR_RE.finditer(full):
        s = re.sub(r"\s+", " ", m.group(0)).strip()
        if COST_KEYWORDS.search(s) and 25 < len(s) < 320:
            snips.append(s)
        if len(snips) >= cap:
            break
    # dedupe preserving order
    seen, out = set(), []
    for s in snips:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def finding_context(full, cap_chars=2200):
    """Pull section/finding headers + lead sentences for boundary recovery."""
    lines = full.split("\n")
    keep = []
    for ln in lines:
        s = ln.strip()
        if re.match(r"(?i)^(finding|chapter|sunset factor|recommendation|"
                    r"objective|conclusion|the (board|department|agency|"
                    r"commission|office) (should|did|lacks|has|failed))", s) \
                and len(s) > 8:
            keep.append(s[:200])
        if len("\n".join(keep)) > cap_chars:
            break
    return keep[:30]


def main():
    finding_gap_ids = load_findings_needing_grok()
    xref_residual = load_xref_residual()

    candidates = {"finding_gaps": [], "cost_snippets": [],
                  "agency_residual": []}

    # A) finding gaps
    for rid in finding_gap_ids:
        d = json.load(open(EXTRACTED / f"{rid}_Report.json"))
        full = "\n".join(p["text"] for p in d["pages"])
        candidates["finding_gaps"].append({
            "report_id": rid,
            "title_page": report_first_page(rid)[:900],
            "section_headers": finding_context(full),
        })

    # B) cost snippets — all reports
    for jf in sorted(EXTRACTED.glob("*_Report.json")):
        d = json.load(open(jf))
        rid = d["report_id"].replace("_Report", "")
        full = "\n".join(p["text"] for p in d["pages"])
        snips = harvest_cost_snippets(rid, full)
        if snips:
            candidates["cost_snippets"].append(
                {"report_id": rid, "snippets": snips})

    # C) agency residual
    seen_raw = set()
    for x in xref_residual:
        candidates["agency_residual"].append(x)
        seen_raw.add(x["agency_raw"])
    # add reports with empty agency_raw
    for r in csv.DictReader(open(OUT / "ag_reports.csv")):
        if not r["agency_raw"]:
            candidates["agency_residual"].append({
                "report_id": r["report_id"],
                "title_page": report_first_page(r["report_id"])[:700],
                "agency_raw": "", "auto_match": "", "auto_score": 0,
            })

    (OUT / "grok_candidates.json").write_text(
        json.dumps(candidates, indent=2, ensure_ascii=False))

    print(f"finding_gaps:    {len(candidates['finding_gaps'])} reports")
    print(f"cost_snippets:   {len(candidates['cost_snippets'])} reports "
          f"with $-near-keyword sentences")
    print(f"  total snippets: "
          f"{sum(len(c['snippets']) for c in candidates['cost_snippets'])}")
    print(f"agency_residual: {len(candidates['agency_residual'])} items")
    sz = (OUT / "grok_candidates.json").stat().st_size
    print(f"payload size:    {sz/1024:.1f} KB")


if __name__ == "__main__":
    main()
