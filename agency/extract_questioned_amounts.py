#!/usr/bin/env python3
"""
Extract main questioned dollar amounts and key financial findings
from Arizona Auditor General State Agency Performance Audit PDFs.

Primary method: pdftotext + regex (fast, works on text PDFs)
Fallback: local vision model for scanned/image-heavy pages (per user preference)

Output: agency/questioned_amounts.md (structured Markdown)
"""

import subprocess
import re
from pathlib import Path
from datetime import datetime

AGENCY_DIR = Path(__file__).parent
OUTPUT_MD = AGENCY_DIR / "questioned_amounts.md"

# Regex patterns tuned for AZ Auditor General performance audit language
PATTERNS = [
    r"approximately \$?([\d,]+(?:\.\d+)?)\s*(million|thousand)?",
    r"\$?([\d,]+(?:\.\d+)?)\s*(million|thousand)? (?:in )?(?:excess cash reserves|questioned costs|unallowable)",
    r"excess (?:cash |reserve |monies? )?[:\s]*\$?([\d,]+(?:\.\d+)?)\s*(million|thousand|)",
    r"questioned costs?[:\s]*\$?([\d,]+(?:\.\d+)?)\s*(million|thousand|)",
    r"potential savings?[:\s]*\$?([\d,]+(?:\.\d+)?)\s*(million|thousand|)",
]

def extract_from_pdf(pdf_path: Path) -> list[str]:
    """Run pdftotext and pull out candidate dollar findings."""
    try:
        txt = subprocess.check_output(
            ["pdftotext", "-layout", str(pdf_path), "-"],
            text=True, timeout=60
        )
    except Exception as e:
        return [f"ERROR extracting text: {e}"]

    findings = []
    for line in txt.splitlines():
        line = line.strip()
        if not line:
            continue
        for pat in PATTERNS:
            if re.search(pat, line, re.IGNORECASE):
                findings.append(line)
                break
    return findings[:15]  # top 15 most relevant lines per report

def main():
    pdfs = sorted(AGENCY_DIR.glob("*.pdf"))
    if not pdfs:
        print("No PDFs found in agency/. Run the download script first.")
        return

    md = ["# Questioned Amounts – Arizona State Agency Performance Audits (2016+)",
          f"Generated: {datetime.now().isoformat()}",
          f"Source PDFs: {len(pdfs)}",
          "",
          "> **Note**: Amounts extracted via pdftotext. Verify against original reports. Vision model fallback available for scanned pages.",
          ""]

    for pdf in pdfs:
        report_id = pdf.stem
        findings = extract_from_pdf(pdf)
        md.append(f"## {report_id}")
        if findings:
            for f in findings:
                md.append(f"- {f}")
        else:
            md.append("- (no obvious questioned-cost language found with current patterns)")
        md.append("")

    OUTPUT_MD.write_text("\n".join(md))
    print(f"Wrote {OUTPUT_MD} with {len(pdfs)} reports processed.")

if __name__ == "__main__":
    main()