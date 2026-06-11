-- ============================================================================
-- TIERING MODEL v2 — post adversarial-verification.
-- Incorporates the red-team/synthesizer fixes:
--   RETIRE agency_benford & triple_gap; GATE placeholder_payee to external rails;
--   new_vendor payee-NAME resurfacing guard (kills FY2021 vid-renumber artifact);
--   tighten person_name (recurrence + suffix/govt); structural dark-switch on offcontract;
--   drop WARRANT from nonstd_rail; redefine duplicate to require DISTINCT documents;
--   round/concentration/yearend/peer benign-by-structure suppressions.
-- Output: table tx_tiered (overwritten = final). Run: duckdb warehouse.duckdb < sql/tiering_v2.sql
-- ============================================================================
SET threads TO 16; SET memory_limit='90GB';
SET lambda_syntax='ENABLE_SINGLE_ARROW';

CREATE OR REPLACE TEMP TABLE hv AS
SELECT *, upper(trim(payee)) AS pkey FROM read_parquet('mart/hv_base.parquet') WHERE amount >= 100000;

-- ---- auxiliary features computed over the full >=$25K mart -----------------
-- payee NAME first-year + recurrence (resurfacing guard / person-name recurrence)
CREATE OR REPLACE TEMP TABLE payee_agg AS
SELECT upper(trim(payee)) AS pkey, min(fiscal_year) AS payee_first_year, count(*) AS payee_hv_count
FROM read_parquet('mart/hv_base.parquet')
WHERE payee IS NOT NULL AND trim(payee) <> '' GROUP BY 1;

-- contract_number population rate per agency x year over discretionary hv (dark-switch)
CREATE OR REPLACE TEMP TABLE contract_pop AS
SELECT agency, fiscal_year, count(contract_number)*1.0/nullif(count(*),0) AS contract_rate
FROM read_parquet('mart/hv_base.parquet')
WHERE category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','OTHER OPERATING EXPENDITURES','CAPITAL OUTLAY','CAPITAL EQUIPMENT','NON-CAPITAL EQUIPMENT')
GROUP BY 1,2;

-- tightened TRUE duplicate: same vid+amount across >=2 DISTINCT documents, >=2 txns, different days <=90
CREATE OR REPLACE TEMP TABLE truedup AS
SELECT vid, amount
FROM read_parquet('mart/hv_base.parquet')
WHERE vid IS NOT NULL AND transaction_reference_id IS NOT NULL
GROUP BY vid, amount
HAVING count(DISTINCT transaction_reference_id) >= 2
   AND count(DISTINCT transaction_id) >= 2
   AND (max(posting_date)-min(posting_date)) BETWEEN 1 AND 90;

-- benign-by-structure appropriation guard (reused by several markers)
-- (pension paydown / employee benefits / risk mgmt / lottery prize / debt service)
CREATE OR REPLACE MACRO benign_approp(a) AS (
  upper(coalesce(a,'')) LIKE '%UNFUNDED LIABILITY%' OR
  upper(coalesce(a,'')) LIKE '%BENEFITS PROGRAM%'  OR
  upper(coalesce(a,'')) LIKE '%RISK MANAGEMENT%'   OR
  upper(coalesce(a,'')) LIKE '%PRIZE%'             OR
  upper(coalesce(a,'')) LIKE '%DEBT SERVICE%'
);
CREATE OR REPLACE MACRO is_igov(p) AS (
  regexp_matches(upper(coalesce(p,'')),'(^| )(DEPARTMENT OF|DEPT OF|UNIVERSITY|COMMUNITY COLLEGE|COUNTY|CITY OF|TOWN OF|STATE OF|STATE TREASUR|US TREASURY|TREASURER|RETIREMENT SYSTEM|SCHOOL DISTRICT|REGENTS)( |$)')
);
CREATE OR REPLACE MACRO is_postage(p) AS (upper(coalesce(p,'')) LIKE 'PITNEY BOWES%' OR upper(coalesce(p,'')) LIKE '%POSTAGE%');
-- structural benign recipients (banks=Lottery/treasury, benefits carriers=employee health trust, pension)
-- needed because FY2019/2020 have NULL appropriation, so benign_approp cannot reach those years
CREATE OR REPLACE MACRO benign_payee(p) AS (
  regexp_matches(upper(coalesce(p,'')),'(^| )(BANK OF AMERICA|U S BANK|US BANK|WELLS FARGO|JPMORGAN|BNY MELLON|NORTHERN TRUST|UNITED HEALTHCARE|BLUE CROSS|RETIREMENT SYSTEM)( |$|,|-)')
);

-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE tx_tiered AS
WITH m AS (
  SELECT h.*,
    pa.payee_first_year, coalesce(pa.payee_hv_count,0) AS payee_hv_count,
    coalesce(cp.contract_rate,0) AS contract_rate,
    (td.vid IS NOT NULL) AS is_true_dup,
    (h.pkey IS NOT NULL AND pa.payee_first_year = h.fiscal_year) AS name_is_new,
    -- discretionary set
    (category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','OTHER OPERATING EXPENDITURES','CAPITAL OUTLAY','CAPITAL EQUIPMENT','NON-CAPITAL EQUIPMENT')) AS is_disc
  FROM hv h
  LEFT JOIN payee_agg pa USING(pkey)
  LEFT JOIN contract_pop cp ON cp.agency=h.agency AND cp.fiscal_year=h.fiscal_year
  LEFT JOIN truedup td ON td.vid=h.vid AND td.amount=h.amount
),
mk AS (
  SELECT m.*,
    -- ===== ROUND family (+ postage/pension/lottery/risk suppressions) =====
    (is_round_1m AND is_disc AND NOT benign_approp(appropriation_1_name) AND NOT is_postage(payee) AND NOT benign_payee(payee)
       AND (payee IS NULL OR (agency<>payee AND NOT contains(upper(payee),upper(agency)) AND NOT contains(upper(agency),upper(payee))))
       AND upper(coalesce(payee,'X')) NOT IN ('N/A','NULL','NONE','VARIOUS')
       AND (payment_method IS NULL OR payment_method NOT IN ('INTERNAL','JOURNAL VOUCHER','COST ALLOCATION'))) AS mk_round_1m,
    (is_round_100k AND NOT is_round_1m AND is_disc AND NOT benign_approp(appropriation_1_name) AND NOT is_postage(payee) AND NOT benign_payee(payee)
       AND (payee IS NULL OR (agency<>payee AND NOT contains(upper(payee),upper(agency)) AND NOT contains(upper(agency),upper(payee))))
       AND upper(coalesce(payee,'X')) NOT IN ('N/A','NULL','NONE','VARIOUS')
       AND (payment_method IS NULL OR payment_method NOT IN ('INTERNAL','JOURNAL VOUCHER','COST ALLOCATION'))) AS mk_round_100k,
    (is_whole_dollar AND NOT is_round_10k
       AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','OTHER OPERATING EXPENDITURES','CAPITAL OUTLAY','CAPITAL EQUIPMENT','EMPLOYEE RELATED EXPENDITURES','FOOD')
       AND NOT benign_approp(appropriation_1_name) AND NOT is_postage(payee) AND NOT benign_payee(payee)
       AND (payee IS NULL OR (agency<>payee AND NOT contains(upper(payee),upper(agency)) AND NOT contains(upper(agency),upper(payee))))
       AND upper(coalesce(payee,'X')) NOT IN ('N/A','NULL','NONE','VARIOUS')
       AND (payment_method IS NULL OR payment_method NOT IN ('INTERNAL','JOURNAL VOUCHER','COST ALLOCATION'))) AS mk_whole_dollar,
    -- ===== DUPLICATE family (tightened: distinct documents) =====
    (is_true_dup AND is_disc AND NOT is_igov(payee) AND NOT benign_approp(appropriation_1_name) AND NOT benign_payee(payee)) AS mk_true_dup,
    -- ===== CONCENTRATION family (+ benefits/IGA exclusions) =====
    (vid IS NOT NULL AND vendor_share_of_agency_year>=0.20 AND is_disc
       AND NOT benign_approp(appropriation_1_name) AND NOT is_igov(payee)
       AND NOT (payee=agency OR payee ILIKE 'STATE OF ARIZONA%')
       AND NOT (regexp_matches(payee,'(^| )(U S BANK|BANK OF AMERICA|WELLS FARGO|JPMORGAN|BNY MELLON|NORTHERN TRUST)( |$|,)') OR payee ILIKE '%RETIREMENT SYSTEM%')) AS mk_sole_source,
    (vid IS NOT NULL AND vendor_share_of_agency_year>=0.33
       AND category1 NOT IN ('AID TO ORGANIZATIONS AND INDIVIDUALS','TRANSFERS OUT','DEBT SERVICE')
       AND NOT benign_approp(appropriation_1_name) AND NOT is_igov(payee)
       AND NOT (payee=agency OR payee ILIKE 'STATE OF ARIZONA%')) AS mk_dependency,
    -- ===== NEW-VENDOR family (name resurfacing guard: NAME must also be new) =====
    (vid IS NOT NULL AND vendor_first_appearance AND name_is_new AND fiscal_year>2016
       AND vendor_year_total>=1000000 AND vendor_share_of_agency_year>=0.10 AND is_disc
       AND NOT is_igov(payee) AND NOT (upper(appropriation_1_name) LIKE '%LUMP SUM%')) AS mk_newvendor_dominant,
    (vid IS NOT NULL AND vendor_first_appearance AND name_is_new AND fiscal_year>2016 AND is_disc
       AND NOT is_igov(payee) AND NOT (upper(appropriation_1_name) LIKE '%LUMP SUM%')) AS mk_newvendor_large,
    (vid IS NOT NULL AND vendor_years_active<=2 AND name_is_new AND vendor_year_total>=2000000 AND fiscal_year>2016
       AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','CAPITAL OUTLAY','CAPITAL EQUIPMENT')
       AND NOT is_igov(payee) AND NOT (upper(appropriation_1_name) LIKE '%LUMP SUM%')) AS mk_short_tenure,
    -- ===== YEAR-END family (exclude internal book entries + require readable payee) =====
    (fp_canon=13 AND is_disc AND payment_method NOT IN ('INTERNAL','JOURNAL VOUCHER','COST ALLOCATION')
       AND upper(coalesce(payee,'NULL')) NOT IN ('N/A','NULL','NONE','VARIOUS') AND payee IS NOT NULL) AS mk_p13_disc,
    (fp_canon=12 AND is_disc AND (is_round_100k OR is_round_1m)
       AND NOT benign_approp(appropriation_1_name) AND NOT is_postage(payee)
       AND payment_method NOT IN ('INTERNAL','JOURNAL VOUCHER','COST ALLOCATION')
       AND upper(coalesce(payee,'NULL')) NOT IN ('N/A','NULL','NONE','VARIOUS') AND payee IS NOT NULL) AS mk_june_round,
    -- ===== PEER-OUTLIER (absolute floor + self/internal exclusion) =====
    (robust_z_in_agencycat>=20 AND ac_n>=8 AND amount>=250000
       AND category1 NOT IN ('AID TO ORGANIZATIONS AND INDIVIDUALS','TRANSFERS OUT','DEBT SERVICE','COST ALLOCATION AND INDIRECT COSTS','PERSONAL SERVICES','EMPLOYEE RELATED EXPENDITURES')
       AND NOT benign_approp(appropriation_1_name) AND NOT is_igov(payee) AND NOT benign_payee(payee)
       AND NOT (upper(coalesce(payee,''))=upper(agency))
       AND NOT (payment_method IN ('INTERNAL','COST ALLOCATION') AND (payee IS NULL OR payee='N/A'))
       AND NOT (payee ILIKE '%UNIVERSITY%' AND agency ILIKE '%UNIVERSITY%')) AS mk_peer_outlier,
    -- ===== ACCOUNTABILITY (placeholder GATED to external rails; triple_gap retired) =====
    (is_disc AND payment_method IN ('ACH','WARRANT','PAYMENT')
       AND (payee IS NULL OR upper(trim(payee)) IN ('N/A','NULL','NONE','UNKNOWN','TBD','VOID'))) AS mk_placeholder_payee,
    (amount>=1000000 AND fiscal_year NOT IN (2019,2020) AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','CAPITAL OUTLAY')
       AND contract_number IS NULL AND contract_rate>0 AND vid IS NOT NULL AND payee IS NOT NULL
       AND payee NOT IN ('N/A','NULL','MISCELLANEOUS VENDOR','MISCELLANEOUS CUSTOMER')
       AND NOT benign_approp(appropriation_1_name) AND NOT is_igov(payee)
       AND upper(trim(payee))<>upper(trim(agency))) AS mk_no_contract,
    (is_disc AND contract_number IS NULL AND contract_rate>0 AND vid IS NOT NULL
       AND coalesce(category2,'')<>'INTRASTATE DISTRIBUTIONS' AND NOT benign_approp(appropriation_1_name)
       AND NOT is_igov(payee) AND payee IS DISTINCT FROM agency) AS mk_offcontract,
    -- ===== MANUAL-RAIL (WARRANT dropped from nonstd; contracted excluded) =====
    (payment_method IN ('JOURNAL VOUCHER','INTERNAL') AND is_disc AND contract_number IS NULL) AS mk_manual_disc,
    (payment_method IN ('JOURNAL VOUCHER','INTERNAL') AND contract_number IS NULL
       AND category1 NOT IN ('TRANSFERS OUT','AID TO ORGANIZATIONS AND INDIVIDUALS','DEBT SERVICE','COST ALLOCATION AND INDIRECT COSTS')) AS mk_manual_nonent,
    (payment_method IS NULL AND is_disc) AS mk_nonstd_null,
    -- ===== ENTITY / NAME-ANOMALY (recurrence + suffix/govt tightening) =====
    (is_disc OR category1='EMPLOYEE RELATED EXPENDITURES') AND payee_hv_count<=3
       AND regexp_matches(payee,'^[A-Z][A-Z''-]+ [A-Z][A-Z''-]+$')
       AND NOT is_igov(payee)
       AND NOT regexp_matches(payee,'(INC|LLC|LLP|CORP|COMPANY|LTD|PLLC|PC|APC|PLC|LP|USA|JV|ASSOC|GROUP|PARTNER|TRUST|BANK|COUNTY|UNIV|DEPT|SERVICE|SYSTEM|SOLUTION|TECH|CONSULT|ENTERPRISE|HOLDING|FUND|FOUNDATION|HOSPITAL|CLINIC|CENTER|CENTRE|INSTITUTE|SCHOOL|DISTRICT|HEALTH|MEDICAL|INSURANCE|CONSTRUCT|ELECTRIC|SUPPLY|EQUIP|PRODUCT|INDUSTR|MGMT|MANAGEMENT|CAPITAL|FINANCIAL|REALTY|PROPERT|MOTOR|AUTO|ENERGY|POWER|WATER|COMMUNICAT|MEDIA|PRESS|ENGINEER|ARCHITECT|PHARM|NETWORK|SOFTWARE|DESIGN|CONTRACT|BUILDER|FARM|RANCH|MARKET|STORE|FOOD|INTERNATIONAL|NATIONAL|GLOBAL|AMERICA|DIGITAL|CREATIVE|DEVELOP|LEARNING|DISTRIBUT|NEXTEL|INSIGHT|VENDOR|CUSTOMER|MISC|VARIOUS|REDACTED|TRANSPORT|MINISTR|CASINO|RISK|LENS|PIPELINE|STORAGE|TRAURIG|ROCK|CHEVROLET|KENWORTH|BOWES|DONNELLEY|DONNELLY|CALMAT|CLYDE|GRAINGER|SALES|LABOR|TREASUR)') AS mk_person_name,
    (is_disc AND payment_method IN ('ACH','WARRANT','PAYMENT')
       AND (payee IS NULL OR upper(payee) IN ('N/A','NA','NONE','NULL','UNKNOWN','TBD','VOID')
            OR regexp_matches(payee,'(^|\s)(MISC|MISCELLANEOUS|VARIOUS|VENDOR|CUSTOMER|PAYEE|PLACEHOLDER|TEMP|TEMPORARY|TEST|DUMMY|DEFAULT|SUNDRY|PCARD|FUEL VENDOR)(\s|$)'))) AS mk_masked_payee
  FROM m
),
fam AS (
  SELECT mk.*,
    GREATEST(if(mk_round_1m,5,0), if(mk_round_100k,4,0), if(mk_whole_dollar,3,0))               AS f_round,
    if(mk_true_dup,4,0)                                                                          AS f_dup,
    GREATEST(if(mk_sole_source,4,0), if(mk_dependency,2,0))                                       AS f_conc,
    GREATEST(if(mk_newvendor_dominant,5,0), if(mk_short_tenure,4,0), if(mk_newvendor_large,3,0))  AS f_newvendor,
    GREATEST(if(mk_p13_disc,4,0), if(mk_june_round,3,0))                                          AS f_yearend,
    if(mk_peer_outlier,4,0)                                                                       AS f_peer,
    GREATEST(if(mk_placeholder_payee,4,0), if(mk_no_contract,2,0), if(mk_offcontract,2,0))        AS f_account,
    GREATEST(if(mk_manual_disc,4,0), if(mk_manual_nonent,3,0), if(mk_nonstd_null,2,0))            AS f_rail,
    -- entity: dedup masked vs placeholder (both external-rail blank payee)
    GREATEST(if(mk_person_name,3,0), if(mk_masked_payee AND NOT mk_placeholder_payee,3,0))        AS f_entity
  FROM mk
),
scored AS (
  SELECT fam.*,
    (CASE category1
       WHEN 'DEBT SERVICE' THEN 0.3 WHEN 'AID TO ORGANIZATIONS AND INDIVIDUALS' THEN 0.4
       WHEN 'TRANSFERS OUT' THEN 0.4 WHEN 'COST ALLOCATION AND INDIRECT COSTS' THEN 0.5
       WHEN 'CONVERSION' THEN 0.5 WHEN 'PERSONAL SERVICES' THEN 0.7
       WHEN 'EMPLOYEE RELATED EXPENDITURES' THEN 0.9 WHEN 'FOOD' THEN 0.9
       WHEN 'TRAVEL - IN-STATE' THEN 1.0 WHEN 'TRAVEL - OUT-OF-STATE' THEN 1.0
       WHEN 'NON-CAPITAL EQUIPMENT' THEN 1.2 WHEN 'CAPITAL EQUIPMENT' THEN 1.2
       WHEN 'CAPITAL OUTLAY' THEN 1.3 WHEN 'OTHER OPERATING EXPENDITURES' THEN 1.4
       WHEN 'PROFESSIONAL AND OUTSIDE SERVICES' THEN 1.5 ELSE 1.0 END) AS category_multiplier,
    (f_round+f_dup+f_conc+f_newvendor+f_yearend+f_peer+f_account+f_rail+f_entity) AS marker_sum
  FROM fam
),
final AS (
  SELECT scored.*,
    round(marker_sum * category_multiplier, 3) AS risk_score,
    list_filter([
      if(mk_round_1m,'round_1m',NULL), if(mk_round_100k,'round_100k',NULL), if(mk_whole_dollar,'whole_dollar',NULL),
      if(mk_true_dup,'true_duplicate',NULL),
      if(mk_sole_source,'sole_source',NULL), if(mk_dependency,'vendor_dependency',NULL),
      if(mk_newvendor_dominant,'new_vendor_dominant',NULL), if(mk_short_tenure,'short_tenure_vendor',NULL), if(mk_newvendor_large,'new_vendor_large',NULL),
      if(mk_p13_disc,'period13_disc',NULL), if(mk_june_round,'june_round',NULL),
      if(mk_peer_outlier,'peer_outlier',NULL),
      if(mk_placeholder_payee,'placeholder_payee_extrail',NULL), if(mk_no_contract,'no_contract_named',NULL), if(mk_offcontract,'offcontract',NULL),
      if(mk_manual_disc,'manual_rail_disc',NULL), if(NOT mk_manual_disc AND mk_manual_nonent,'manual_rail',NULL), if(NOT mk_manual_disc AND NOT mk_manual_nonent AND mk_nonstd_null,'nonstd_rail',NULL),
      if(mk_person_name,'person_name_payee',NULL), if(mk_masked_payee AND NOT mk_placeholder_payee,'masked_payee',NULL)
    ], x -> x IS NOT NULL) AS fired_markers
  FROM scored
)
SELECT
  record_number, transaction_id, transaction_reference_id, fiscal_year, posting_date,
  agency, org2, cabinet_name, category1, category2, appropriation_1_name, fund_1_name,
  payee, vendor_id_code, vid, contract_number, payment_method, fp_canon,
  amount, mag_band,
  vendor_share_of_agency_year, vendor_first_appearance, name_is_new, robust_z_in_agencycat, is_true_dup,
  f_round, f_dup, f_conc, f_newvendor, f_yearend, f_peer, f_account, f_rail, f_entity,
  marker_sum, category_multiplier, risk_score, fired_markers, len(fired_markers) AS n_markers,
  (CASE WHEN amount >= 1000000 THEN 'MB_HIGH' ELSE 'MB_LOW' END) AS mag_class,
  (CASE WHEN risk_score = 0 THEN 'R0_NONE' WHEN risk_score < 3 THEN 'R1_LOW'
        WHEN risk_score < 6 THEN 'R2_MODERATE' WHEN risk_score < 10 THEN 'R3_ELEVATED'
        ELSE 'R4_HIGH' END) AS risk_band,
  (CASE
     WHEN amount>=1000000 AND risk_score >= 6 THEN 1
     WHEN (amount>=1000000 AND risk_score >= 3) OR (amount<1000000 AND risk_score >= 6) THEN 2
     WHEN risk_score > 0 THEN 3
     WHEN amount>=1000000 THEN 4
     ELSE 5 END) AS tier
FROM final;

.print '======== v2 TIER DISTRIBUTION ========'
SELECT tier, count(*) AS n_txn, printf('%.2f%%',100.0*count(*)/sum(count(*)) OVER ()) AS pct,
  printf('%.0f',sum(amount)) AS exposure, printf('%.2f',avg(risk_score)) AS avg_score, printf('%.2f',avg(n_markers)) AS avg_mk
FROM tx_tiered GROUP BY tier ORDER BY tier;
.print ''
.print '======== v2 vs v1 score summary ========'
SELECT printf('%.2f',avg(risk_score)) mean, printf('%.2f',quantile_cont(risk_score,0.99)) p99,
  printf('%.2f',max(risk_score)) max, printf('%.1f%%',100.0*count(*) FILTER(risk_score=0)/count(*)) pct_clean,
  count(*) FILTER (tier=1) AS n_tier1 FROM tx_tiered;
