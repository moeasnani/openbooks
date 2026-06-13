#!/usr/bin/env python3
"""
Expanded questioned-cost candidate harvester (v2, all 122 reports).

Widens the net well beyond v1's narrow keyword list: any $-bearing sentence
near a broad questioned-cost vocabulary (unsupported/improper/unallowable/
ineligible/duplicate/overpayment/recover/repay/noncompliance/...) plus the
immediately preceding sentence for context. Emits a compact payload for
in-context Grok adjudication.

Output: ag_build/qc_candidates_v2.json
"""

import json
import re
from pathlib import Path

EXTRACTED = Path(__file__).parent / "extracted"
OUT = Path(__file__).parent / "ag_build"

DOLLAR = re.compile(r"\$[\d,]+(?:\.\d+)?\s*(?:million|billion|thousand)?", re.I)
WIDE = re.compile(
    r"\b(question|unsupport|improper|unallow|unreconcil|misspent|misspend|"
    r"recover|disallow|overpa|fraud|unaccounted|lack\w* (?:support|docu)|"
    r"without (?:support|docu)|not support|insufficient docu|ineligible|"
    r"erroneous|duplicate paymen|waste|abuse|noncompli|did not (?:comply|"
    r"have docu)|could not (?:locate|provide|support)|inappropriate|excess|"
    r"owed|repay|reimburse|not allowable|prohibited|should not have)\b", re.I)

# context-only noise: figures that are almost never the audit's own QC
NOISE = re.compile(
    r"\b(nation-?wide|annually nation|premium increase|American Rescue|"
    r"appropriat\w+ \$|budget|received \$[\d,]+ (?:million|billion) (?:in|"
    r"through|from)|total (?:budget|appropriation|revenue)|civil penalty|"
    r"\$[\d,]+ (?:to|-) ?\$[\d,]+ (?:civil|penalty|fine))\b", re.I)


def sentences(text):
    text = re.sub(r"\s+", " ", text)
    return re.split(r"(?<=[.])\s+", text)


def main():
    payload = []
    for jf in sorted(EXTRACTED.glob("*_Report.json")):
        d = json.load(open(jf))
        rid = d["report_id"].replace("_Report", "")
        full = " ".join(p["text"] for p in d["pages"])
        sents = sentences(full)
        cands = []
        seen = set()
        for i, s in enumerate(sents):
            if not (DOLLAR.search(s) and WIDE.search(s)):
                continue
            s = s.strip()
            if len(s) < 30 or len(s) > 400:
                continue
            key = s[:80]
            if key in seen:
                continue
            seen.add(key)
            prev = sents[i - 1].strip()[:200] if i > 0 else ""
            cands.append({
                "text": s,
                "context_before": prev,
                "likely_noise": bool(NOISE.search(s)),
            })
            if len(cands) >= 12:        # cap per report
                break
        if cands:
            payload.append({"report_id": rid, "candidates": cands})

    (OUT / "qc_candidates_v2.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False))
    tot = sum(len(p["candidates"]) for p in payload)
    noise = sum(c["likely_noise"] for p in payload for c in p["candidates"])
    print(f"reports with candidates: {len(payload)}")
    print(f"total candidates: {tot}  (flagged likely_noise: {noise})")
    print(f"payload size: {(OUT / 'qc_candidates_v2.json').stat().st_size/1024:.1f} KB")


if __name__ == "__main__":
    main()
