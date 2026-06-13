#!/usr/bin/env python3
"""
Download State Agency Performance Audit PDFs from the CSV.
Handles the actual CSV structure (title row + BOM + headers on row 3).
"""

import csv
import subprocess
import time
from pathlib import Path

CSV_PATH = Path("/Users/moeasnani/Openbooks/ag_reports/AZ_State_Agency_Performance_Audits_2016-2026.csv")
BASE_DIR = Path("/Users/moeasnani/Openbooks/agency")

def download_file(url: str, dest: Path) -> bool:
    if dest.exists():
        return True
    try:
        subprocess.check_call(
            ["curl", "-sSfL", "-o", str(dest), url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=40
        )
        return True
    except Exception:
        if dest.exists():
            dest.unlink()
        return False

def main():
    print("Starting download from CSV...")
    BASE_DIR.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    skipped = 0

    with open(CSV_PATH, newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        # Skip title row and empty row
        next(reader, None)
        next(reader, None)
        headers = next(reader, None)  # Actual headers: Year, Report #, ...

        for row in reader:
            if len(row) < 6:
                continue
            year = row[0].strip()
            report_num = row[1].strip()
            pdf_url = row[5].strip()

            if not year or not report_num or not pdf_url or "http" not in pdf_url:
                continue

            year_dir = BASE_DIR / year
            year_dir.mkdir(exist_ok=True)

            # Main report
            main_dest = year_dir / f"{report_num}_Report.pdf"
            if download_file(pdf_url, main_dest):
                print(f"✓ {report_num}")
                downloaded += 1
            else:
                print(f"✗ {report_num}")

            time.sleep(0.2)

    print(f"\nDone. Downloaded: {downloaded} reports.")

if __name__ == "__main__":
    main()