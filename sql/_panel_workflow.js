export const meta = {
  name: 'az-auditor-tiering-panel',
  description: 'Expert auditor panel: calibrate forensic markers for high-value transaction tiering against the AZ checkbook feature mart, then synthesize a scoring/tier model',
  phases: [
    { title: 'Panel', detail: '12 forensic-auditor agents calibrate one marker family each against mart/hv_base.parquet' },
    { title: 'Synthesize', detail: 'chief audit synthesizer combines markers into a composite score + tier matrix' },
  ],
}

// ---------------------------------------------------------------------------
const PREAMBLE = `
You are a senior forensic auditor (CFE/CPA-level) building a TRANSACTION-TIERING model
for the State of Arizona checkbook (overtaker.ai — institutional analytics for municipal
bondholders). Framing discipline (NON-NEGOTIABLE): every marker produces *leads warranting
review*, NEVER findings of fraud; nothing names a wrongdoer; flags are "items to confirm,"
not accusations. A prior team learned this the hard way: a substring matcher flagged 25
Arizona "Aztec" firms as the sanctioned company "ZTE" ($47M false positives) — word-boundary
matching fixed it. Your guardrails must prevent that class of error.

DATA: A compact feature mart of every clean expenditure >= $25,000, FY2016-2025 (FY2022
excluded; FY2019-2020 are "thin" years — see caveats). 1,015,977 rows, $426.6B. Query ONLY
this file with duckdb (do NOT open warehouse.duckdb, and do NOT scan the raw parquet):

  duckdb -c "SET threads TO 4; SELECT ... FROM read_parquet('mart/hv_base.parquet') WHERE ..."

(Run from /Users/moeasnani/Documents/Openbooks. Use printf for readable dollars.)

COLUMNS (one row = one high-value expenditure):
 identity:    record_number, transaction_id, transaction_reference_id, invoice_number,
              fiscal_year, posting_date, fp_canon(int fiscal period 1-13; 13=adjustment)
 org:         agency(=organization_level_1_name), org2, cabinet_name
 nature:      category1, category2, appropriation_1_name, appropriation_1_code,
              appr_type_canon('1','2','2A'..,'3','RB'; NULL in FY19/20), appropriation_category_1_name,
              fund_1_name, object_no(populated FY16-18 only)
 payee:       payee, payee_dba_name, vendor_id_code, vid(=real vendor id or NULL),
              contract_number, contract_name(both ~2-6% populated)
 rail:        payment_method('ACH','WARRANT','INTERNAL','JOURNAL VOUCHER','COST ALLOCATION','PAYMENT',NULL)
 amount:      amount(DECIMAL), mag_band('M1_100M+','M2_10M-100M','M3_1M-10M','M4_250K-1M','M5_100K-250K','M6_25K-100K')
 round flags: is_whole_dollar,is_round_1k,is_round_10k,is_round_100k,is_round_1m, first_digit
 vendor feat: vendor_year_total, vendor_year_txns, vendor_first_year, vendor_years_active,
              vendor_first_appearance(bool), vendor_agency_year_total, agency_year_total,
              vendor_share_of_agency_year(0-1 double)
 approp feat: approp_year_total, approp_year_txns
 dup feat:    dup_distinct_txn, dup_rows, dup_day_span, in_dup_signature(bool), in_dup_diffday(bool)
 peer ref:    ac_n, ac_median, ac_q1, ac_q3, robust_z_in_agencycat(double; (amt-median)/(IQR/1.349) within agency x category1)

DATA-QUALITY CAVEATS (your marker MUST degrade gracefully — never fire on, or penalize, a
transaction merely because a field is system-wide absent in its year):
 - FY2019 & FY2020: appr_type_canon, fp_canon, vid all NULL system-wide. vendor/appropriation/
   period markers CANNOT run there. State applicable_years honestly.
 - vid (real vendor id) present only ~24-48% even in good years; 42.7% of the >=$100K pop has NULL vid.
 - object_no populated FY2016-2018 only; invoice_number FY2016-2018 & 2021 only.
 - '99999999.xx' sentinels already excluded. Vendor sentinels MISCPAYVEND / 0000.. already mapped to NULL vid.

EXPECTED-LARGE vs DISCRETIONARY (auditor risk-weighting by category1): AID TO ORGANIZATIONS
AND INDIVIDUALS (Medicaid/benefits), TRANSFERS OUT, DEBT SERVICE are *scheduled/entitlement* —
large amounts are normal, low per-txn suspicion. PROFESSIONAL AND OUTSIDE SERVICES, OTHER
OPERATING EXPENDITURES, CAPITAL OUTLAY/EQUIPMENT are *discretionary procurement* — the same
dollar carries more scrutiny weight. Reflect this in guardrails/weights where relevant.

BASELINE PREVALENCE in the >=$100K population (306,604 txns) for orientation:
 round_100k 1.8% | round_1m 0.6% | vendor_first_appearance 12.1% | dup_diffday 0.2% |
 vendor_share>=50% 3.1% | period13 0.2% | June(p12) 8.5% | manual rail(JV/INTERNAL) 11.5% |
 robust_z>=10 14.4% | NULL vid 42.7%.
`;

const TASK = `
YOUR JOB for this marker family:
1) PROTOTYPE candidate predicates with duckdb against mart/hv_base.parquet. Iterate.
2) CALIBRATE so each marker is discriminating, not noise: a high-severity marker should fire on
   a SMALL share of the >=$100K population (rule of thumb: <5% for severe, <15% for moderate).
   Report the calibrated count and dollar exposure (restrict your headline stats to amount>=100000).
3) SANITY-CHECK by pulling 3-5 real example rows (agency, payee, amount, appropriation_1_name)
   and confirm they look like genuine review-worthy items, NOT benign-by-construction artifacts.
4) Write GUARDRAILS into the predicate (or as explicit notes) to suppress benign matches
   (e.g., exclude entitlement categories; require vid IS NOT NULL so you never fire on a thin year;
   word-boundary not substring for any name match).
5) Assign a severity_weight 1-5 (5 = strongest standalone scrutiny signal).
6) Predicates MUST be boolean SQL expressions over hv_base columns ONLY (so they compose into one
   query). If you need a feature not in the mart, still return the predicate using a placeholder
   column name and describe the feature precisely in 'notes' so it can be added.

Return a MarkerSpec. Make every sql_predicate a valid DuckDB boolean expression you actually ran.
`;

const FAMILIES = [
  { key: 'materiality_stratification', title: 'Materiality & category risk-weighting',
    brief: `Define the materiality stratification (validate/justify the M1-M6 magnitude bands or
    propose better cutoffs grounded in the amount distribution) AND the category1 risk-weight map
    (expected-large vs discretionary). Your "markers" here are: (a) a magnitude-band scheme with a
    per-band base scrutiny weight, and (b) a category_risk_multiplier per category1 value (query the
    distinct category1 values and assign each a multiplier 0.3-1.5). This feeds the whole model.` },
  { key: 'round_negotiated_amount', title: 'Round / negotiated-amount marker',
    brief: `Round and "too-clean" amounts at high value (is_round_100k, is_round_1m, exact whole-dollar
    in categories that normally have cents) suggest estimated/negotiated/manual amounts rather than
    invoice-derived. Distinguish suspicious roundness (discretionary services/capital) from benign
    roundness (grants, transfers, debt service, appropriated allotments which are legitimately round).` },
  { key: 'duplicate_split_payment', title: 'Duplicate & split-payment marker',
    brief: `Use in_dup_signature/in_dup_diffday/dup_* for repeated-payment exposure; weight different-day
    repeats far above same-day batches. Also design a "split" angle: clusters of high-value payments to
    the same vid in a short window that may aggregate around a control threshold. Guardrail: Medicaid
    managed-care capitation and pension paydowns are legitimate same-reference batches.` },
  { key: 'sole_source_concentration', title: 'Sole-source & vendor-concentration marker',
    brief: `vendor_share_of_agency_year high (vendor dominates an agency's spend), or a vendor that is
    the near-entirety of an appropriation. Tier by how dominant. Guardrail: utilities, pension systems
    (PSPRS), banks (Lottery->BofA), and sole legitimate statewide providers are concentrated for benign
    reasons — these are dependency/exposure signals, not irregularity. vid IS NOT NULL required.` },
  { key: 'new_vendor_high_value', title: 'New / first-appearance vendor at high value',
    brief: `vendor_first_appearance carrying a large FIRST payment, or vendors with very few
    vendor_years_active but large vendor_year_total. New vendor + immediate big money is a classic
    onboarding-control lead. Guardrail: legitimate new contracts, renamed entities, one-off capital
    grants to municipalities, federal pass-through grantees. vid IS NOT NULL required.` },
  { key: 'yearend_adjustment_timing', title: 'Year-end & adjustment-period timing',
    brief: `fp_canon=13 (adjustment period) at high value is a top-side/closing-entry lead; June
    (fp_canon=12) surges are "use-it-or-lose-it". Design markers for period-13 high-value items and
    for amounts disproportionately posted in the final period. Guardrail: legitimate year-end true-ups,
    accruals. Only applicable where fp_canon present (NOT FY19/20).` },
  { key: 'manual_payment_rail', title: 'Manual / non-standard payment rail',
    brief: `JOURNAL VOUCHER, INTERNAL, and (to a lesser degree) WARRANT or NULL payment_method at high
    value bypass the normal ACH accounts-payable path — manual entries warrant review. Guardrail:
    inter-fund TRANSFERS OUT and COST ALLOCATION legitimately use JV/INTERNAL; do not over-fire on them.` },
  { key: 'peer_outlier_spike', title: 'Peer-outlier & spike marker',
    brief: `robust_z_in_agencycat extreme (amount dwarfs the agency x category norm). Also approximate a
    year-over-year spike angle using vendor_year_total / approp_year_total magnitude. Calibrate the
    robust_z cutoff so it isolates genuine outliers, not the whole tail. Guardrail: known mega-programs
    (AHCCCS capitation, pension paydown) are outliers for benign reasons.` },
  { key: 'accountability_gap', title: 'Accountability-gap marker',
    brief: `High-value items missing the fields that enable oversight: NULL appropriation_1_code, generic/
    placeholder payee, payee_dba_name present but differs oddly from payee, no contract on a discretionary
    services/capital item, NULL vid in a year where vid is OTHERWISE populated (i.e. a real gap, not a thin
    year). CRITICAL guardrail: never fire for a field that is system-wide absent in that fiscal_year.` },
  { key: 'discretionary_procurement', title: 'Discretionary-procurement concentration',
    brief: `Focus on PROFESSIONAL AND OUTSIDE SERVICES, OTHER OPERATING EXPENDITURES, CAPITAL OUTLAY/
    EQUIPMENT (true procurement). Large, concentrated, off-contract (contract_number NULL) procurement is
    the highest competitive-bidding-risk surface (mirrors the AG's "vendors without fair/open competition"
    finding). Build a marker isolating large discretionary-procurement items lacking a contract linkage.` },
  { key: 'entity_name_anomaly', title: 'Entity / payee-name anomaly',
    brief: `Payee strings that look like an individual person in a NON-payroll/benefit category (possible
    related-party), DBA != legal name, near-duplicate vendor name variants (shell-company style), or
    generic/placeholder names. Use string heuristics on payee/payee_dba_name. STRICT guardrail: word-
    boundary/regex not substring; exclude obvious legitimate businesses; legitimate sole proprietors and
    common surnames exist — keep severity modest and flag for confirmation only.` },
  { key: 'digit_pattern', title: 'Benford / digit-pattern at stratum',
    brief: `Within the high-value population, look for digit-preference anomalies that hint at estimated/
    fabricated amounts: agencies whose high-value amounts over-represent certain leading/second digits, or
    excess of specific repeated-digit amounts. Output an agency-level digit-anomaly flag that can elevate
    that agency's transactions modestly. This is a population diagnostic; keep per-transaction weight low.` },
];

// ---------------------------------------------------------------------------
const MARKER_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['family', 'markers', 'notes'],
  properties: {
    family: { type: 'string' },
    markers: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['marker_id','name','sql_predicate','severity_weight','applicable_years',
                   'rationale','guardrails','calibrated_count','calibrated_exposure_usd','examples'],
        properties: {
          marker_id: { type: 'string', description: 'snake_case unique id' },
          name: { type: 'string' },
          sql_predicate: { type: 'string', description: 'valid DuckDB boolean expression over hv_base columns; for non-boolean outputs (e.g. category multiplier map) describe in notes and give a representative predicate' },
          severity_weight: { type: 'number', description: '1-5' },
          applicable_years: { type: 'string' },
          rationale: { type: 'string' },
          guardrails: { type: 'string' },
          calibrated_count: { type: 'number', description: 'rows in the >=100k population that fire' },
          calibrated_exposure_usd: { type: 'number' },
          examples: {
            type: 'array',
            items: {
              type: 'object', additionalProperties: false,
              required: ['amount','agency','payee','why'],
              properties: {
                amount: { type: 'number' }, agency: { type: 'string' },
                payee: { type: 'string' }, why: { type: 'string' },
              },
            },
          },
        },
      },
    },
    notes: { type: 'string', description: 'overlaps with other families, features needed but absent from mart, calibration reasoning' },
  },
};

const SCORING_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['marker_weights','risk_bands','tier_matrix','overlap_notes','scoring_formula','category_multipliers'],
  properties: {
    scoring_formula: { type: 'string', description: 'precise description of how per-transaction risk_score is computed from markers' },
    marker_weights: { type: 'array', items: { type: 'object', additionalProperties: false,
      required: ['marker_id','weight','reason'],
      properties: { marker_id: {type:'string'}, weight: {type:'number'}, reason: {type:'string'} } } },
    category_multipliers: { type: 'array', items: { type: 'object', additionalProperties: false,
      required: ['category1','multiplier'], properties: { category1:{type:'string'}, multiplier:{type:'number'} } } },
    risk_bands: { type: 'array', items: { type: 'object', additionalProperties: false,
      required: ['label','min_score','max_score'],
      properties: { label:{type:'string'}, min_score:{type:'number'}, max_score:{type:'number'} } } },
    tier_matrix: { type: 'string', description: 'how (magnitude band x risk band) maps to final Tier 1-4; be explicit and recomputable' },
    overlap_notes: { type: 'string', description: 'which markers double-count and how to dedup' },
  },
};

// ---------------------------------------------------------------------------
phase('Panel');
const specs = await parallel(FAMILIES.map(f => () =>
  agent(
    `${PREAMBLE}\nMARKER FAMILY: ${f.title}\n${f.brief}\n${TASK}`,
    { label: `panel:${f.key}`, phase: 'Panel', schema: MARKER_SCHEMA, agentType: 'general-purpose' }
  )
));

const goodSpecs = specs.filter(Boolean);
log(`Panel returned ${goodSpecs.length}/${FAMILIES.length} marker specs`);

phase('Synthesize');
const model = await agent(
  `${PREAMBLE}
You are the CHIEF AUDIT SYNTHESIZER. Twelve forensic-auditor specialists each calibrated a marker
family against mart/hv_base.parquet. Their calibrated specs (JSON):

${JSON.stringify(goodSpecs, null, 1)}

Design the COMPOSITE TIERING MODEL that combines these into (a) a per-transaction risk_score, and
(b) a final scrutiny Tier. Requirements:
 - Documented, fully recomputable thresholds (NO black-box) — a bondholder must be able to reproduce
   every tier from the source fields. This matches the product's existing scorecard philosophy.
 - risk_score = weighted sum of fired markers, modulated by the category_risk_multiplier. Resolve
   double-counting (several families overlap: round+manual+yearend can all fire on one estimate JV).
 - Final Tier 1-4 from (magnitude band x risk band): Tier 1 = high materiality AND elevated markers
   (top review priority); Tier 4 = high value but clean. Define the matrix explicitly.
 - Keep weights faithful to the specialists' severity_weights but reconcile overlaps and over-firing.
 - You may run duckdb against mart/hv_base.parquet to check combined prevalence before finalizing.
Return a ScoringModel.`,
  { label: 'synthesize:scoring-model', phase: 'Synthesize', schema: SCORING_SCHEMA, agentType: 'general-purpose' }
);

return { specs: goodSpecs, model };
