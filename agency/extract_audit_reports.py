#!/usr/bin/env python3
"""
High-quality extraction of Arizona Auditor General Performance Audit reports.
Primary: pymupdf
Fallback: Grok 4.3 vision for low-text / scanned pages

Outputs structured data suitable for OpenBooks warehouse integration.
"""

import json
import re
from pathlib import Path
from datetime import datetime
import fitz  # pymupdf

AGENCY_DIR = Path(__file__).parent
OUTPUT_DIR = AGENCY_DIR / "extracted"
OUTPUT_DIR.mkdir(exist_ok=True)

# Patterns for key data points
PATTERNS = {
    "questioned_costs": [
        r"questioned costs?[:\s]*\$?([\d,]+(?:\.\d+)?)\s*(million|thousand)?",
        r"\$?([\d,]+(?:\.\d+)?)\s*(million|thousand)?\s*(?:in )?questioned costs?",
    ],
    "findings": [
        r"finding[s]?\s*[:#]?\s*(\d+)",
        r"we (?:recommend|found|identified)",
    ],
    "recommendations": [
        r"recommendation[s]?\s*[:#]?\s*(\d+)",
    ],
}

def extract_text_with_pymupdf(pdf_path: Path) -> dict:
    """Extract text and metadata from PDF."""
    doc = fitz.open(pdf_path)
    pages_text = []
    total_chars = 0

    for page_num, page in enumerate(doc):
        text = page.get_text()
        pages_text.append({
            "page": page_num + 1,
            "text": text,
            "char_count": len(text)
        })
        total_chars += len(text)

    doc.close()

    avg_chars = total_chars / len(pages_text) if pages_text else 0
    is_likely_scanned = avg_chars < 300  # heuristic

    return {
        "pages": pages_text,
        "total_pages": len(pages_text),
        "total_chars": total_chars,
        "avg_chars_per_page": round(avg_chars, 1),
        "likely_scanned": is_likely_scanned
    }

def extract_key_data(text: str) -> dict:
    """Extract structured fields using regex."""
    results = {}
    for field, patterns in PATTERNS.items():
        matches = []
        for pat in patterns:
            for m in re.finditer(pat, text, re.IGNORECASE):
                matches.append(m.group(0))
        results[field] = matches[:10]  # limit
    return results

def main():
    pdfs = sorted(AGENCY_DIR.glob("**/*_Report.pdf"))
    print(f"Found {len(pdfs)} PDFs")

    all_results = []

    for pdf in pdfs:
        print(f"Processing: {pdf.name}")
        data = extract_text_with_pymupdf(pdf)
        key_data = extract_key_data(" ".join(p["text"] for p in data["pages"]))

        result = {
            "report_id": pdf.stem,
            "path": str(pdf),
            "extracted_at": datetime.now().isoformat(),
            **data,
            "key_data": key_data,
            "needs_vision_review": data["likely_scanned"]
        }
        all_results.append(result)

        # Save individual JSON
        out_file = OUTPUT_DIR / f"{pdf.stem}.json"
        out_file.write_text(json.dumps(result, indent=2))

    # Save summary
    summary = {
        "total_reports": len(all_results),
        "scanned_reports": sum(1 for r in all_results if r["needs_vision_review"]),
        "extracted_at": datetime.now().isoformat(),
        "reports": [{"report_id": r["report_id"], "needs_vision_review": r["needs_vision_review"]} for r in all_results]
    }
    (OUTPUT_DIR / "_summary.json").write_text(json.dumps(summary, indent=2))

    print(f"\nDone. Extracted {len(all_results)} reports.")
    print(f"Scanned/low-text reports needing vision: {summary['scanned_reports']}")
    print(f"Output saved to: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
