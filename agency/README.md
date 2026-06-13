# AG Audit-Findings Layer

Structured extraction of Arizona Auditor General performance-audit reports,
integrated into `warehouse.duckdb` as a queryable findings store that bridges
to the checkbook spending data.

## Why

The warehouse's 8-row `ag_findings_xref` is a hand-curated "credibility
multiplier" linking our tier signals to AG findings. This layer scales that
idea: it turns 122 downloaded AG PDF reports into 3 relational tables so any
agency card can show *official audited findings* alongside checkbook spend.

AG findings are **audited findings**, distinct from the warehouse's
"leads warranting confirmation" tier signals — but the same scope boundary
applies (State of Arizona only; regional COGs and independent authorities have
no checkbook equivalent and are intentionally left unbridged).

## Pipeline (hybrid: deterministic + cached LLM)

```
extracted/*.json                      # pymupdf text extraction (122 reports)
  → build_ag_findings.py              # DETERMINISTIC parser
      → ag_build/ag_reports.csv         (metadata: id, FY, type, agency, date)
      → ag_build/ag_findings.csv        (Finding N: / Recommendation N: template)
      → ag_build/ag_report_agency_xref.csv  (token-Jaccard agency bridge)
  → make_grok_candidates.py           # extracts ONLY residual snippets needing
      → ag_build/grok_candidates.json   #   LLM judgment (~80KB, not full reports)
  → ag_build/grok_enrichment.json     # CACHED LLM (Grok-4.3) adjudications:
                                       #   questioned costs, finding-gap structure,
                                       #   residual agency names + bridge overrides
                                       #   *** committed — keeps refreshes deterministic
  → load_ag_to_warehouse.py           # merges all → 3 tables, CREATE OR REPLACE,
                                       #   build_version stamped, single transaction
```

## Tables

- **ag_reports** (122) — one row per report. `agency_checkbook` is the bridge
  key into `agency_summary.agency` / `tier_agency_scorecard.agency`.
- **ag_findings** (231) — one row per finding. `questioned_cost_usd` populated
  for 15 findings (~$2.04B total, dominated by the AHCCCS $1.77B FY2020 CMS
  improper-payment projection). `has_adverse_findings` distinguishes real
  findings from clean reports. `questioned_cost_confidence` flags estimates.
- **ag_report_agency_xref** (94) — agency-key → checkbook-agency map, token /
  word-boundary matched (never substring, per INTEGRATION_BRIEF §6.7).

## Coverage

- 113/122 reports bridged to a checkbook agency.
- 9 unbridged are correct non-matches: regional COGs (MAG, Pima), independent
  authorities (Sports & Tourism, Power Authority), and boards/programs with no
  state-checkbook line (Foster Care Review Board, Psychiatric Security Review
  Board, Physician Assistants Board, Foster Care Tuition Waiver program).

## Refresh

To rebuild after adding new report PDFs + re-running extraction:

```bash
cd /Users/moeasnani/Openbooks
.venv/bin/python agency/build_ag_findings.py
.venv/bin/python agency/make_grok_candidates.py    # review new residuals
# (hand-adjudicate any NEW residual cases into ag_build/grok_enrichment.json)
.venv/bin/python agency/load_ag_to_warehouse.py
```

The deterministic steps are fully reproducible. Only genuinely new residual
cases (new agencies, new questioned-cost prose) require adding entries to
`grok_enrichment.json` — existing cached adjudications are reused.
