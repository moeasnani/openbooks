# Arizona Fiscal Governance Assessment — Methodology & Narrative
### For Overtaker.ai · institutional analytics for municipal bondholders

**Obligor scope:** State of Arizona and its agencies/funds (the state checkbook). **This does not describe the credit of Arizona cities, counties, school, or special districts** — state grants *to* a local entity describe the State's spending, not the local issuer's. Use alongside, not instead of, the Arizona Auditor General reports, the state ACFR, and official statements.

**What this is / is not.** A data-driven *screening and fiscal-management indicator* built from transaction-level disbursement and receipt records. It is **not** a credit rating, not the product of an NRSRO, and not an audit. Anomaly flags are *items warranting confirmation*, never findings of fraud or wrongdoing. Every metric is source-grounded and reproducible from the warehouse.

---

## 1. Data provenance & limitations (disclosed up front)
- **Corpus:** 115.7M transactions, FY2016–FY2025 (9 years). Source: Arizona OpenBooks checkbook CSVs (~74 GB) → typed Parquet → DuckDB warehouse.
- **FY2022 is excluded** — source data shipped only a partial file (~3.3M of ~13M rows). Year-over-year trends skip FY2022.
- **FY2021 lost 69,917 rows (0.57%)** and FY2018 10,350 rows (0.08%) to malformed source CSV (unescaped quotes). FY2021 totals are therefore marginally understated and should be recovered before relied upon.
- **`hourly_rate` is 100% empty** in source — no payroll pay-rate analysis is possible.
- **`99999999.xx` placeholder amounts** (~$19.7B across the corpus) are sentinels, excluded from every total here. Their per-year count is itself tracked as a data-hygiene indicator.
- **Coded vendor identifiers exist FY2017+**; invoice numbers only FY2016–2018 & FY2021. Vendor-level analysis is not uniform across all years.

---

## 2. Modules (the structured feed)
All feed tables live in `warehouse.duckdb`; each carries a neutral annotation in `feed_annotations`.

### 2.1 Fund Flow Analysis — federal dependency
`ff_federal_dependency_by_year`, `ff_federal_dependency_by_agency_year`
- Federal transfers were ~41% of revenue pre-2020, **peaked at 48.0% in FY2023**, and sit at **45.5% in FY2025** — above the pre-pandemic baseline.
- Exposure is concentrated: **AHCCCS (Medicaid) ~81% of $20.4B**, Economic Security ~97%, Emergency & Military Affairs ~95%, Education ~56%. *Interpretation (neutral): nearly half the revenue base, and a majority of several large agencies, is exposed to federal funding decisions — a revenue-volatility factor.*

### 2.2 Procurement Flags
`pf_duplicate_payment_groups`, `pf_structuring_by_agency`, `pf_vendor_concentration`
- **Duplicate payments:** raw same-value repeats total $4.67B, but after excluding same-day batch disbursements the *different-day* exposure is $706.9M, of which **99.1% is Medicaid managed-care capitation mechanics**. The genuinely reviewable non-Medicaid residual is **$6.3M (largest single item $9,981)** — immaterial. Confirmation of any item requires warrant-number reconciliation not present in source data.
- **Structuring:** expenditures clustering just under $5,000 are elevated for Dept of Veterans Services (1.77×), Senate (1.57×), House (1.32×); their $10k ratios are <1. *Consistent with a $5k small-purchase threshold; not evidence of intent.*
- **Vendor concentration (self-payments excluded):** Lottery→Bank of America 77%, Emergency/Military→Banner Health 71% ($711M), Corrections→NaphCare 41% ($1.0B agency). *Sole-source/dependency exposure, not irregularity.*

### 2.3 Vendor Compliance Screening
`pf_vendor_compliance` — replaces the rejected "ideological" concept with objective, list-based screening.
- 127,858 vendors / $277B screened against the **live OFAC SDN list (19,050 entries**, normalized exact-name) and **Section 889 / FAR-4.20 prohibited sources** (Huawei, ZTE, Hytera, Hikvision, Dahua, Kaspersky; word-boundary match).
- **Result: 0 confirmed hits.** One OFAC generic-name collision ("INDUSTRIAL SOLUTIONS," $18k, FY2016) flagged for confirmation. *Methodology note: an initial substring matcher was rejected after it produced 25 false positives ($47M, all Arizona "Aztec"-named firms colliding with the token ZTE); word-boundary matching corrected this.*

---

## 3. Governance scorecard & band methodology
`gov_scorecard` (per year), `gov_fiscal_panel`. Bands are **documented thresholds**, not a black-box composite — a bondholder can recompute every band. No single composite score is published, because any weighting across these dimensions is a judgment we decline to disguise as a measurement.

| Indicator | Definition | Bands |
|---|---|---|
| **Net cash margin** | (Receipts − Disbursements) / Receipts. *Cash-basis proxy; NOT GAAP fund balance.* | favorable ≥ +2% · balanced −2%…+2% · watch < −2% |
| **Federal revenue dependency** | Federal transfers / total revenue | lower ≤40% · moderate 40–45% · elevated >45% |
| **Data-quality (sentinel lines)** | Count of 99999999.xx placeholders/yr | low ≤25 · moderate 26–75 · elevated >75 |

**Per-year reading (exhibit):** surpluses FY2018–2021 (peak **+6.7% / $3.4B in FY2021**, stimulus era), **a −3.2% / $1.9B cash deficit in FY2024** (post-stimulus, consistent with Arizona's documented FY2024 shortfall), near-balance in FY2025. Federal dependency trends "elevated" from FY2020 on. Data-hygiene worsens to "elevated" in FY2024–25 (rising sentinel counts).

> The FY2024 cash deficit and elevated, sticky federal dependency are the two most credit-relevant signals in the series. Both are neutral observations grounded in source fields; neither is an opinion on Arizona's ultimate creditworthiness.

---

## 4. Auditor General cross-reference (completed — FY2024 Single Audit / Highlights ingested)
Source: AZ Auditor General, *State of Arizona, Year Ended June 30, 2024* — Annual Comprehensive Financial Report & Single Audit Report (Highlights + Single Audit, downloaded to `ag_reports/`). The FY2024 federal compliance audit covered 25 federal programs/clusters: **2 adverse, 5 qualified, 18 unmodified opinions; 22 findings; $40,802,572 questioned costs.** The State's financial statements received **qualified opinions specifically because of AHCCCS and DES activity** auditors could not substantiate. Mapping table: `ag_findings_xref`.

**The central result — independent corroboration.** Our transaction-level analysis, built with no access to the audit, independently surfaced the *same* risk concentration the AG identified through formal audit:

| Our signal (data-derived) | AG finding (audit) | Relationship |
|---|---|---|
| **AHCCCS = #1 federal exposure** (81% of $20.4B revenue) | AHCCCS = 57.3% ($16.4B) of all federal spend; FY24 financials **restated** ($384.8M + $30.5M errors); **300+ sober-living providers suspended** for fraudulent behavioral-health billing (Findings 2024-21, -22, -120) | **Corroborates** |
| **DES = #2 federal exposure** (97%) | DES had the most findings (6): UI bank recs incomplete ($19.9M unreconciled, fraud risk), $257.4M COVID-UI unsupported, childcare/rental payments lacked eligibility docs (2024-05/06/07/104/107) | **Corroborates** |
| **Procurement Flags** (process/competition irregularities exist) | ADE: **$37.3M to 57 vendors without fair/open competition**; unauthorized noncompetitive waiver (2024-111) | **Corroborates** |
| **Vendor concentration: Corrections→NaphCare** (41%, $1B agency) | DCRR spent **$50.9M opioid-settlement monies undocumented** (2024-10) | **Extends** |
| **Control/data-quality trend degrading** (rising sentinels FY24–25) | ACFR filed ~13 months late; **uncorrected federal findings rose 8→42 FY2019–FY2024** (2024-101, Fig. 3) | **Corroborates** |
| **Payroll analysis impossible** (`hourly_rate` 100% empty) | DEMA lacked pay-rate documentation on $1.7M federal payroll (2024-116) — the exact blind spot our data has | **Corroborates our limitation** |

**The correction — and why it matters for credibility.** Our governance scorecard flagged FY2024 net cash margin at **−3.2% (−$1.9B), "watch."** The audited ACFR shows the State's **net position *increased* $259M in FY2024** (accrual basis, total net position $49.4B). The ~$2.2B divergence is exactly the cash-vs-GAAP gap our methodology disclaims. **The FY2024 "watch" band must not be read as accrual fiscal deterioration** — on audited statements the year was a small surplus. The cash-margin indicator is a timing/liquidity texture, not a solvency verdict; the ACFR governs. (Surfacing this correction, rather than letting the cash deficit stand as a scary headline, is the discipline this product depends on.)

*Status: FY2024 ingested and mapped. FY2023 & FY2025 Single Audit PDFs are downloaded (`ag_reports/`) for trend extraction; per-finding subaward dollar detail can be deepened from the full Single Audit Report's Schedule of Findings.*

---

## 5. Standing disclaimers for bondholder-facing use
1. Screening flags are leads requiring confirmation; they are not assertions of fraud, misconduct, or illegality, and no individual or named entity is accused.
2. Cash-basis figures are proxies derived from the checkbook and will differ from audited GAAP financial statements; the ACFR governs.
3. FY2022 is absent and FY2021 is marginally incomplete; trends are affected accordingly.
4. This is analytics, not a credit rating or investment advice.
