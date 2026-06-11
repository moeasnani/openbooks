# Overtaker Integration Brief — Arizona OpenBooks Analytics Platform

*Prepared for Overtaker product + engineering leadership. Every figure below was verified directly against the shipped feed in `overtaker_handoff/` and the `sql/` pipeline.*

---

## 1. Executive summary

This is an analytics layer over the **State of Arizona OpenBooks checkbook** (FY2016–2025; FY2022 absent), purpose-built for **municipal bondholders** as overtaker.ai's audience. It turns ~$476B of raw state cash-basis spending into two things a bondholder can act on:

1. A **fiscal-governance assessment** of the state as an obligor (federal-revenue dependency, a banded governance scorecard, procurement flags, an OFAC/Section-889 vendor screen, and an Arizona Auditor-General cross-reference).
2. A **high-value transaction tiering** of every expenditure ≥ $100,000 (306,604 transactions = 82.6% of all dollars), each scored on 10 forensic marker families and assigned a **Tier 1–4 review priority**, rolled up to vendors / agencies / programs, with a vendor-first drill-down.

**What integrating it gives Overtaker:** a finished, defensible product — not a raw dataset. It ships a working self-contained UI, a relational star of pre-aggregated feeds, a human-verified overlay that separates real leads from accounting noise, and a fully deterministic refresh pipeline. The analytical core is **state-agnostic**, so Arizona is the first instance of a repeatable, multi-state product, not a one-off. Critically, **87% of high-value transactions score zero markers** and ~$304B of scheduled mega-flows are correctly parked as "large-but-clean" — the model's first job is to *exonerate* the vast majority of dollars, which is the most persuasive thing it does.

**The single non-negotiable:** every tier, marker, and flag is a **"lead warranting confirmation," never a finding of fraud or wrongdoing.** No entity is accused. Cash-basis checkbook ≠ audited GAAP (the ACFR governs). These disclaimers are a **data contract**, not UI copy, and must travel with every surfaced row.

---

## 2. What you're receiving

A self-contained **`overtaker_handoff/`** folder (7.7 MB) with two integrated components plus docs.

**Component A — Fiscal-governance assessment (prior work).** Per-obligor, time-series, and externally-corroborated feeds:
- `ff_federal_dependency_by_year` (9), `ff_federal_dependency_by_agency_year` (986)
- `gov_fiscal_panel` (9), `gov_scorecard` (27 banded governance indicators)
- `pf_duplicate_payment_groups` (15,828), `pf_structuring_by_agency` (32), `pf_vendor_concentration` (140), `pf_vendor_compliance` (1)
- `ag_findings_xref` (8) — the Arizona Auditor-General cross-reference (a credibility multiplier)
- `feed_annotations.csv` (19) — a neutral note per table, designed to be attached to exports

**Component B — High-value transaction tiering (new).** The 8 tier tables, the canonical nested JSON, the reference browser, and the docs (see §3).

**Docs (markdown):** `MANIFEST.md`, `TIERING_METHODOLOGY.md`, `TIERING_FINDINGS.md`, `ASSESSMENT_METHODOLOGY.md`, `FINDINGS_REPORT.md`, plus `tier_interesting_entities.md` (curated marquee shortlist).

**Not shipped, available on request:** the reproducibility backbone — `parquet/` (2.4 GB typed transactions, source of truth), `warehouse.duckdb`, `mart/` (feature mart + the curated verdicts file), and the `sql/` pipeline. This is the hard dependency for refresh and API work (§7, §8).

---

## 3. The data model & contracts

### 3.1 Feed tables (all row counts verified)

| Table | Rows | Grain | Primary key | Notes |
|---|---|---|---|---|
| `tier_entities.csv` | 1,700 | parent vendor | `entity_key` (normalized parent payee string) | The vendor universe |
| `tier_vendor_flagged.csv` | 901 | raw system vendor_id | `vid` | **Separate identity space** (see 3.2) |
| `tier_top_transactions.csv` | 1,094 | transaction | `rank` (build-local only) | Tier-1 leaderboard. **No durable txn id in CSV** |
| `tier_agency_scorecard.csv` | 108 | agency | `agency` | Obligor card |
| `tier_agency_year.csv` | 712 | agency × year | (`agency`, `fiscal_year`) | The bondholder trend series |
| `tier_program_summary.csv` | 138 | appropriation | `appropriation` | Program view |
| `tier_distribution.csv` | 50 | tier × year | (`fiscal_year`, `tier`) | **Contains literal `ALL` rollup row** + tier `5` = Unranked |
| `tier_entity_crosswalk.csv` | 4,543 | raw normalized name | `entity_key`; FK `parent_key` | The reversible merge map |

### 3.2 The entity join graph (the load-bearing contract)

There are **two distinct identity namespaces that do NOT share a surrogate key.** This is the one thing that will silently corrupt an integration if missed:

- **Name-based (the merge graph):** `crosswalk.entity_key` (raw normalized name) → `crosswalk.parent_key` **===** `tier_entities.entity_key`. Confirmed in `apply_crosswalk.sql`: `tier_entities` is keyed on `coalesce(crosswalk.parent_key, normalized_payee)`, and its emitted `entity_key` equals that value. `group_size > 1` means a real merge.
- **ID-based:** `tier_vendor_flagged.vid` and `tier_top_transactions.vid` are the source-system `vendor_id_code`, present on only ~24–48% of rows.

**The only bridge between the two is the payee string** (`tier_entities.entity_name` / `tier_vendor_flagged.payee`). **Do not join `vid` to `entity_key`** — there is no such key. Pick one grain per view or you will double-count exposure.

### 3.3 The canonical UI document — `tier_entities_nested.json`

A flat top-level **array of 840 vendor nodes** (verified: type = list, len = 840). This is *the* render contract; store it as a document/JSONB blob and refresh it wholesale — do **not** shred it.

- **Node keys** (verified): `entity_name, names_merged, primary_agency, primary_cabinet, agencies[], n_agencies, n_ids, n_txn, n_tier1, n_tier2, exposure, usd_tier1, top_tier, max_score, fy0, fy1, top_markers[], verdict, overtaker_interest, public_context, transactions[]`
- **Transaction keys** (verified): `fy, date, agency, cat, amount, score, tier, markers`
- Nodes are **pre-filtered to `top_tier ∈ {1,2}`** (192 Tier-1 + 648 Tier-2). The deeper/cleaner universe lives in `tier_entities.csv` (1,700). The `transactions[]` array is capped at top-30 by score per node — for full transaction history, read the CSV/relational grain, not the JSON.

### 3.4 The one-loader path — `_tier_feed_bundle.jsonl`

4,703 lines, each `{feed, row}`. Verified coverage: it flattens **exactly 7 feeds** — `tier_agency_scorecard (108), tier_agency_year (712), tier_entities (1700), tier_vendor_flagged (901), tier_top_transactions (1094), tier_program_summary (138), tier_distribution (50)`. **`tier_entity_crosswalk` is NOT in the bundle** (verified). A bundle-only ingest silently loses the entity merge map and breaks the parent rollup join — **always load the crosswalk from its own CSV.**

> **CSV hazard (verified directly):** payee/entity names contain embedded commas, quotes, ampersands, backslashes, and newlines. Use a strict **RFC-4180** parser and assert post-load row counts against the table above. `tier_distribution.fiscal_year` must be typed **VARCHAR** (or the `ALL` row split out) or an integer cast will drop the grand-total row.

---

## 4. How the tiering works (integration-relevant)

**Fully recomputable, no black box.** Verified from `tiering_v2.sql`:

```
risk_score = marker_sum × category_multiplier
```

- `marker_sum` = sum over 10 marker families, each contributing only its single strongest fire, after cross-family de-duplication.
- `category_multiplier` re-weights by spend type — verified map: `DEBT SERVICE 0.3 … AID 0.4 … TRANSFERS OUT 0.4 … PERSONAL SERVICES 0.7 … CAPITAL OUTLAY 1.3 … PROFESSIONAL AND OUTSIDE SERVICES 1.5`, else `1.0`.
- **Tier CASE (verified verbatim):**

| Tier | Rule | Meaning |
|---|---|---|
| **1** | `amount ≥ $1M AND risk_score ≥ 6` | Top review priority |
| **2** | (`≥$1M AND score ≥ 3`) OR (`<$1M AND score ≥ 6`) | Secondary review |
| **3** | `risk_score > 0` | Some markers fired |
| **4** | `amount ≥ $1M` (clean) | **High-value but CLEAN** — scheduled mega-flows, correctly parked |
| **5 / Unranked** | else | Small + clean |

Every tier is reproducible from **3 source fields**: `amount`, `category1`, fired markers.

**Verified distribution (the `ALL` rollup row):** Tier 1 = 1,094 txns / **$5.05B**; Tier 4 = 40,918 txns / **$304.3B** (the clean-by-structure story); Tier 5 = 227,032 / $61.2B.

**Calibration provenance:** a 13-agent auditor calibration panel + an 88-agent adversarial-verification pass that *retired* markers firing on accounting convention rather than behavior, cutting Tier 1 from 3,146 → 1,094.

### The verification overlay (the differentiator)

A human/agent-curated overlay (`mart/vendor_verdicts.csv`, ~60 rows) LEFT-JOINed throughout. **Verified enums and coverage:**

- `verify_status` (`tier_top_transactions`, **never blank**): `screened_unreviewed 651, explained_benign 290, mixed 106, false_positive_marker 28, genuine_review 19`.
- `verify_verdict` (`tier_entities`): `blank 1,647, explained_benign 31, false_positive_marker 10, genuine_review 7, mixed 5`.
- `overtaker_interest` 1–5 (a **salience/newsworthiness** signal, not a guilt score).
- `recommended_action` (`tier_vendor_flagged`): `annotate 20, downgrade 13, suppress_marker 11, keep 6, blank 851`.

**The central design fact:** only ~3% of vendor rows (53/1,700) and ~40% of Tier-1 txns (443/1,094) are human-adjudicated. The rest are **machine-screened, not reviewed.** Blank/`screened_unreviewed` must render as **"not yet reviewed," never as "clean."**

---

## 5. Integration surfaces

The feed grains map 1:1 onto five bondholder-facing screens:

| Screen | Bondholder question | Feeds |
|---|---|---|
| **Issuer/agency fiscal card** (landing) | "How exposed & how well-governed is my obligor?" | `tier_agency_scorecard` + `tier_agency_year` (trend) + `ff_federal_dependency_*` + `gov_scorecard` + `ag_findings_xref` (AG-corroboration badge) |
| **Vendor-entity explorer** | "Which vendors, and is it real?" | `tier_entities_nested.json` (render contract) |
| **Transaction drill-down / action queue** | "Show me the leads" | node `transactions[]` + `tier_top_transactions` (filter `verify_status ∈ {genuine_review, screened_unreviewed}`) |
| **Program view** | "Which appropriations?" | `tier_program_summary` |
| **Exposure waterfall + Marquee rail** | "Does the model clear most dollars?" | `tier_distribution` (total → Tier-4 ~$304B parked → Tier-1 ~$5.05B) + `tier_interesting_entities.md` |

**Navigation spine — reuse verbatim from the reference browser:** five group-by lenses already implemented — **Vendor / Agency / Cabinet / Tier / Verdict** — plus one free-text filter. Cabinet rollup is meaningful (verified node distribution: TRANSPORTATION 251, GENERAL GOVERNMENT 177, HEALTH AND WELFARE 138, PROTECTION AND SAFETY 87, EDUCATION 64, NATURAL RESOURCES 45, INSPECTION AND REGULATION 10, unassigned 68).

**Default ranking:** sort by `overtaker_interest DESC`, then `exposure / usd_tier1 DESC`, with `verdict` as a colored credibility chip *alongside* the tier chip. This surfaces the ~19 genuine_review txns and verified vendors above the 87% that score zero. `explained_benign` rows stay **visible, not hidden** — their `public_context` is the "we checked, here's why it's fine" reassurance surface.

**Interim surface — `tier_browser.html` (993 KB).** Fully self-contained (nested JSON embedded inline, vanilla JS, no server/build/fetch). Host it behind auth and it *is* the product on day one. **Two verified defects to fix before shipping vendor names (see §6).**

---

## 6. Non-negotiable requirements

These are enforced from the artifacts but **must be re-enforced at the presentation layer** — the reference UI does not yet meet all of them.

1. **"Leads not findings" framing on every view that names a vendor.** Reuse `TIERING_METHODOLOGY.md §7` / `ASSESSMENT_METHODOLOGY.md §5` verbatim — do not weaken it. *Verified gap: the browser carries only the one-line subtitle "Leads warranting confirmation, never findings."*

2. **`verify_status` / `verify_verdict` + `public_context` must render in the same component as the tier/marker chip — a flag is never shown bare.** For the 28 `false_positive_marker` and 290 `explained_benign` Tier-1 txns, the verdict (the *cleared* conclusion) must be at least as prominent as the marker, or the product publishes a refuted signal against a named firm.

3. **`screened_unreviewed` needs an explicit visible "machine-screened, not yet reviewed" badge.** *Verified defect: the browser defines CSS for only 4 verdict states (`v-genuine_review, v-mixed, v-explained_benign, v-false_positive_marker`); `screened_unreviewed` — the **majority** Tier-1 state at 651/1,094 — has zero mentions and renders with no badge, which reads as "confirmed by omission." This is the single highest-liability defect in the shipped UI.*

4. **Persistent, non-dismissible disclaimers in the product surface, not only in markdown:** cash-basis-vs-GAAP/ACFR-governs; data limits; "analytics, not a credit rating or investment advice." *Verified gap: a search of the browser for `cash-basis / ACFR / GAAP / FY2022 / investment advice` returns 0 hits.*

5. **Data-limit disclosures wherever absence could be misread as clean:** FY2022 entirely absent (a *gap*, not a zero, on every trend line); FY2019–2020 lack appropriation/fiscal_period/vendor_id system-wide (markers self-suppress — those years under-flag by construction); `vendor_id` present on ~24–48% of rows; `contract_number` ~2–6%; the FY2021 vendor-id re-mint mislabels long-standing payees as "new." Materialize these as first-class row flags on year-grained tables so trend math never reads a suppressed year as a real decline.

6. **Scope boundary:** the feed describes **State of Arizona** spending, **NOT** the credit of cities/counties/school/special districts, nor the creditworthiness of any named vendor. A state grant *to* a local entity describes state spending.

7. **Auditability / reversibility as a feature:** expose a "why this tier" explainer (`amount + category1 + fired markers → marker_sum × category_multiplier`) and a "names merged into this parent" panel driven by the crosswalk (`method` + `confidence` + `group_size` preserved). Provide a documented entity contest/correction path. **Name matching stays token/word-boundary based — never substring** (the documented "Aztec"/ZTE false-positive class). Sentinels (`99999999.xx`) excluded; JV/umbrella entities protected from over-merge.

---

## 7. Refresh & reproducibility

**Engine: DuckDB throughout** (embeddable, columnar, no server). The same `warehouse.duckdb` tables back both the static feed and any future API — migration is additive, not a rewrite.

**The deterministic `sql/` DAG (verified order):**

```
parquet/ (new FY load)
  → build_warehouse.sql        (transactions view: transaction_type IN ('EX','RV'))
  → build_feature_mart.sql     (mart/hv_base.parquet, ≥$25K spine; excludes sentinels; EX & amount>0)
  → tiering_v2.sql             (tx_tiered: risk_score / tier / fired_markers)
  → build_rollups.sql          (agency / vendor / program / year tables + tier_top_transactions)
  → resolve_entities.py + apply_crosswalk.sql   (deterministic union-find parent merge → tier_entities + nested JSON)
  → write_feed.sql             (COPY all CSVs to feed/)
  → _make_browser.py           (embeds nested JSON into tier_browser.html)
```

**Load semantics:** every producer is `CREATE OR REPLACE` / `COPY`-overwrite, so the feed is a **full snapshot** — the correct pattern is **truncate-and-replace per table within a transaction, stamped with a `build_version`** (source FY-load id + pipeline git SHA). No incremental/upsert path exists or is needed. Because `tier_top_transactions.rank` is recomputed every build (`row_number over risk_score DESC, amount DESC`), it is **not a stable cross-build key** — request that `transaction_id` (it exists in `tx_tiered`, dropped from the shipped CSV) be added back for period-over-period diffing.

**The one non-deterministic node:** `mart/vendor_verdicts.csv` (~60 rows, hand-curated) is LEFT-JOINed everywhere. A refresh recomputes tiers correctly but leaves `verdict`/`public_context` **blank for any newly flagged vendor** until a human curates it. Any "fresh verified leads" promise is gated on this manual step. Conversely, a **verdict-only update** needs only `apply_crosswalk.sql` + `write_feed.sql` (+ `_make_browser.py`) — a cheap path that does **not** require re-tiering.

**Cadence:** annual full rebuild aligned to the OpenBooks fiscal-year publication; ad-hoc verdict-only re-export when verdicts change.

**Multi-state generalization (the platform play):** the core is state-agnostic, bounded by **one interface** — a fixed canonical schema (`gen_projections.py` `CANON`) surfaced as the `transactions` view. Arizona alone needed 4 physical layouts (`base38 / full57 / fy2021 / fy2017_18`) reconciled to that one schema, proving the adapter pattern. A new state needs: (1) a projection mapping its raw export to `CANON` (absent columns → `CAST(NULL)`, self-suppressing markers); (2) a `convert_all.sh` JOBS list; (3) **calibration** of the `category_risk_multiplier` map (keyed to AZ category strings — unmapped categories fall through to `1.0`); (4) re-tuning the AZ-specific benign allowlists (`benign_payee / benign_approp / is_igov` macros, person-name regex). These last two are **mandatory gated calibration steps**, not an automatic port — the 88-agent verification pass was corpus-specific.

---

## 8. Phased integration plan

| Phase | Scope | Artifacts consumed | Dependencies | Milestone | Effort |
|---|---|---|---|---|---|
| **0 — MVP: static feed + embedded browser** | Host `tier_browser.html` behind auth; bulk-load the 7 feeds via `_tier_feed_bundle.jsonl` (+ crosswalk from its own CSV); surface docs as the methodology pane | `tier_browser.html`, `_tier_feed_bundle.jsonl`, `tier_entities_nested.json`, `tier_entity_crosswalk.csv`, `feed_annotations.csv`, docs | Static host + RFC-4180 loader — nothing else | A bondholder logs in, opens the vendor browser, drills a vendor, reads methodology — full story, zero custom UI | ~1 eng-week |
| **1 — Native screens off relational tables** | The five screens (§5) from the flat grains; reviewer action queue = Tier-1 filtered to `verify_status ∈ {genuine_review, screened_unreviewed}`; crosswalk-backed merge explainer | all 8 tier CSVs + nested JSON | Phase 0 loader; a relational store (Postgres/DuckDB/SQLite — schema is flat, join-free) | Agency trend FY2016–2025; live action queue | ~3–5 eng-weeks |
| **2 — Refresh automation + new FY/state** | Wrap the `sql/` DAG in one orchestrator; parametrize the load dir; define the human-in-the-loop verdict gate; per-state adapter + calibration | full `sql/` + `parquet/` + `warehouse.duckdb` + `mart/vendor_verdicts.csv` | **Internal backbone (request before starting)**; DuckDB; Python 3; new-FY extract | Drop a new FY parquet, run one command, feed + browser regenerate | ~2–4 eng-weeks (AZ); +1–2 wk/state |
| **3 — Read API + alerting** | Thin read API over `warehouse.duckdb` (entity / agency+trend / tier-query / txn-detail, returning the node shape); cross-refresh diff of `tx_tiered` → new Tier-1, verdict transitions, band crossings; keyed on the reversible crosswalk for stable identity | Phase 1 store + Phase 2 diffs; `tier_detail.parquet`, `tx_tiered` | Phases 1+2; a scheduler | A webhook fires when a refresh introduces a new `genuine_review` Tier-1 lead | ~3–5 eng-weeks |

- **Smallest end-to-end MVP:** Phase 0 — host the self-contained browser + one-pass bundle load. Delivers the headline product in ~1 week with near-zero custom code.
- **Critical path:** the **verification overlay**, not the SQL. The deterministic chain recomputes instantly; *new verified leads* are gated on the manual `vendor_verdicts.csv` curation step. Phases 0–1 are fully unblocked **today** from the shipped feed alone. Phases 2–3 require **requesting the internal backbone** (`parquet/`, `warehouse.duckdb`, `sql/`) — schedule that handoff before Phase 2 begins.

---

## 9. Open questions / decisions for Overtaker

1. **Static feed vs live API.** Ship the static drop (Phase 0/1) now, or stand up the read API (Phase 3)? The same DuckDB tables back both, so this is a sequencing call, not an architectural fork — but the API requires the internal backbone.
2. **Document vs relational store.** Recommended hybrid: 8 relational tables for the CSVs (one star) **plus** the nested JSON as a single document/JSONB blob (refreshed wholesale, never shredded). Confirm your store supports both.
3. **How to surface verdicts.** Decide the chip vocabulary and — critically — how `screened_unreviewed` (the 651-txn majority) renders. It **must** get a visible "machine-screened, not reviewed" badge; blanks must read "unreviewed," never "clean." Also decide whether the default vendor view *gates* emphasis to verified/curated leads while keeping the rest inspectable-but-labeled (recommended).
4. **One canonical tier vocabulary.** Reconcile before rendering: `tier_distribution` uses tiers **1–5** (5 = Unranked); the nested JSON carries `top_tier ∈ {1,2}` only; the narrative describes Tier 1–4 + Unranked. Fix one mapping so counts reconcile across the waterfall, agency card, and explorer.
5. **`transaction_id` for diffing.** Request it be added back to `tier_top_transactions` (it exists upstream, dropped from the CSV) — without it, period-over-period diffing of the marquee list is unreliable since `rank` is recomputed every build.
6. **Multi-state roadmap.** Is Arizona a one-off or instance #1 of a platform? If the latter, formalize now: the canonical-schema ingestion contract, per-state calibration as a gated step (category map + benign allowlists separated from marker logic into config), `model_version` + `category_profile_id` stamped on every output, and a governed adversarial-verification re-run per new corpus.
7. **Curation ownership.** `vendor_verdicts.csv` is the only non-deterministic node and the product's differentiator. Who owns the human-in-the-loop curation gate in the refresh runbook, and at what cadence?

---

*Disclaimer that travels with this feed: every tier, marker, and flag is a **lead warranting confirmation, not a finding of fraud, misconduct, or illegality. No individual or named entity is accused.** Figures are cash-basis checkbook data and differ from audited GAAP; the ACFR governs. This is analytics, not a credit rating or investment advice, and describes State of Arizona spending — not the credit of any locality or vendor.*
