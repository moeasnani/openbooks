# High-Value Transaction Tiering — Methodology
### Arizona state checkbook · for overtaker.ai (institutional analytics for municipal bondholders)

**What this is.** A reproducible, auditor-style **scrutiny-tiering** of the State of Arizona's high-value
disbursements. Every expenditure ≥ $100,000 (the 306,604 transactions that carry **82.6 % of all state
spending**) is scored on a documented set of forensic markers and assigned a **Tier 1–4** review priority.
It tells a bondholder *where to look first* and surfaces the handful of genuinely notable items inside a
$476 B, 92.8 M-row corpus.

**What this is not.** Not a credit rating, not an audit, not an accusation. Every tier and every marker is a
**lead warranting confirmation** — never a finding of fraud, misconduct, or illegality, and no named entity
is accused. Cash-basis checkbook figures differ from audited GAAP statements; the State ACFR governs. Use
alongside the existing `ASSESSMENT_METHODOLOGY.md` (federal dependency, governance scorecard, AG cross-reference).

---

## 1. Population & stratification

Auditors stratify by materiality: a small number of transactions carry most of the dollars, and those get
100 % examination. The Arizona checkbook follows that shape exactly (clean expenditures, sentinels excluded):

| Threshold | # txns | Exposure | % of all spend |
|---|--:|--:|--:|
| ≥ $100K | 306,604 | $393.0B | **82.6 %** |
| ≥ $1M | 45,853 | $322.5B | 67.8 % |
| ≥ $10M | 7,419 | $210.5B | 44.2 % |
| ≥ $100M | 86 | $18.7B | 3.9 % |

The model scores the **≥ $100K population**. "Clean" = `transaction_type='EX'`, `amount > 0`, and the
`99999999.xx` sentinel placeholders excluded (per the corpus methodology).

---

## 2. How the model was built (pipeline)

1. **Feature mart** — every ≥ $25K expenditure enriched with audit features (magnitude band, round-number
   flags, vendor-year totals & first-appearance, vendor share-of-agency, appropriation-year totals,
   duplicate-payment signatures, agency × category robust-outlier z-scores). `mart/hv_base.parquet`.
2. **Auditor strategy panel (13 agents)** — twelve specialist forensic-auditor agents each *empirically
   calibrated* one marker family against the mart (not designed in the abstract), with hard guardrails against
   the known "Aztec → ZTE" substring false-positive class; a chief synthesizer combined them into a scoring model.
3. **Implementation** — the calibrated predicates assembled into one recomputable SQL model (`sql/tiering.sql`).
4. **Adversarial verification (88 agents)** — 60 vendor-verdict agents, 15 accountability-cluster reviewers,
   and 12 per-marker red-team agents stress-tested the output under the discipline *"read the appropriation,
   never infer."* Their fixes produced the final model (`sql/tiering_v2.sql`), which **retired or tightened
   every marker that fired on data structure rather than behavior** (§5).

---

## 3. The two-axis tier model

A transaction's **Tier** is a function of two independent, recomputable axes — magnitude and behavior — so a
large amount *alone* can never manufacture a high tier, and a behavioral cluster on a small amount is not
over-ranked.

### Axis A — `risk_score` (behavioral markers)

`risk_score = marker_sum × category_risk_multiplier`

**`marker_sum`** = the sum of **ten marker families**, where each family contributes **only its single
strongest firing tier** (so over-firing within a theme is impossible), after cross-family de-duplication:

| Family | Strongest markers (severity) | What it screens for |
|---|---|---|
| **Round / negotiated** | round_1m (5) · round_100k (4) · whole_dollar (3) | estimated/negotiated amounts, not invoice-derived |
| **Duplicate** | true_duplicate (4) | same vendor + amount across ≥2 *distinct* documents, different days |
| **Concentration** | sole_source ≥20 % (4) · dependency ≥33 % (2) | vendor dominates an agency's discretionary spend |
| **New vendor** | dominant_new (5) · short_tenure (4) · large_first (3) | first-appearance vendor (by **name**) winning big money |
| **Year-end** | period13_disc (4) · june_round (3) | closing-entry / use-it-or-lose-it timing |
| **Peer outlier** | peer_outlier (4) | amount dwarfs the agency × category norm (robust-z ≥ 20) |
| **Accountability** | placeholder_payee (4) · no_contract (2) · offcontract (2) | external-rail blank payee, off-contract procurement |
| **Manual rail** | manual_rail_disc (4) · manual_rail (3) · nonstd_rail (2) | JOURNAL VOUCHER / INTERNAL bypassing normal AP |
| **Entity / name** | person_name_payee (3) · masked_payee (3) | individual-looking or masked payee on discretionary spend |
| **(retired)** | ~~agency_benford~~, ~~triple_gap~~ | removed after verification (§5) |

**`category_risk_multiplier`** re-weights the same dollar by spend type (auditors scrutinize discretionary
procurement more than scheduled entitlement). Documented map: DEBT SERVICE 0.3 · AID / TRANSFERS OUT 0.4 ·
COST ALLOCATION / CONVERSION 0.5 · PERSONAL SERVICES 0.7 · EMPLOYEE-RELATED / FOOD 0.9 · TRAVEL / NULL 1.0 ·
NON-CAP & CAP EQUIPMENT 1.2 · CAPITAL OUTLAY 1.3 · OTHER OPERATING 1.4 · **PROFESSIONAL & OUTSIDE SERVICES 1.5**.

Risk bands: **R0** = 0 · **R1** 0–3 · **R2** 3–6 · **R3** 6–10 · **R4** ≥ 10.

### Axis B — magnitude class

`MB_HIGH` = amount ≥ $1M · `MB_LOW` = $100K–$1M.

### Tier matrix (recompute with one CASE)

| Tier | Rule | Meaning |
|---|---|---|
| **Tier 1** | MB_HIGH **and** (R3 or R4) | top review priority — material **and** elevated markers |
| **Tier 2** | (MB_HIGH and R2) **or** (MB_LOW and R3/R4) | material+moderate, or smaller+elevated |
| **Tier 3** | any remaining row with risk_score > 0 | screening queue |
| **Tier 4** | MB_HIGH and R0 | **high-value but clean** (scheduled mega-flows) |
| Unranked | MB_LOW and R0 | small and clean |

A bondholder reproduces any tier from three source fields: `amount` (→ magnitude band), `category1`
(→ multiplier), and the fired markers (→ marker_sum).

### Final distribution (306,604-txn ≥ $100K population)

| Tier | # txns | % | Exposure |
|---|--:|--:|--:|
| **Tier 1** | 1,094 | 0.36 % | $5.1B |
| **Tier 2** | 7,034 | 2.29 % | $6.8B |
| **Tier 3** | 30,526 | 9.96 % | $15.7B |
| **Tier 4** (clean) | 40,918 | 13.35 % | $304.3B |
| Unranked | 227,032 | 74.05 % | $61.2B |

87.3 % of high-value transactions score zero markers. Tier 1 is a tight 0.36 % — the items a reviewer should
actually open. Tier 4 deliberately holds the **$304 B of large-but-clean scheduled flows** (Medicaid
capitation, pension paydowns, inter-fund transfers, debt service) so they are visible but not mistaken for risk.

---

## 4. Data-quality limits (markers degrade gracefully — they never penalize a system-wide-absent field)

- **FY2019 & FY2020** carry NULL `appropriation_type`, `fiscal_period`, and `vendor_id` system-wide. Vendor,
  appropriation, and period markers cannot run there; appropriation-based benign suppressions are replaced by
  payee-name structural guards in those two years.
- **`vendor_id`** is present on only ~24–48 % of rows even in good years (42.7 % of the ≥ $100K population has
  none); vendor markers require a real id and simply do not fire otherwise.
- **`contract_number`** is populated on only ~2–6 % of rows. The off-contract markers use a *structural
  dark-switch*: they do not fire for any agency-year that populates `contract_number` on 0 % of its rows
  (universities, ASRS, courts, ADOT capital), where the field is uninformative.
- **FY2022** is included (both shards recovered 2026-06-16). **`hourly_rate`** is 100 % empty (no pay-rate analysis).

---

## 5. Adversarial verification — what changed, and why the model is trustworthy

The 88-agent verification pass is the discipline this product depends on. It cut Tier 1 from 3,146 → 1,094
(−65 %) by removing markers that detected **data conventions, not behavior**:

| Fix | Evidence | Action |
|---|---|---|
| **`agency_benford` retired** | tagged ~100 % of an agency's rows → cannot localize a lead | removed from scoring |
| **`triple_gap` retired** | 8,016/8,016 rows 100 % redundant with placeholder_payee; fires only on internal rails | removed |
| **`placeholder_payee` gated** | 99 % of fires were internal-accounting entries (INTERNAL/JV) that have no external payee by design | now fires only on external rails (ACH/WARRANT/PAYMENT) |
| **New-vendor resurfacing guard** | ~70–80 % of FY2021 "new vendors" were old payees re-surfacing after the FY2019–20 vendor-id blackout / FY2021 id re-numbering | a vendor must be new by **payee name**, not just by id |
| **`person_name` tightened** | misfired on PITNEY BOWES (170×), RR DONNELLEY, STATE TREASURER | added suffix/govt exclusions + a ≤3-transaction recurrence test (companies recur; one-off individuals don't) |
| **`duplicate` redefined** | 95–97 % of old "duplicates" were one invoice split into accounting lines under one document | now requires ≥2 *distinct* `transaction_reference_id`s |
| **`nonstd_rail` fixed** | plain WARRANT is the Treasurer's standard external check rail (103,506 rows) | WARRANT dropped; only NULL-method discretionary remains |
| **benign-by-structure guards** | banks (Lottery), benefits carriers (UnitedHealthcare/BCBS), pension (PSPRS/ASRS), debt service, risk-mgmt | suppressed across round / peer / duplicate / concentration markers, by appropriation **and** by payee (the latter to reach NULL-appropriation FY2019–20) |

All 15 reviewed **accountability-gap clusters** (~$5B) resolved to a **systemic accounting convention** —
null/placeholder payees on internal rails for inter-fund, benefits-trust, Medicaid, and pension postings —
best reported as a single agency-level disclosure-completeness note, not thousands of per-item leads.

After these fixes the seven genuine, publicly-salient leads all survive in Tier 1 (Fondomonte, AshBritt,
Active Resource Management, the Holocaust-center earmark, Kinella, Weil Gotshal, and a tightly-scoped set of
discretionary sole-source items); the benign mega-clusters were correctly demoted to Tier 4 / Unranked.

---

## 6. Reproducibility & refresh

- `sql/build_feature_mart.sql` → `mart/hv_base.parquet` (feature mart)
- `sql/tiering_v2.sql` → table `tx_tiered` (final per-transaction tiers)
- `sql/build_rollups.sql` → `tier_agency_summary`, `tier_agency_year`, `tier_vendor_flagged`, `tier_program_summary`, `tier_top_transactions`
- `sql/write_feed.sql` → `overtaker_handoff/feed/tier_*.csv` + `_tier_feed_bundle.jsonl`

Re-run the chain against refreshed checkbook data to regenerate. Every threshold in this document is a literal
constant in those scripts; nothing is a black box.

## 7. Standing disclaimers

1. Tiers and markers are **leads requiring confirmation**, not assertions of fraud, misconduct, or illegality;
   no individual or named entity is accused.
2. Cash-basis checkbook figures are proxies and differ from audited GAAP statements; the ACFR governs.
3. FY2022 is absent and FY2019–2021 carry the documented field gaps above; affected markers self-suppress.
4. This is analytics, not a credit rating or investment advice.
