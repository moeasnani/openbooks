# OpenBooks Three-Way Triangulation Dashboard — Query Template Set

## Overview

This document defines the query templates that power the AI dashboard, enabling
natural-language questions across three data layers:

1. **BUDGET** — what the legislature *authorized* (SB1847 + BRBs + JLBC)
2. **CHECKBOOK** — what was *actually spent* (warehouse.duckdb, 129M txns)
3. **AG FINDINGS** — what auditors *flagged* (231 findings, $2.05B questioned)

The headline metric is **variance**: authorized vs. actual, with audit findings
as the regulatory overlay.

---

## Layer 1: Budget Queries (from structured JSON)

### BQ-1: Agency Budget Summary
**Question:** "What is the total appropriation for [agency]?"
**Inputs:** agency_name
**Output:** total_appropriation, FTE, line_items[], fund_sources[]
**Source:** `fy2027_sb1847_structured.json` → agencies[]

### BQ-2: Line-Item Breakdown
**Question:** "Break down [agency]'s budget by line item"
**Inputs:** agency_name
**Output:** [{line_item_name, amount, fiscal_year}]
**Source:** `fy2027_sb1847_structured.json` → agencies[].line_items[]

### BQ-3: Fund Source Analysis
**Question:** "What funds pay for [agency]?"
**Inputs:** agency_name
**Output:** [{fund_name, amount, pct_of_total}]
**Source:** `fy2027_sb1847_structured.json` → agencies[].fund_sources[]

### BQ-4: Top N Agencies by Appropriation
**Question:** "Show the top 10 agencies by budget"
**Inputs:** limit (default 10)
**Output:** [{agency, total_appropriation, fte, pct_of_total}]
**Source:** SB1847 sorted by total_appropriation DESC

### BQ-5: Year-over-Year Budget Comparison
**Question:** "How did [agency]'s budget change from FY2026 to FY2027?"
**Inputs:** agency_name
**Output:** {agency, fy2026_total, fy2027_total, delta, pct_change}
**Source:** SB1847 line_items with "2025-26" and "2026-27" keys + JLBC FY2026

### BQ-6: General Fund Exposure
**Question:** "How much general fund money does [agency] use?"
**Inputs:** agency_name
**Output:** {agency, general_fund_amount, general_fund_pct, other_funds_total}
**Source:** fund_sources[] filtered for "State general fund"

### BQ-7: Cross-Agency Program Search
**Question:** "Which agencies have budget line items for [program/keyword]?"
**Inputs:** keyword (e.g. "medicaid", "prison", "IT", "fire")
**Output:** [{agency, line_item_name, amount}]
**Source:** All agencies[].line_items[] name-matched

---

## Layer 2: Checkbook Queries (from warehouse.duckdb)

### CQ-1: Total Agency Spend
**Question:** "How much did [agency] actually spend?"
**Inputs:** agency_name, fiscal_year (optional)
**Existing method:** `ob.spend(agency=..., fiscal_year=...)`

### CQ-2: Spend by Category
**Question:** "What did [agency] spend on [category]?"
**Inputs:** agency_name, category (e.g. "information technology")
**Existing method:** `ob.spend(agency=..., category=...)`

### CQ-3: Spend Trend Over Time
**Question:** "Show [agency]'s spending over the years"
**Inputs:** agency_name
**Existing method:** `ob.spend(agency=..., breakdown="year")`

### CQ-4: Top Vendors for Agency
**Question:** "Who does [agency] pay the most?"
**Inputs:** agency_name, limit
**Existing method:** `ob.spend(agency=..., breakdown="vendor")`

### CQ-5: Agency Ranking by Spend
**Question:** "Which agencies spend the most?"
**Existing method:** `ob.rank_agencies("usd_tier1")` or `spend()` rollup

### CQ-6: Vendor Deep Dive
**Question:** "Show me everything about vendor [name]"
**Existing method:** `ob.entity(name)`

### CQ-7: Flagged Transactions
**Question:** "What are the suspicious transactions for [agency]?"
**Existing method:** `ob.leads(agency=...)`

---

## Layer 3: AG Findings Queries (from ag_findings tables)

### AQ-1: Agency Audit Profile
**Question:** "What audit findings does [agency] have?"
**Inputs:** agency_name
**Output:** [{finding_id, finding_text, questioned_cost, fiscal_year, title}]
**Existing method:** `ob.search_findings(agency_name)` + `agency_card()._ag_audit()`

### AQ-2: Questioned Cost Leaderboard
**Question:** "Which agencies have the most questioned costs?"
**Existing method:** `ob.rank_ag_findings("total_questioned_cost")`

### AQ-3: Finding-to-Spend Linkage
**Question:** "How does the spending flagged in audit findings compare to actual spend?"
**Output:** {finding, questioned_cost, actual_spend, pct_of_spend}
**Source:** `ag_finding_context` table (231 rows linking findings → spend)

### AQ-4: Topic Search
**Question:** "Find audit findings about [topic]"
**Inputs:** keyword (e.g. "procurement", "IT", "overtime")
**Existing method:** `ob.search_findings(keyword)`

### AQ-5: Adverse Findings
**Question:** "Which agencies have adverse audit opinions?"
**Existing method:** `ob.rank_ag_findings("n_adverse")`

---

## Layer 4: Triangulation Queries (THE DASHBOARD CORE)

### TQ-1: Budget vs. Actual Variance ⭐ HEADLINE METRIC
**Question:** "Did [agency] spend more or less than authorized?"
**Inputs:** agency_name, fiscal_year
**Output:**
```
{
  agency: "DEPT OF CORRECTIONS",
  budget_authorized: 1600697200,
  actual_spent: 1580000000,  # from spend()
  variance: -20697200,
  variance_pct: -1.3%,
  status: "under_budget" | "over_budget" | "on_track"
}
```
**Flow:** BQ-1 → CQ-1 → compute variance
**Requires:** Agency name resolution between SB1847 ↔ checkbook

### TQ-2: Three-Way Agency Card ⭐ FLAGSHIP VIEW
**Question:** "Give me the full picture for [agency]"
**Inputs:** agency_name
**Output:**
```
{
  agency: "...",
  budget: {
    total_authorized: ...,
    line_items: [...],
    fund_sources: [...],
    fte: ...
  },
  actual: {
    total_spent: ...,
    top_vendors: [...],
    spend_trend: [...],
    flagged_transactions: [...]
  },
  audit: {
    n_findings: ...,
    total_questioned_cost: ...,
    adverse_findings: ...,
    recent_findings: [...]
  },
  triangulation: {
    budget_vs_actual_variance: ...,
    questioned_cost_pct_of_budget: ...,
    flagged_txn_pct_of_spend: ...,
    risk_score: "high" | "medium" | "low"
  }
}
```
**Flow:** BQ-1 + BQ-2 + CQ-1 + CQ-4 + CQ-7 + AQ-1 + AQ-3

### TQ-3: Over-Budget Agencies
**Question:** "Which agencies spent more than their authorization?"
**Output:** [{agency, budget, actual, overage_amount, overage_pct}]
**Flow:** For each major agency: BQ-1 → CQ-1 → filter variance > 0

### TQ-4: Audit Risk × Budget Exposure Matrix
**Question:** "Which agencies have both high budgets AND audit findings?"
**Output:** [{agency, budget, n_findings, questioned_cost, risk_tier}]
**Flow:** BQ-4 (top agencies) → AQ-1 for each → cross-reference

### TQ-5: Program-Level Accountability
**Question:** "How much was authorized vs spent for [program/line item]?"
**Inputs:** keyword (e.g. "private prison", "inmate health care")
**Output:**
```
{
  program: "Private prison per diem",
  agency: "DEPT OF CORRECTIONS",
  budget_authorized: 269359200,
  actual_spent: 275000000,  # category-matched from checkbook
  variance: +5640800,
  related_findings: [...]
}
```
**Flow:** BQ-7 (keyword search) → CQ-2 (category spend) → AQ-4 (topic search)

### TQ-6: Fund Source Risk
**Question:** "Which agencies are most dependent on the general fund?"
**Output:** [{agency, gf_amount, gf_pct, total_budget}]
**Flow:** BQ-6 for all agencies → sort by gf_pct DESC

### TQ-7: Year-over-Year Delta with Spending Context
**Question:** "Did [agency]'s spending track its budget increase?"
**Output:**
```
{
  agency: "...",
  fy2026_budget: ...,
  fy2027_budget: ...,
  budget_change_pct: ...,
  fy2026_spend: ...,
  fy2027_spend: ...,  # estimated or partial
  spend_change_pct: ...,
  alignment: "tracking" | "diverging"
}
```
**Flow:** BQ-5 → CQ-3 (year breakdown)

### TQ-8: Questioned Cost as % of Budget
**Question:** "Where are audit findings most material relative to budget?"
**Output:** [{agency, budget, questioned_cost, qcost_pct, severity}]
**Flow:** BQ-1 → AQ-1 → compute questioned_cost / budget

### TQ-9: Vendor-Budget Cross-Reference
**Question:** "Are payments to [vendor] consistent with [agency]'s authorized line items?"
**Inputs:** vendor_name, agency_name
**Output:**
```
{
  vendor: "...",
  agency: "...",
  total_paid: ...,
  matching_line_items: [...],
  unexplained_payments: ...,
  related_findings: [...]
}
```
**Flow:** CQ-6 (entity) → BQ-2 (line items) → AQ-4 (vendor keyword)

### TQ-10: Dashboard Summary Cards
**Question:** "Give me the statewide overview"
**Output:**
```
{
  total_budget_authorized: 47423774900,
  total_actual_spent: ...,  # latest FY
  total_questioned_costs: 2050000000,
  n_agencies: 86,
  n_findings: 231,
  n_adverse: ...,
  top_overruns: [...],  # TQ-3 top 5
  top_audit_risks: [...],  # TQ-4 top 5
  top_questioned: [...]  # TQ-8 top 5
}
```

---

## Implementation Notes

### Agency Name Resolution
SB1847 uses full legal names; checkbook uses abbreviated forms.
Use `ob.resolve_agency()` for fuzzy matching. Key mappings:
- "STATE DEPARTMENT OF CORRECTIONS" → "DEPT OF CORRECTIONS"
- "ARIZONA HEALTH CARE COST CONTAINMENT SYSTEM" → "AHCCCS"
- "DEPARTMENT OF ECONOMIC SECURITY" → "DEPT OF ECONOMIC SECURITY"
- etc.

### Data Freshness
- **Budget**: FY2026-27 (static — enacted legislation)
- **Checkbook**: FY2016-FY2025 (historical actuals)
- **AG Findings**: Through latest audit cycle
- **Gap**: FY2026 checkbook data not yet in warehouse (when available, TQ-1 becomes live)

### Natural Language → Template Mapping
| User says... | Template |
|---|---|
| "How much was X authorized?" | BQ-1 |
| "What did X actually spend?" | CQ-1 |
| "Did X go over budget?" | TQ-1 |
| "Show me everything about X" | TQ-2 |
| "What audit problems does X have?" | AQ-1 |
| "Which agencies are over budget?" | TQ-3 |
| "Where are the biggest audit risks?" | TQ-4 |
| "What's the full picture?" | TQ-10 |
