# High-Value Tiering — Findings
### Arizona state checkbook, FY2016–FY2025 · for overtaker.ai

> **Framing.** Everything below is a **lead warranting confirmation**, never a finding of fraud or wrongdoing.
> No named entity is accused. Tiers are a *screening* priority built from source fields and reproducible from
> the warehouse (`TIERING_METHODOLOGY.md`). Cash-basis; the ACFR governs.

## Bottom line

Of 306,604 high-value transactions (≥ $100K, **82.6 % of all state spending**), the model isolates a tight
**Tier 1 of 1,094 items ($5.1B)** as top review priority and parks **$304B of large-but-clean scheduled
flows in Tier 4** — exactly the separation a bondholder needs. After an 88-agent adversarial verification pass
that retired the markers firing on accounting *convention* rather than behavior, the surviving leads are
discretionary, concentrated, often sole-source spend to **named private parties** — and several map directly
onto Arizona's most public spending stories.

## The marquee leads (verified genuine — confirm before relying)

| Entity | Agency | Exposure | Tier | What the data shows | Public salience |
|---|---|--:|:--:|---|---|
| **Fondomonte Arizona LLC** | Land Department | $7.0M | 1 | one sole-source, round $7M **uncontracted** WARRANT from *State & Local Fiscal Recovery Funds* (ARPA); the vendor's only transaction in the corpus | Saudi (Almarai) alfalfa grower on AZ state land; cheap leases + unmetered groundwater drew national scrutiny and lease cancellation |
| **AshBritt Inc** | Emergency & Military Affairs | $194.7M | 1 | the single largest genuinely-new, dominant, sole-source vendor in the data — all FY2023, all from the **Border Security Fund** | Florida disaster contractor that built the 2022 shipping-container border wall under a no-bid emergency contract (later removed) |
| **NaphCare Inc** | Corrections | ~$1.4B contract | 1 | one documented statewide **inmate-healthcare** contract (CTR060508); most markers down-weighted as contract mechanics | Feb-2026 **federal receivership** over AZ prison healthcare with power to renegotiate/terminate the contract — review-worthy beyond the data |
| **Active Resource Mgmt LLC** | Water Resources | $12.5M | 1 | one round, sole-source ACH from the **Water Banking Authority**; the vendor's only transaction | Vidler-owned water co.; sale of 50,000 long-term storage credits — a water-rights asset purchase |
| **AZ Jewish Historical Society** | Education | $7.0M | 1 | one-off concentrated FY2025 legislative earmark, named *Holocaust Education* | $7M state appropriation toward the Phoenix Holocaust Education Center |
| **Kinella Construction LLC** | Senate / House | $5.8M | 1 | discretionary, concentrated, exact-round Legislature building work under *Operating Lump Sum* | Phoenix-area GC; Legislative-branch construction |
| **Weil Gotshal & Manges LLP** | Administration | $1.5M | 1 | round, one-time, off-contract June disbursement from the self-insured **risk-management** fund | AmLaw-100 firm — consistent with a litigation/settlement engagement |

A persistent **transparency texture** also recurs: large dollars flow to redacted/individual/one-shot payees
("NAME REDACTED," settlement-fund law firms, restitution) the published checkbook does not name. Best handled
as a single disclosure-completeness note, not per-item leads.

## Where the high-tier exposure concentrates (entities)

| Agency | Tier-1 # | Tier-1 $ | Tier-1+2 % of its high-value spend | Driver |
|---|--:|--:|--:|---|
| Dept of Transportation | 398 | $2,292M | 13.1 % | freeway design-build JVs, federal-aid capital |
| Dept of Corrections | 142 | $1,205M | 25.2 % | inmate healthcare (NaphCare), private-prison per-diem |
| Dept of Emergency & Military Affairs | 25 | $215M | 22.1 % | Border Security Fund (AshBritt) |
| Dept of Health Services | 80 | $248M | 6.0 % | COVID public-health emergency procurement |
| Dept of Water Resources | 25 | $97M | 34.9 % | water-banking credit purchases |

Top programs by Tier 1+2 exposure: **Federal Aid** (DOT, $1.78B), **Inmate Health Care Contracted Services**
($1.13B), **Loop-202/RARF freeway construction** ($467M), **Private Prison Per Diem** ($308M), **Border
Security Fence & Technologies** ($296M), **Public Health Emergencies** (COVID, $263M).

*Two entity-level readings warrant a caveat rather than a flag:* **ASRS** shows a high Tier-1+2 share (82 %)
because a pension/investment agency books large manual-rail movements — structurally expected, not procurement
risk; and a **"no appropriation"** cluster at DPS reflects the FY2019–20 system-wide appropriation blackout,
not missing authority.

## What the big dollars actually are (correctly demoted)

The largest flagged values are **benign-by-structure** and were demoted out of the high tiers by the
verification fixes: the State's self-insured employee-health-benefits trust (UnitedHealthcare, BCBS at ADOA),
AHCCCS Medicaid managed-care capitation, PSPRS/ASRS pension paydowns, Lottery prize-fund sweeps to Bank of
America, ADOT highway-construction JVs, and university operating lump sums. These are dependency/scheduled
flows, not irregularities — surfaced in Tier 4 ("high value, clean") so they remain visible without crowding
out the genuine leads.

## How to use this in Overtaker

- **`tier_agency_scorecard`** — per-agency entity card: high-value exposure, Tier-1/2 counts & $, the share of
  an agency's discretionary spend under elevated scrutiny, and its dominant markers.
- **`tier_agency_year`** — the bondholder time-series: is an agency's high-tier exposure rising?
- **`tier_vendor_flagged`** — the flagged-vendor list with verification verdicts and public context.
- **`tier_top_transactions`** — the 1,094-item Tier-1 leaderboard with markers and verification status.
- **`tier_interesting_entities.md`** — the curated marquee shortlist + headline findings (this is the
  "most interesting data at a high level").
- **`tier_program_summary`** — appropriation/program concentration.

Every figure is reproducible from the warehouse; treat each item as a lead to confirm, never an accusation.
