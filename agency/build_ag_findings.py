#!/usr/bin/env python3
"""
Deterministic parser for Arizona Auditor General performance-audit reports.

Reads the per-report JSON extracted by extract_audit_reports.py and produces
three warehouse-bound tables as an intermediate artifact (ag_build/*.csv):

  ag_reports                 1 row / report   (metadata)
  ag_findings                1 row / finding  (deterministic text + recs)
  ag_report_agency_xref      agency_key -> checkbook agency (token-based bridge)

This is the DETERMINISTIC layer of the hybrid pipeline. A later cached Grok
pass (enrich_ag_findings_grok.py) fills questioned_cost_usd and cleans finding
boundaries for the ~28 reports lacking the clean 'Finding N:' template.

Honors INTEGRATION_BRIEF conventions:
  - token/word-boundary agency matching, never substring (§6.7)
  - full-snapshot, stamped with build_version (§7)
"""

import csv
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

AGENCY_DIR = Path(__file__).parent
EXTRACTED = AGENCY_DIR / "extracted"
OUT = AGENCY_DIR / "ag_build"
OUT.mkdir(exist_ok=True)

MONTHS = ("January February March April May June July August September "
          "October November December").split()
MONTH_RE = "|".join(MONTHS)


def build_version() -> str:
    try:
        sha = subprocess.check_output(
            ["git", "-C", str(AGENCY_DIR), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        sha = "nogit"
    return f"ag-{datetime.now(timezone.utc):%Y%m%d}-{sha}"


def parse_report_id(stem: str):
    """16-104_Report -> (16-104, 2016, '104', report_type)."""
    rid = stem.replace("_Report", "")
    m = re.match(r"(\d{2})-([A-Z]*\d+)", rid)
    if not m:
        return rid, 0, rid, "unknown"
    yy, no = m.group(1), m.group(2)
    fy = 2000 + int(yy)
    if no.startswith("CR"):
        rtype = "compliance_review"
    elif no.startswith("3"):
        rtype = "biennial_special"
    elif no.startswith("2"):
        rtype = "followup"
    else:
        rtype = "performance_audit"
    return rid, fy, no, rtype


AGENCY_LEAD_RE = re.compile(
    r"^(Arizona|State\b|Board\b|Department\b|Commission\b|Office\b|"
    r"Maricopa|Pima|Water\b|Registrar\b|Attorney\b)", re.IGNORECASE)
NON_AGENCY_RE = re.compile(
    r"(Auditor General|Legislature|Performance Audit|Sunset Review|"
    r"Biennial|Division|Honorable|Governor|Members of|^Ms\.|^Mr\.|"
    r"Executive Director|Deputy|^A REPORT|^A Report|Report \d{2}-|"
    r"^REPORT NO|^\d{4}|et seq|Transmitted)", re.IGNORECASE)


def _looks_like_title_sentence(ln: str) -> bool:
    """A descriptive title sentence (vs. an agency noun phrase). Keyed on
    sentence-like verbs/punctuation, NOT raw length — agency names such as
    'Arizona Foster Care Tuition Waiver Pilot Programs' are long but not prose.
    """
    return (ln.rstrip().endswith((",", ";", ":"))
            or re.search(r"\b(should|lacks?|has|have|had|did not|does not|"
                         r"appropriately|established|provides?|provided|need|"
                         r"needs|improve|helped|ensure|implement|complied|"
                         r"taken|met|but|its|their|consistent)\b", ln,
                         re.IGNORECASE) is not None)


def _join_agency_block(lines, start):
    """Join consecutive short Title-Case continuation lines into one agency
    name, stopping at the first descriptive title sentence."""
    parts = [lines[start]]
    j = start + 1
    while j < len(lines):
        nxt = lines[j]
        if (NON_AGENCY_RE.search(nxt) or _looks_like_title_sentence(nxt)
                or len(nxt.split()) > 6):
            break
        # continuation: short phrase, often ends with 'of'/'in'/'and' on prev
        if parts[-1].rstrip().endswith(("of", "in", "and", "for", "-", "—")) \
                or len(nxt.split()) <= 4:
            parts.append(nxt)
            j += 1
        else:
            break
    return re.sub(r"\s+", " ", " ".join(parts)).strip(), j


def extract_title_and_agency(page1: str, full: str):
    """Pull report title + audited agency from the title page.
    Returns (title, agency_raw). Era-aware with prioritized reliable anchors.
    """
    lines = [ln.strip() for ln in page1.split("\n") if ln.strip()]
    agency = title = None

    # --- Anchor 1: transmittal letter (2024-25). Agency = line before
    #     "Transmitted herewith"; title/type inside that sentence. ---
    has_transmittal = any(ln.startswith("Transmitted herewith")
                          for ln in lines)
    for i, ln in enumerate(lines):
        if ln.startswith("Transmitted herewith"):
            for k in range(i - 1, -1, -1):
                if AGENCY_LEAD_RE.search(lines[k]) and \
                        not NON_AGENCY_RE.search(lines[k]):
                    agency = lines[k]
                    break
            break
    # transmittal-sentence fallback ONLY when a transmittal letter exists
    # (otherwise this matches the JLAC "...composed of five senators..." prose)
    if agency is None and has_transmittal:
        m = re.search(r"report,?\s+(?:A\s+)?(?:Performance Audit|Sunset"
                      r"|Special|Follow-?up|Biennial)[^.]*?\s+of\s+"
                      r"(?:the\s+)?(.+?)\.", full[:1600],
                      re.IGNORECASE | re.DOTALL)
        if m and len(m.group(1).split()) <= 10:
            agency = re.sub(r"\s+", " ", m.group(1)).strip()

    # --- Anchor 2: Era B "Report NN-NNN" near top -> next agency line
    #     (skipping the "A Report to the Arizona Legislature" boilerplate). ---
    if agency is None:
        for i, ln in enumerate(lines[:8]):
            if re.match(r"Report\s+\d{2}-\d", ln):
                for k in range(i + 1, min(i + 4, len(lines))):
                    cand = lines[k]
                    if re.match(r"A Report to the Arizona Legislature", cand,
                                re.IGNORECASE):
                        continue
                    if AGENCY_LEAD_RE.search(cand) and \
                            not NON_AGENCY_RE.search(cand):
                        agency, j = _join_agency_block(lines, k)
                        if j < len(lines):
                            title = lines[j]
                        break
                break

    # --- Anchor 3: generic — first Arizona/Board/Dept lead line that is a
    #     noun phrase (Era A multi-line + any leftover). ---
    if agency is None:
        for i, ln in enumerate(lines):
            if AGENCY_LEAD_RE.search(ln) and not NON_AGENCY_RE.search(ln) \
                    and not _looks_like_title_sentence(ln):
                agency, j = _join_agency_block(lines, i)
                if title is None and j < len(lines) \
                        and _looks_like_title_sentence(lines[j]):
                    title = lines[j]
                break

    if agency:
        agency = re.sub(r"\s+", " ", agency).strip(" ,.-—")
    return title, agency


def extract_report_date(full: str, fy: int):
    m = re.search(rf"({MONTH_RE})\s+(\d{{1,2}}),?\s+(\d{{4}})", full[:2000])
    if m:
        try:
            return datetime.strptime(
                f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y"
            ).date().isoformat()
        except ValueError:
            pass
    m = re.search(rf"({MONTH_RE})\s*[•·]?\s*(\d{{4}})", full[:2000])
    if m:
        try:
            return datetime.strptime(
                f"{m.group(1)} {m.group(2)}", "%B %Y"
            ).date().isoformat()
        except ValueError:
            pass
    return None


FINDING_RE = re.compile(r"(?m)^Finding\s+(\d+):\s*(.+?)\s*$")
REC_RE = re.compile(r"(?m)^Recommendation\s+(\d+[a-z]?):\s*(.+?)\s*$")


def parse_findings(full: str, report_id: str):
    """Deterministic finding extraction for clean 'Finding N:' template reports.

    Returns (findings_list, parse_status).
    """
    findings = []
    fmatches = list(FINDING_RE.finditer(full))
    rmatches = list(REC_RE.finditer(full))

    if not fmatches:
        return [], "no_finding_template"

    # Dedupe finding numbers (TOC + body repeat); keep the longest title seen.
    by_no = {}
    for m in fmatches:
        no = int(m.group(1))
        title = m.group(2).strip()
        if no not in by_no or len(title) > len(by_no[no][1]):
            by_no[no] = (m.start(), title)

    # Map recommendations to the finding whose body they fall under (by offset).
    finding_offsets = sorted((pos, no) for no, (pos, _) in by_no.items())
    recs_by_finding = {no: [] for no in by_no}
    seen_rec = set()
    for rm in rmatches:
        rno = rm.group(1)
        if rno in seen_rec:
            continue
        seen_rec.add(rno)
        # nearest finding whose offset precedes this recommendation
        owner = None
        for pos, fno in finding_offsets:
            if pos <= rm.start():
                owner = fno
            else:
                break
        if owner is not None:
            recs_by_finding[owner].append(
                f"{rno}: {rm.group(2).strip()}")

    for no in sorted(by_no):
        _, title = by_no[no]
        findings.append({
            "finding_no": no,
            "finding_text": title,
            "recommendation_text": " | ".join(recs_by_finding[no]),
            "n_recommendations": len(recs_by_finding[no]),
        })
    return findings, "clean_template"


def main():
    bv = build_version()
    pdfs = sorted(EXTRACTED.glob("*_Report.json"))
    reports_rows = []
    findings_rows = []
    agency_keys = {}
    finding_seq = 0

    for jf in pdfs:
        d = json.load(open(jf))
        full = "\n".join(p["text"] for p in d["pages"])
        page1 = d["pages"][0]["text"] if d["pages"] else ""
        rid, fy, no, rtype = parse_report_id(d["report_id"])
        title, agency_raw = extract_title_and_agency(page1, full)
        rdate = extract_report_date(full, fy)
        akey = normalize_agency_key(agency_raw) if agency_raw else ""
        if akey:
            agency_keys[akey] = agency_raw

        reports_rows.append({
            "report_id": rid, "fiscal_year": fy, "report_no": no,
            "report_type": rtype, "agency_raw": agency_raw or "",
            "agency_key": akey, "title": title or "",
            "report_date": rdate or "", "total_pages": d["total_pages"],
            "source_path": d["path"], "build_version": bv,
            "extracted_at": d.get("extracted_at", ""),
        })

        findings, status = parse_findings(full, rid)
        for fnd in findings:
            finding_seq += 1
            findings_rows.append({
                "finding_id": f"{rid}-F{fnd['finding_no']:02d}",
                "report_id": rid, "finding_no": fnd["finding_no"],
                "finding_text": fnd["finding_text"],
                "recommendation_text": fnd["recommendation_text"],
                "n_recommendations": fnd["n_recommendations"],
                "questioned_cost_usd": "",          # filled by Grok pass
                "parse_status": status,
                "needs_grok": int(status != "clean_template"),
                "build_version": bv,
            })
        # reports with no deterministic findings: stub row flagged for Grok
        if not findings:
            findings_rows.append({
                "finding_id": f"{rid}-F00", "report_id": rid, "finding_no": 0,
                "finding_text": "", "recommendation_text": "",
                "n_recommendations": 0, "questioned_cost_usd": "",
                "parse_status": status, "needs_grok": 1, "build_version": bv,
            })

    write_csv(OUT / "ag_reports.csv", reports_rows)
    write_csv(OUT / "ag_findings.csv", findings_rows)
    write_agency_keys(agency_keys, bv)

    print(f"build_version: {bv}")
    print(f"ag_reports:  {len(reports_rows)} rows")
    print(f"ag_findings: {len(findings_rows)} rows "
          f"({sum(r['needs_grok'] for r in findings_rows)} need Grok)")
    print(f"distinct agency_keys: {len(agency_keys)}")
    print(f"reports with no parsed title: "
          f"{sum(1 for r in reports_rows if not r['title'])}")
    print(f"reports with no agency_raw:   "
          f"{sum(1 for r in reports_rows if not r['agency_raw'])}")


def normalize_agency_key(name: str) -> str:
    """Token-based normalization (NOT substring). Strips boilerplate words,
    expands common abbreviations, sorts to a stable key form."""
    s = name.upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\b(ARIZONA|STATE|OF|THE|AND|FOR|OFFICE|DEPARTMENT|DEPT|"
               r"BOARD|COMMISSION|DIVISION|AZ)\b", " ", s)
    tokens = [t for t in s.split() if len(t) > 1]
    return " ".join(sorted(set(tokens)))


def write_agency_keys(agency_keys: dict, bv: str):
    """Emit the xref skeleton. checkbook_agency left blank for the Grok/manual
    bridge step; we pre-fill via token overlap against checkbook agency list
    if the warehouse list is present."""
    rows = []
    checkbook = load_checkbook_agencies()
    for akey, raw in sorted(agency_keys.items()):
        match, score = best_agency_match(akey, checkbook)
        rows.append({
            "agency_key": akey, "agency_raw": raw,
            "checkbook_agency": match or "", "match_score": round(score, 3),
            "match_method": "token_jaccard" if match else "unmatched",
            "build_version": bv,
        })
    write_csv(OUT / "ag_report_agency_xref.csv", rows)
    matched = sum(1 for r in rows if r["checkbook_agency"])
    print(f"ag_report_agency_xref: {len(rows)} rows ({matched} auto-matched)")


def load_checkbook_agencies():
    try:
        import duckdb
        con = duckdb.connect(str(AGENCY_DIR.parent / "warehouse.duckdb"),
                             read_only=True)
        rows = con.execute(
            "select distinct agency from agency_summary "
            "where agency is not null").fetchall()
        con.close()
        return [r[0] for r in rows]
    except Exception as e:
        print(f"(checkbook agency list unavailable: {e})")
        return []


def best_agency_match(akey: str, checkbook):
    """Token Jaccard overlap, word-boundary based. Returns (agency, score)."""
    if not checkbook:
        return None, 0.0
    aset = set(akey.split())
    if not aset:
        return None, 0.0
    best, best_score = None, 0.0
    for cb in checkbook:
        cset = set(normalize_agency_key(cb).split())
        if not cset:
            continue
        j = len(aset & cset) / len(aset | cset)
        if j > best_score:
            best, best_score = cb, j
    # require meaningful overlap to avoid spurious links
    return (best, best_score) if best_score >= 0.34 else (None, best_score)


def write_csv(path: Path, rows):
    if not rows:
        path.write_text("")
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()),
                           quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
