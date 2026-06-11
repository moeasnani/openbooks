export const meta = {
  name: 'overtaker-integration-brief',
  description: 'Draft an integration-ready platform brief for Overtaker from multiple expert lenses (data-eng, product, compliance, rollout, API), grounded in the exact current artifacts, then synthesize one document',
  phases: [
    { title: 'Lenses', detail: '5 specialists each draft the integration brief from one lens' },
    { title: 'Synthesize', detail: 'chief solutions architect composes the unified integration brief' },
  ],
}

// ===== GROUND TRUTH — exact current state of the deliverable (no invention) =====
const FACTS = `
WHAT WAS BUILT (factual inventory; describe ONLY what exists here):

PRODUCT. An analytics layer over the State of Arizona OpenBooks checkbook (FY2016-2025; FY2022
excluded), for overtaker.ai — institutional analytics for MUNICIPAL BONDHOLDERS. Two integrated
components:
 (A) FISCAL-GOVERNANCE ASSESSMENT (prior): federal-revenue dependency, a banded governance
     scorecard, procurement flags, OFAC/Section-889 vendor screen, and an Arizona Auditor-General
     cross-reference.
 (B) HIGH-VALUE TRANSACTION TIERING (new): every expenditure >= $100,000 (306,604 txns = 82.6% of
     all $476B state spending) scored on 10 forensic marker families and assigned a Tier 1-4 review
     priority, then rolled up to vendors / agencies / programs, with a vendor-first drill-down UI.

CORE FRAMING (NON-NEGOTIABLE, must survive integration): every tier/marker/flag is a "LEAD WARRANTING
CONFIRMATION," NEVER a finding of fraud/wrongdoing; no entity is accused. Cash-basis checkbook != audited
GAAP (the ACFR governs). These disclaimers must travel with any surfaced item.

THE TIERING MODEL (fully recomputable, no black box):
 risk_score = marker_sum x category_risk_multiplier. marker_sum = sum over 10 marker families, each
 contributing only its single strongest fire, after cross-family de-duplication. category_multiplier
 re-weights by spend type (DEBT/AID/TRANSFERS 0.3-0.5 ... PROFESSIONAL SERVICES 1.5). Tier = f(magnitude
 band, risk band): Tier1 = >=$1M AND elevated markers (top priority); Tier4 = high-value but CLEAN
 ($304B of scheduled mega-flows, correctly parked); Unranked = small+clean. Reproducible from 3 source
 fields: amount, category1, fired markers.
 Built via: a 13-agent auditor calibration panel + an 88-agent adversarial-verification pass that
 RETIRED markers firing on accounting convention not behavior (cut Tier1 3,146 -> 1,094). Final:
 Tier1 = 1,094 txns / $5.05B; 87% of high-value txns score zero markers.

DELIVERABLE = a self-contained folder 'overtaker_handoff/' (7.7 MB). Contents:

 DOCS (markdown): MANIFEST.md (index), TIERING_METHODOLOGY.md, TIERING_FINDINGS.md,
   ASSESSMENT_METHODOLOGY.md, FINDINGS_REPORT.md.

 FEED — high-value tiering (CSV, exact columns):
  - tier_entities.csv (1,700 rows; grain = parent vendor): entity_name, names_merged, n_vendor_ids,
    hv_txn, hv_exposure, n_flagged, flagged_exposure, n_tier1, usd_tier1, n_tier2, top_tier,
    max_risk_score, first_year, last_year, n_agencies, agencies, top_markers, verify_verdict,
    overtaker_interest, public_context, entity_key.
  - tier_agency_scorecard.csv (108 rows; agency): agency, cabinet, hv_txn, hv_exposure, n_tier1,
    usd_tier1, n_tier2, usd_tier2, n_tier3, n_flagged, tier12_exposure, tier12_pct_of_hv,
    avg_risk_score, max_risk_score, distinct_flagged_vendors, top_markers.
  - tier_agency_year.csv (712 rows; agency x year): agency, fiscal_year, hv_txn, hv_exposure, n_tier1,
    usd_tier1, n_tier12, usd_tier12, tier12_pct_of_hv, avg_risk_score.  (the time-series for trend.)
  - tier_vendor_flagged.csv (901 rows; raw vendor_id): vid, payee, hv_exposure, flagged_exposure,
    n_flagged, n_tier1, usd_tier1, n_tier2, top_tier, max_risk_score, agencies_served, top_agency,
    first_year_seen, peak_agency_share, top_markers, verify_verdict, overtaker_interest,
    recommended_action, public_context.
  - tier_top_transactions.csv (1,094 rows; transaction): rank, fiscal_year, posting_date, agency,
    category1, payee, appropriation_1_name, amount, risk_score, markers, verify_status,
    overtaker_interest, public_context.
  - tier_program_summary.csv (138 rows; appropriation): appropriation, lead_agency, hv_txn,
    hv_exposure, n_tier1, tier12_exposure, max_risk_score, distinct_vendors.
  - tier_distribution.csv (50 rows; tier x year): fiscal_year, tier, n_txn, exposure.
  - tier_entity_crosswalk.csv (4,543 rows; raw name -> parent): entity_key, parent_key, parent_name,
    method (norm_exact/token_subset/token_jaccard), confidence, group_size. (auditable, reversible.)

 FEED — JSON / interactive:
  - tier_entities_nested.json (840 vendor-first nodes): node keys {entity_name, names_merged,
    primary_agency, primary_cabinet, agencies[], n_agencies, n_ids, n_txn, n_tier1, n_tier2, exposure,
    usd_tier1, top_tier, max_score, fy0, fy1, top_markers[], verdict, overtaker_interest,
    public_context, transactions[]}; each transaction {fy, date, agency, cat, amount, score, tier,
    markers}. THIS is the canonical structure the UI renders.
  - tier_browser.html (993 KB, self-contained): reference UI — vendor-first list, expand to
    transactions, group-by toggle (Vendor/Agency/Cabinet/Tier/Verdict), optional filter, tier+verdict
    chips. Embeds the nested JSON; no server/build needed.
  - tier_interesting_entities.md: curated marquee shortlist + headline findings.
  - _tier_feed_bundle.jsonl (4,703 lines): every tier CSV as {feed,row} JSON lines (one-loader ingest).

 FEED — prior governance assessment (CSV): ff_federal_dependency_by_year (9),
  ff_federal_dependency_by_agency_year (986), gov_fiscal_panel (9), gov_scorecard (27),
  pf_duplicate_payment_groups (15,828), pf_structuring_by_agency (32), pf_vendor_concentration (140),
  pf_vendor_compliance (1), ag_findings_xref (8), feed_annotations.csv (19, a neutral note per table),
  _feed_bundle.jsonl.

 VERIFICATION OVERLAY: verify_verdict / verify_status fields carry adversarial-verification outcomes
  (genuine_review / mixed / explained_benign / false_positive_marker / screened_unreviewed) +
  overtaker_interest (1-5) + public_context. A reviewer filters Tier-1 to genuine_review/unreviewed
  for an action queue; explained_benign items carry their verified conclusion.

REPRODUCIBILITY / REFRESH BACKBONE (NOT in the shipped feed; internal, available on request):
  - parquet/ (2.4 GB typed transactions, source of truth) -> warehouse.duckdb (DuckDB; tables incl.
    tx_tiered=306,604, tier_detail, tier_entities, vendor_universe=127,858, ofac_sdn=18,712).
  - mart/hv_base.parquet (feature mart, >=$25K).
  - sql/ pipeline (deterministic, re-runnable): build_feature_mart.sql -> tiering_v2.sql ->
    build_rollups.sql -> resolve_entities.py (+ apply_crosswalk.sql) -> write_feed.sql -> _make_browser.py.
    Re-running against a new fiscal-year load regenerates the entire feed. Engine: DuckDB (embeddable,
    columnar, no server).

KNOWN DATA LIMITS (must be disclosed in integration): FY2022 absent; FY2019-2020 lack
appropriation/fiscal_period/vendor_id system-wide (affected markers self-suppress); contract_number
~2-6% populated; vendor_id present on ~24-48% of rows; '99999999.xx' sentinels excluded.
`;

const LENSES = [
  { key:'data_engineering', title:'Data-engineering / ingestion architect',
    brief:`Define the DATA CONTRACTS and INGESTION architecture. For each feed table: grain, primary key,
    join keys (how tier_entities <-> tier_entity_crosswalk <-> tier_vendor_flagged <-> tier_top_transactions
    relate; entity_key/parent_key/vid linkage). Recommend ingestion paths (the per-table CSVs vs the
    single _tier_feed_bundle.jsonl vs the nested JSON), target store shape (relational tables vs a
    document for the nested vendor tree), idempotent load + versioning, and the REFRESH pipeline (how a new
    fiscal-year load flows through the sql/ chain to regenerate the feed; cadence; DuckDB as the engine).
    Call out schema-stability guarantees and the data-limit caveats that must be encoded as flags.` },
  { key:'product_surface', title:'Product / analytics-surface designer',
    brief:`Map the assets onto BONDHOLDER-FACING SURFACES. What screens does this enable: an issuer/agency
    fiscal card (tier_agency_scorecard + tier_agency_year trend + federal-dependency + governance scorecard),
    a vendor-entity explorer (the vendor-first nested tree / tier_browser), a transaction drill-down, a
    program view, the curated "interesting entities" marquee. How the verification overlay (verdict chips,
    overtaker_interest) drives ranking/filtering. What the user's primary journeys are (screen an obligor ->
    see high-tier exposure -> drill to vendor -> read the lead + verified context). Note the reference
    tier_browser.html is an embeddable interim surface while native screens are built.` },
  { key:'compliance_framing', title:'Compliance / framing / risk counsel',
    brief:`Specify what MUST be enforced for a bondholder-facing product that names vendors. The "leads not
    findings, never an accusation" framing; the cash-vs-GAAP/ACFR-governs disclaimer; the data-limit
    disclosures; how verify_status must travel with every surfaced item and how to present
    false_positive_marker / explained_benign so a flag is never shown bare. Where disclaimers live in the
    UI, what gating (e.g. lead Tier-1 with verified context), and the audit/traceability story (every tier
    recomputable from source fields; the entity crosswalk is inspectable/reversible). Treat this as
    integration REQUIREMENTS, not nice-to-haves.` },
  { key:'rollout_plan', title:'Phased-rollout / delivery engineer',
    brief:`Produce a concrete, PHASED integration plan Overtaker can execute. Phase 0 (ingest the static
    feed + render the embeddable browser -> fastest value), Phase 1 (native entity/agency screens off the
    relational tables), Phase 2 (refresh automation + new-fiscal-year pipeline + multi-state generalization),
    Phase 3 (service/API + alerting). For each: scope, the specific artifacts consumed, dependencies,
    rough effort, and the demo-able milestone. Identify the critical path and the smallest end-to-end MVP.
    Be realistic and specific to the artifacts that exist.` },
  { key:'platform_api', title:'Platform / API & generalization strategist',
    brief:`Describe how this becomes a PLATFORM capability, not a one-off Arizona drop. The state-agnostic
    pipeline (DuckDB + sql/ chain) and what a second state (or municipality) would require. The eventual
    API/service surface (entity lookup, agency scorecard, tier query, transaction detail) vs the current
    static-feed model, and a migration path between them. The model-governance angle: versioning markers,
    re-calibration when new data lands, keeping the crosswalk current. Where overtaker_interest / verdicts
    could feed alerting or a watchlist.` },
];

const LENS_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['lens','summary','key_points','integration_recommendations','risks_or_caveats','must_haves'],
  properties:{
    lens:{type:'string'},
    summary:{type:'string', description:'2-4 sentence framing of this lens'},
    key_points:{type:'array', items:{type:'string'}, description:'the substantive content, grounded in the exact artifacts'},
    integration_recommendations:{type:'array', items:{type:'object', additionalProperties:false,
      required:['recommendation','artifacts_used','rationale'],
      properties:{recommendation:{type:'string'}, artifacts_used:{type:'string'}, rationale:{type:'string'}}}},
    risks_or_caveats:{type:'array', items:{type:'string'}},
    must_haves:{type:'array', items:{type:'string'}, description:'non-negotiables for THIS lens'},
  },
};

phase('Lenses');
const lenses = (await parallel(LENSES.map(l => () =>
  agent(`${FACTS}\nYOUR LENS: ${l.title}\n${l.brief}\n\nWork ONLY from the factual inventory above — do not invent
columns, files, or numbers. Be concrete and specific to the artifacts that exist. Return a structured brief
for your lens.`,
    { label:`lens:${l.key}`, phase:'Lenses', schema:LENS_SCHEMA, agentType:'general-purpose' })
))).filter(Boolean);
log(`${lenses.length}/${LENSES.length} lenses drafted`);

phase('Synthesize');
const brief = await agent(
  `${FACTS}

You are the CHIEF SOLUTIONS ARCHITECT. Five specialists each drafted the Overtaker integration brief from
one lens. Their structured outputs:

${JSON.stringify(lenses, null, 0)}

Compose ONE integration-ready brief that Overtaker's team can read and immediately build an integration plan
from. Audience: Overtaker's product + engineering leadership. Write in clear markdown. Required sections:
 1. Executive summary — what this platform is, in plain language, and what integrating it gives Overtaker.
 2. What you're receiving — the two components (governance assessment + high-value tiering) and the bundle.
 3. The data model & contracts — the feed tables with grain/keys and how they join; the canonical
    vendor-first nested JSON; the JSONL one-loader path. (A compact table is good.)
 4. How the tiering works (brief, integration-relevant) — recomputable score -> tier, the verification
    overlay, what each tier means.
 5. Integration surfaces — the bondholder-facing screens this enables and which artifacts feed each;
    the embeddable reference browser as an interim surface.
 6. Non-negotiable requirements — the "leads not findings" framing, disclaimers, verify_status travel,
    data-limit disclosures, auditability/reversibility.
 7. Refresh & reproducibility — the DuckDB + sql/ pipeline, how new fiscal years (and new states)
    regenerate the feed.
 8. A phased integration plan — Phase 0..3 with scope, artifacts consumed, dependencies, milestone,
    rough effort; name the smallest end-to-end MVP and the critical path.
 9. Open questions / decisions for Overtaker — the choices they need to make (static feed vs API,
    document vs relational store, how to surface verdicts, multi-state roadmap).
Output the COMPLETE markdown document as your response (this IS the deliverable text).`,
  { label:'synthesize:integration-brief', phase:'Synthesize', agentType:'general-purpose' }
);

return { brief, lenses };
