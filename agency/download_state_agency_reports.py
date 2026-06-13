#!/usr/bin/env python3
"""
Targeted downloader for State Agency reports from the two high-quality search URLs:

- Audit type 1005 (Performance Audits)
- Audit type 1009 (second important category)

This version focuses only on the filtered search pages the user provided.
"""

import subprocess
import time
import sys
import re
from pathlib import Path

AGENCY_DIR = Path(__file__).parent

# The two high-quality filtered search URLs provided by the user
SEARCH_URLS = [
    "https://www.azauditor.gov/reports/search-entity/state-agencies?field_state_agency_target_id=All&field_audit_type_target_id=1005&field_date_value=&sort_by=field_date_value&sort_order=DESC",
    "https://www.azauditor.gov/reports/search-entity/state-agencies?field_state_agency_target_id=All&field_audit_type_target_id=1009&field_date_value=&sort_by=field_date_value&sort_order=DESC",
]

def get_page_content(url: str) -> str:
    """Fetch page content with curl."""
    try:
        result = subprocess.run(
            ["curl", "-sL", url],
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.stdout
    except Exception:
        return ""

def extract_report_numbers(html: str) -> list[str]:
    """Extract report numbers like 25-117, 24-105 etc. from the page."""
    # Common patterns on the site
    pattern = r'\b(2[0-5])[-–](\d{3})\b'
    matches = re.findall(pattern, html)
    reports = [f"{y}-{n}" for y, n in matches]
    return sorted(set(reports), reverse=True)

def candidate_months(report_num: str) -> list[str]:
    year = 2000 + int(report_num.split("-")[0])
    return [
        f"{year}-11", f"{year}-10", f"{year}-09", f"{year}-12",
        f"{year+1}-01", f"{year+1}-02", f"{year+1}-03",
        f"{year}-08", f"{year}-07",
    ]

def download_pdf(report_num: str, yyyymm: str, suffix: str = "") -> bool:
    filename = f"{report_num}{suffix}.pdf"
    dest = AGENCY_DIR / filename
    if dest.exists():
        return True

    url = f"https://www.azauditor.gov/sites/default/files/{yyyymm}/{report_num}{suffix}.pdf"
    try:
        subprocess.check_call(
            ["curl", "-sSfL", "-o", str(dest), url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=25
        )
        print(f"  ✓ {filename} ({yyyymm})")
        return True
    except Exception:
        if dest.exists():
            dest.unlink()
        return False

def download_report(report_num: str):
    """Try to download main report + Highlights for a given report number."""
    for month in candidate_months(report_num):
        got_main = download_pdf(report_num, month)
        got_high = download_pdf(report_num, month, " Highlights_0")

        if got_main or got_high:
            return True
        time.sleep(0.2)
    print(f"  ✗ Could not locate {report_num}")
    return False

def main():
    print("Downloading State Agency reports from the two filtered search pages...")
    all_reports = set()

    for url in SEARCH_URLS:
        print(f"\nFetching: {url[:80]}...")
        html = get_page_content(url)
        reports = extract_report_numbers(html)
        print(f"  Found {len(reports)} report numbers on first page")
        all_reports.update(reports)

        # Also try page 2–8 (common pagination)
        for page in range(1, 9):
            paged_url = url + f"&page={page}"
            html = get_page_content(paged_url)
            more = extract_report_numbers(html)
            if more:
                all_reports.update(more)

    # Filter to 2016+
    reports_2016_plus = sorted([r for r in all_reports if int(r.split("-")[0]) >= 16], reverse=True)
    print(f"\nTotal unique reports 2016+: {len(reports_2016_plus)}")

    downloaded = 0
    for num in reports_2016_plus:
        if download_report(num):
            downloaded += 1
        time.sleep(0.3)

    print(f"\nDone. Downloaded new files for {downloaded} reports.")

if __name__ == "__main__":
    main()