# Arizona OpenBooks — First-Pass Forensic Findings

**Prepared:** 2026-05-26 (overnight autonomous run)
**Scope:** 10 fiscal years — FY2016–FY2025 (FY2022 recovered 2026-06-16; both shards downloaded from the Arizona Open Data Portal)
**Corpus:** 115,699,297 transaction rows across ~74 GB of source CSV, converted to ~2.0 GB of typed Parquet
**Engine:** DuckDB 1.5.3 over `~/Documents/Openbooks/parquet/`, summary tables in `warehouse.duckdb`

> **READ THIS FIRST — framing.** Everything below is **leads warranting review, not findings of fraud.** Statistical anomalies have many innocent explanations (new contracts, federal pass-throughs, legitimate recurring payments, data-entry conventions). Nothing here names a wrongdoer, and nothing should be published or acted on as an accusation without human investigation of the underlying transactions. This is a *screening* pass to decide where a human auditor should look — and, just as importantly, a validation that the data pipeline produces trustworthy numbers.

---

## Bottom line

- **The screens that bite found real exposure.** Repeated identical payments (same vendor + reference ID + amount, distinct transaction IDs) total **431,218 groups / ~$4.67B**. This is *ambiguous, not exonerated* — the same pattern describes both legitimate batch disbursements and double-payments, and the data here can't tell them apart without warrant/payment-number reconciliation. It is under-investigated and is the headline item.
- **The "clean" screens leaned on the most forgiving tests.** First-digit Benford and ±5% threshold windows are the weakest fabrication detectors; their passing is weak evidence of nothing, not strong evidence of integrity. Treat "clean" here as "this particular weak test didn't fire," not "no problem."
- **Several specific leads need a human** (below). Where the data explains an anomaly (e.g. a named appropriation), that explanation is stated — but only after checking, not assumed.
- **Data-quality issues materially distort totals** and must be fixed before any public reporting.

> **Method note / bias correction.** An earlier draft of this report attached benign explanations ("likely COVID relief," "probably legitimate") to large anomalies *without checking them*. That was confirmation bias. Every explanation below is now grounded in the transaction's own appropriation/reference fields, and items that remain genuinely open are left open rather than alibi'd.

---

## Leads worth reviewing (ranked)

### 1. Dept of Veterans Services — just-under-$5K clustering
The only agency with a notably elevated just-under-threshold ratio: **1,301 expenditures in $4,750–$4,999.99 vs 736 just above $5,000 (1.77×)**. Every other agency sits ~1.0–1.2×. Could be benign (a real $5K approval cutoff producing legitimate sub-threshold purchases), but it's the single clearest structuring lead in the corpus. *Suggested check: pull these transactions, group by buyer/card-holder and vendor, look for repeated splits to the same vendor within days.*

### 2. Systemic repeated-payment exposure — $4.67B, the largest open item
**431,218 groups** share the same `vendor_id_code` + `transaction_reference_id` + `amount` across **distinct `transaction_id`s**; the duplicate-side value totals **~$4.67B**, of which **421,408 groups fall within a 7-day window** and 687 exceed $100K per group. This is far larger than the $310M invoice-level figure quoted earlier (which was artificially capped to the four years carrying `invoice_number`).

**It is genuinely ambiguous and that ambiguity is the finding.** The largest groups are *same-day* repeats — PSPRS pension paydown (`VC0000023979`, ten identical $99M lines, 0-day span) and Medicaid managed-care capitation (`00110007`, references like `GAXNEHCAPMMS*`). Same-day identical tranches under one reference are the normal shape of a *batch disbursement*, so the same-day mass is probably not double-payment. **The sharper concern is the different-day repeats** — identical vendor + reference + amount posted days apart under separate transaction IDs, which is the classic double-payment signature.

*Concrete example:* FY2018 `MERCY CARE PLAN` (`00010306`), reference `GAXHCAPMMS18041289`, **$35,857,549.22 posted on 2017-10-04 and again on 2017-10-05** — same reference, same amount, two EFTs, one day apart. Could be two months' capitation booked together; could be a double-pay. The data alone can't say.

*Required to resolve (cannot be done with current columns): reconcile against the warrant/payment number (`payment__`) and bank settlement. Recommend triaging the `day_span > 0`, `amount ≥ $100K` subset first.*

### 3. Largest year-over-year vendor jumps — checked, not assumed
Several real vendors leapt 10×–40,000× in one year. Rather than guess, here is what each one's appropriation/payee actually says:

| Vendor ID | FY | Spike | What the data says | Status |
|---|--:|--:|---|---|
| VC0000023979 | 2021 | $1,108M | PSPRS — appropriations `PSPRS DPS / CORP CORRECTIONS UNFUNDED LIABILITY REDUCTION` ($500M + $500M) | **Explained** (pension paydown — *not* COVID, as earlier claimed) |
| IV0000033445 | 2024 | $59.8M | Town of Queen Creek — appropriation `QUEEN CRK-EXT ST RTE 24 ... INTERCHANGE` | **Explained** (SR-24 highway capital grant) |
| IV0000002963 | 2025 | $505.6M | reference `GAXMAA*`; also appears in the duplicate-signature set above | **Open** — not yet traced |
| IV0000009938 | 2023 | $385.9M | not yet traced | **Open** |

*Two of the four resolved cleanly once the appropriation was actually read; two remain open. The lesson is the method: read the appropriation/reference, never infer the reason.*

---

## Screens that did not fire (weak tests — absence of signal ≠ absence of problem)

These are the *least* sensitive fraud tests. Their not firing rules out only crude, whole-dataset manipulation; it says nothing about targeted schemes, which hide below this resolution (per-agency × per-category × per-vendor, or in payment timing). Do not read "clean" as "audited."

| Screen | Result |
|---|---|
| **Benford's Law (first digit), aggregate** | Close fit; only mild leading-1 excess (+2.45 pts). Normal. |
| **Benford per agency (MAD)** | All agencies ≤ 0.0119, below the 0.015 nonconformity threshold. No agency flagged. |
| **Just-under-threshold ($5K/$10K/$25K/$50K/$100K), aggregate** | Under/over ratios ~1.02–1.04. No structuring at the corpus level. |
| **Round-number excess** | 3.83% whole-dollar — normal. |
| **Weekend/off-hours postings** | 0.28% — consistent with an automated accounting system. |

---

## Data-quality issues (must fix before public reporting)

These affect the **accuracy of any published total** and are the highest-priority follow-ups.

1. **`99999999.xx` sentinel amounts — exclude from all totals.** Placeholder values appear every year (17–100 lines/yr), summing to **~$19.7B** of fake money across the corpus. They are already excluded from the forensic screens above; they must also be excluded from any headline spending figure. (Mechanism: `abs(amount) BETWEEN 99999999 AND 100000000`.)

2. **FY2021 — 69,917 rows dropped (0.57%)** and **FY2018 — 10,350 rows (0.08%)** during conversion, due to malformed CSV rows with unescaped embedded double-quotes (e.g. `OFC DEPOT 1/2" RING BINDERS`). FY2018's loss is negligible; **FY2021's 0.57% should be recovered** (custom quote-repair pass) before FY2021 totals are published, since the dropped rows may not be randomly distributed.

3. **`hourly_rate` is 100% empty** across all years. The column exists in the schema but is never populated, so payroll pay-rate analysis and rate-based ghost-employee detection are **not possible** with this data. (Payroll review would have to rely on `position_title` + payee patterns instead.)

4. **`vendor_id_code` sentinels** `00000000000000000000` and `MISCPAYVEND` are catch-all placeholders, not real vendors — excluded from all vendor-based analysis here.

5. **`vendor_id_code` only exists FY2017+**; `invoice_number` only FY2016–2018 & FY2021. Duplicate-payment and vendor-ID analysis is therefore **not uniform across years**.

6. **FY2016 had a truncated 38-column header over 59-column data** (recovered by positional mapping). **FY2022** was initially missing its first shard (FY22_000); both shards were recovered on 2026-06-16 from the Arizona Open Data Portal and are now included (13.1M rows total).

---

## What's built and reusable

- `parquet/transactions_FY*.parquet` — typed, canonical 60-column schema (DECIMAL amounts, real DATEs, cleaned NULLs), one file per source.
- `warehouse.duckdb` — `transactions` view (header-leak-guarded) + summary tables `spend_by_month`, `vendor_summary`, `agency_summary`.
- `sql/` — `gen_projections.py` (schema-variant projection generator), `convert_all.sh` (resumable converter), `build_warehouse.sql`, `forensics.sql`, `forensics_results.txt`.

## Recommended next steps

1. **Refine duplicate detection** — exclude placeholder invoice numbers; require same invoice + amount + *different* transaction_id within a short window before flagging.
2. **Recover FY2021's dropped 0.57%** with a quote-repair preprocessing pass.
3. **Pull the Lead #1 and #2 transactions** for human review.
4. **Fuzzy vendor-name matching** (Python/rapidfuzz) to catch shell-company / near-duplicate vendor variants — not yet run.
5. **Stand up the reporting layer** (Quarto documents + Evidence.dev dashboard) on the warehouse, with the sentinel exclusion baked into every total.
