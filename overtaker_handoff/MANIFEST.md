# Overtaker handoff — Arizona fiscal governance assessment

Scope: **State of Arizona + agencies/funds**, FY2016–FY2025 (10 years; FY2022 recovered 2026-06-16).
All metrics derive from the state checkbook; see methodology for limitations.

## /feed — structured metrics (ingest these)
Each table also has a neutral, source-grounded note in `feed_annotations.csv`.

| File | Grain | What it is |
|---|---|---|
| `ff_federal_dependency_by_year.csv` | year | Federal revenue as % of total revenue |
| `ff_federal_dependency_by_agency_year.csv` | year × agency | Same, per agency (AHCCCS/DES concentration) |
| `gov_fiscal_panel.csv` | year | Revenue, expenditure, net cash margin, federal dependency, data-quality |
| `gov_scorecard.csv` | year × indicator | Banded indicators (documented thresholds; no black-box composite) |
| `pf_duplicate_payment_groups.csv` | group | Different-day repeated-payment review queue (severity/confidence/status) |
| `pf_structuring_by_agency.csv` | agency | Just-under-$5k/$10k clustering ratios |
| `pf_vendor_concentration.csv` | year × agency | Top single-vendor dependency (self-payments excluded) |
| `pf_vendor_compliance.csv` | hit | OFAC SDN / Section 889 screen results (0 confirmed) |
| `ag_findings_xref.csv` | signal | Cross-reference to AZ Auditor General FY2024 findings |
| `feed_annotations.csv` | metric | Neutral annotation per feed table |
| `_feed_bundle.jsonl` | — | First-pass feed tables as JSON lines for app ingestion |

### High-value transaction tiering (new — see `TIERING_METHODOLOGY.md`)
Scrutiny-tiering of every expenditure ≥ $100K (306,604 txns = 82.6% of all spend) on 10 forensic marker
families, two-axis (materiality × behavior) Tier 1–4, hardened by an 88-agent adversarial-verification pass.

**Display surface — read these first for vendor display (deduped, drill-down):**
- `tier_entities.csv` — **one row per vendor** (vendor-ID variants merged); the elegant replacement for per-transaction repetition
- `tier_entities_nested.json` — same entities with transactions nested, for responsive expand/collapse
- `tier_browser.html` — **primary viewer**: vendor-first list (one row per vendor) → expand to transactions. Optional group-by Agency/Cabinet/Tier/Verdict; filter is optional

| File | Grain | What it is |
|---|---|---|
| `tier_entities.csv` | **vendor** | One row per canonical vendor (IDs merged): exposure, tiers, span, agencies, markers, verdict |
| `tier_entities_nested.json` | vendor → txns | Nested drill-down (transactions inside each parent vendor) |
| `tier_entity_crosswalk.csv` | name → parent | Auditable parent-company merge map (method + confidence) |
| `tier_agency_scorecard.csv` | agency | Per-agency tiering card: exposure, Tier-1/2 $, Tier-1+2 % of high-value spend, dominant markers |
| `tier_agency_year.csv` | agency × year | High-tier exposure trend (bondholder time-series) |
| `tier_vendor_flagged.csv` | vendor | Vendors with Tier-1/2 exposure + adversarial-verification verdict & public context |
| `tier_top_transactions.csv` | transaction | The 1,094-item Tier-1 leaderboard (markers + verify status) |
| `tier_program_summary.csv` | appropriation | Program-level high-tier concentration |
| `tier_distribution.csv` | tier × year | Tier counts/exposure (Tier 4 = $304B large-but-clean; Tier 1 = $5.1B) |
| `tier_interesting_entities.md` | — | Curated marquee shortlist + headline findings (the high-level interesting data) |
| `_tier_feed_bundle.jsonl` | — | All tier feed tables as JSON lines for app ingestion |

## Documentation (display / due-diligence)
- `INTEGRATION_BRIEF.md` — **for Overtaker eng/product**: data contracts, integration surfaces, non-negotiables, phased Phase 0–3 plan, open decisions
- `ASSESSMENT_METHODOLOGY.md` — methodology, band thresholds, AG corroboration, disclaimers
- `FINDINGS_REPORT.md` — first-pass forensic findings narrative
- `TIERING_METHODOLOGY.md` — high-value tiering model (population, markers, scoring, tier matrix, verification)
- `TIERING_FINDINGS.md` — tiering findings: marquee leads, entity/program concentration, what the big dollars are

## NOT included (internal — available on request)
- `parquet/` raw typed transactions (2.4 GB, source of truth)
- `ag_reports/` Auditor General source PDFs (citations)
- `sql/` build scripts (reproducibility)

## Refresh
Regenerate by rerunning the `sql/` pipeline against new checkbook data, then re-export this bundle from `warehouse.duckdb`.
