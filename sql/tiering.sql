-- ============================================================================
-- UNIFIED HIGH-VALUE TRANSACTION TIERING MODEL
-- Implements the auditor-panel + chief-synthesizer scoring design.
--   Population : clean EX, amount >= $100,000  (the 306,604-txn / 82.6%-of-spend set)
--   Output     : table tx_tiered in warehouse.duckdb  (one row per high-value txn)
--   Recompute  : every threshold below is documented & reproducible from source fields.
-- Run: duckdb warehouse.duckdb < sql/tiering.sql
-- ============================================================================
SET threads TO 16;
SET memory_limit = '90GB';

-- High-value spine, read from the enriched mart (fast; already filtered >= $25K).
CREATE OR REPLACE TEMP TABLE hv AS
SELECT * FROM read_parquet('mart/hv_base.parquet') WHERE amount >= 100000;

-- ---------------------------------------------------------------------------
-- (1) AGENCY BENFORD FLAG  (dual first+second-digit MAD, peer-p75, n>=500)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TEMP TABLE digits AS
SELECT agency,
       CAST(left(CAST(CAST(floor(amount) AS BIGINT) AS VARCHAR),1) AS INT)    AS d1,
       CAST(substr(CAST(CAST(floor(amount) AS BIGINT) AS VARCHAR),2,1) AS INT) AS d2
FROM hv;

CREATE OR REPLACE TEMP TABLE agc AS
SELECT agency, count(*) AS n FROM digits GROUP BY 1 HAVING count(*) >= 500;

CREATE OR REPLACE TEMP TABLE mad1 AS
WITH cnt AS (SELECT agency, d1 AS d, count(*) AS c FROM digits WHERE d1 BETWEEN 1 AND 9 GROUP BY 1,2),
spine AS (SELECT a.agency, e.d, e.p FROM agc a CROSS JOIN
  (VALUES (1,0.30103),(2,0.17609),(3,0.12494),(4,0.09691),(5,0.07918),
          (6,0.06695),(7,0.05799),(8,0.05115),(9,0.04576)) e(d,p)),
freq AS (SELECT s.agency, s.p, coalesce(c.c,0)*1.0/a.n AS f
         FROM spine s JOIN agc a USING(agency)
         LEFT JOIN cnt c ON c.agency=s.agency AND c.d=s.d)
SELECT agency, avg(abs(f-p)) AS mad1 FROM freq GROUP BY 1;

CREATE OR REPLACE TEMP TABLE mad2 AS
WITH cnt AS (SELECT agency, d2 AS d, count(*) AS c FROM digits WHERE d2 BETWEEN 0 AND 9 GROUP BY 1,2),
spine AS (SELECT a.agency, e.d, e.p FROM agc a CROSS JOIN
  (VALUES (0,0.11968),(1,0.11389),(2,0.10882),(3,0.10433),(4,0.10031),
          (5,0.09668),(6,0.09337),(7,0.09035),(8,0.08757),(9,0.08500)) e(d,p)),
freq AS (SELECT s.agency, s.p, coalesce(c.c,0)*1.0/a.n AS f
         FROM spine s JOIN agc a USING(agency)
         LEFT JOIN cnt c ON c.agency=s.agency AND c.d=s.d)
SELECT agency, avg(abs(f-p)) AS mad2 FROM freq GROUP BY 1;

CREATE OR REPLACE TEMP TABLE benford AS
WITH m AS (SELECT a.agency, m1.mad1, m2.mad2 FROM agc a JOIN mad1 m1 USING(agency) JOIN mad2 m2 USING(agency)),
thr AS (SELECT quantile_cont(mad1,0.75) AS p75_1, quantile_cont(mad2,0.75) AS p75_2 FROM m)
SELECT m.agency, TRUE AS agency_benford_flag, m.mad1, m.mad2
FROM m, thr WHERE m.mad1 > thr.p75_1 AND m.mad2 > thr.p75_2;

-- ---------------------------------------------------------------------------
-- (2) PER-TRANSACTION MARKERS -> FAMILIES -> SCORE -> TIER
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE tx_tiered AS
WITH m AS (
  SELECT h.*,
    coalesce(b.agency_benford_flag,FALSE) AS agency_benford_flag,
    -- ============ ROUND / NEGOTIATED-AMOUNT family ============
    (amount>=100000 AND is_round_1m
       AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','OTHER OPERATING EXPENDITURES','CAPITAL OUTLAY','CAPITAL EQUIPMENT','NON-CAPITAL EQUIPMENT','EMPLOYEE RELATED EXPENDITURES','FOOD','TRAVEL - IN-STATE','TRAVEL - OUT-OF-STATE')
       AND (payee IS NULL OR (agency<>payee AND NOT contains(upper(payee),upper(agency)) AND NOT contains(upper(agency),upper(payee))))
       AND upper(coalesce(payee,'X')) NOT IN ('N/A','NULL','NONE','VARIOUS')
       AND (payment_method IS NULL OR payment_method NOT IN ('INTERNAL','JOURNAL VOUCHER','COST ALLOCATION'))) AS mk_round_1m,
    (amount>=100000 AND is_round_100k AND NOT is_round_1m
       AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','OTHER OPERATING EXPENDITURES','CAPITAL OUTLAY','CAPITAL EQUIPMENT','NON-CAPITAL EQUIPMENT','EMPLOYEE RELATED EXPENDITURES','FOOD','TRAVEL - IN-STATE','TRAVEL - OUT-OF-STATE')
       AND (payee IS NULL OR (agency<>payee AND NOT contains(upper(payee),upper(agency)) AND NOT contains(upper(agency),upper(payee))))
       AND upper(coalesce(payee,'X')) NOT IN ('N/A','NULL','NONE','VARIOUS')
       AND (payment_method IS NULL OR payment_method NOT IN ('INTERNAL','JOURNAL VOUCHER','COST ALLOCATION'))) AS mk_round_100k,
    (amount>=100000 AND is_whole_dollar AND NOT is_round_10k
       AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','OTHER OPERATING EXPENDITURES','CAPITAL OUTLAY','CAPITAL EQUIPMENT','EMPLOYEE RELATED EXPENDITURES','FOOD')
       AND (payee IS NULL OR (agency<>payee AND NOT contains(upper(payee),upper(agency)) AND NOT contains(upper(agency),upper(payee))))
       AND upper(coalesce(payee,'X')) NOT IN ('N/A','NULL','NONE','VARIOUS')
       AND (payment_method IS NULL OR payment_method NOT IN ('INTERNAL','JOURNAL VOUCHER','COST ALLOCATION'))) AS mk_whole_dollar,
    -- ============ DUPLICATE / SPLIT family ============
    (amount>=100000 AND in_dup_diffday
       AND category1 NOT IN ('AID TO ORGANIZATIONS AND INDIVIDUALS','TRANSFERS OUT','DEBT SERVICE')) AS mk_dup_diffday_disc,
    (amount>=100000 AND in_dup_signature AND NOT in_dup_diffday
       AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','OTHER OPERATING EXPENDITURES','CAPITAL OUTLAY','CAPITAL EQUIPMENT','NON-CAPITAL EQUIPMENT')
       AND payee IS DISTINCT FROM agency) AS mk_dup_sameday_ext,
    (amount>=100000 AND in_dup_diffday) AS mk_dup_diffday_all,
    -- ============ SOLE-SOURCE / CONCENTRATION family ============
    (amount>=100000 AND vid IS NOT NULL AND vendor_share_of_agency_year>=0.20
       AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','OTHER OPERATING EXPENDITURES','CAPITAL OUTLAY','CAPITAL EQUIPMENT','NON-CAPITAL EQUIPMENT')
       AND NOT (payee=agency OR payee ILIKE 'STATE OF ARIZONA%'
                OR (payee ILIKE '%UNIVERSITY%' AND agency ILIKE '%UNIVERSITY%')
                OR (payee ILIKE '%COMMUNITY COLLEGE%' AND agency ILIKE '%COMMUNITY COLLEGE%'))
       AND NOT (regexp_matches(payee,'(^| )(U S BANK|BANK OF AMERICA|WELLS FARGO|JPMORGAN|BNY MELLON|NORTHERN TRUST)( |$|,)') OR payee ILIKE '%RETIREMENT SYSTEM%')) AS mk_sole_source,
    (amount>=100000 AND vid IS NOT NULL AND vendor_share_of_agency_year>=0.33
       AND category1 NOT IN ('AID TO ORGANIZATIONS AND INDIVIDUALS','TRANSFERS OUT','DEBT SERVICE')
       AND NOT (payee=agency OR payee ILIKE 'STATE OF ARIZONA%'
                OR (payee ILIKE '%UNIVERSITY%' AND agency ILIKE '%UNIVERSITY%')
                OR (payee ILIKE '%COMMUNITY COLLEGE%' AND agency ILIKE '%COMMUNITY COLLEGE%'))) AS mk_dependency,
    -- ============ NEW-VENDOR family ============
    (vid IS NOT NULL AND vendor_first_appearance AND fiscal_year>2016 AND amount>=100000
       AND vendor_year_total>=1000000 AND vendor_share_of_agency_year>=0.10
       AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','OTHER OPERATING EXPENDITURES','CAPITAL OUTLAY','CAPITAL EQUIPMENT','NON-CAPITAL EQUIPMENT')
       AND NOT (upper(appropriation_1_name) LIKE '%LUMP SUM%')) AS mk_newvendor_dominant,
    (vid IS NOT NULL AND vendor_first_appearance AND fiscal_year>2016 AND amount>=100000
       AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','OTHER OPERATING EXPENDITURES','CAPITAL OUTLAY','CAPITAL EQUIPMENT','NON-CAPITAL EQUIPMENT')
       AND NOT (upper(appropriation_1_name) LIKE '%LUMP SUM%')) AS mk_newvendor_large,
    (vid IS NOT NULL AND vendor_years_active<=2 AND vendor_year_total>=2000000 AND fiscal_year>2016 AND amount>=100000
       AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','CAPITAL OUTLAY','CAPITAL EQUIPMENT')
       AND NOT (upper(appropriation_1_name) LIKE '%LUMP SUM%')) AS mk_short_tenure,
    -- ============ YEAR-END / ADJUSTMENT family ============
    (amount>=100000 AND fp_canon=13
       AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','OTHER OPERATING EXPENDITURES','CAPITAL OUTLAY','CAPITAL EQUIPMENT','NON-CAPITAL EQUIPMENT')) AS mk_p13_disc,
    (amount>=100000 AND fp_canon=13) AS mk_p13_all,
    (amount>=100000 AND fp_canon=12
       AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','OTHER OPERATING EXPENDITURES','CAPITAL OUTLAY','CAPITAL EQUIPMENT','NON-CAPITAL EQUIPMENT','EMPLOYEE RELATED EXPENDITURES')
       AND (is_round_100k OR is_round_1m)) AS mk_june_round,
    -- ============ PEER-OUTLIER family ============
    (amount>=100000 AND robust_z_in_agencycat>=20 AND ac_n>=8
       AND category1 NOT IN ('AID TO ORGANIZATIONS AND INDIVIDUALS','TRANSFERS OUT','DEBT SERVICE','COST ALLOCATION AND INDIRECT COSTS','PERSONAL SERVICES','EMPLOYEE RELATED EXPENDITURES')) AS mk_peer_outlier,
    -- ============ ACCOUNTABILITY-GAP family ============
    (amount>=100000 AND fiscal_year NOT IN (2019,2020) AND vid IS NULL AND payment_method IN ('JOURNAL VOUCHER','INTERNAL')
       AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','OTHER OPERATING EXPENDITURES','CAPITAL OUTLAY','CAPITAL EQUIPMENT','NON-CAPITAL EQUIPMENT')) AS mk_triple_gap,
    (amount>=100000 AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','OTHER OPERATING EXPENDITURES','CAPITAL OUTLAY','CAPITAL EQUIPMENT','NON-CAPITAL EQUIPMENT')
       AND (payee IN ('N/A','MISCELLANEOUS VENDOR','MISCELLANEOUS CUSTOMER') OR payee IS NULL OR regexp_matches(upper(payee),'(^| )(PCARD|FUEL) VENDOR( |$)'))) AS mk_placeholder_payee,
    (amount>=1000000 AND fiscal_year NOT IN (2019,2020) AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','CAPITAL OUTLAY')
       AND contract_number IS NULL AND vid IS NOT NULL AND payee IS NOT NULL
       AND payee NOT IN ('N/A','NULL','MISCELLANEOUS VENDOR','MISCELLANEOUS CUSTOMER')
       AND upper(trim(payee))<>upper(trim(agency))) AS mk_no_contract,
    (amount>=100000 AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','OTHER OPERATING EXPENDITURES','CAPITAL OUTLAY','CAPITAL EQUIPMENT','NON-CAPITAL EQUIPMENT')
       AND contract_number IS NULL AND vid IS NOT NULL AND coalesce(category2,'')<>'INTRASTATE DISTRIBUTIONS'
       AND payee IS DISTINCT FROM agency) AS mk_offcontract_base,
    -- ============ MANUAL-RAIL family ============
    (payment_method IN ('JOURNAL VOUCHER','INTERNAL')
       AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','OTHER OPERATING EXPENDITURES','CAPITAL OUTLAY','CAPITAL EQUIPMENT','NON-CAPITAL EQUIPMENT')) AS mk_manual_disc,
    (payment_method IN ('JOURNAL VOUCHER','INTERNAL')
       AND category1 NOT IN ('TRANSFERS OUT','AID TO ORGANIZATIONS AND INDIVIDUALS','DEBT SERVICE','COST ALLOCATION AND INDIRECT COSTS')) AS mk_manual_nonent,
    ((payment_method IS NULL OR payment_method='WARRANT')
       AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','OTHER OPERATING EXPENDITURES','CAPITAL OUTLAY','CAPITAL EQUIPMENT','NON-CAPITAL EQUIPMENT')) AS mk_null_warrant,
    -- ============ ENTITY / NAME-ANOMALY family ============
    (amount>=100000 AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','OTHER OPERATING EXPENDITURES','CAPITAL OUTLAY','CAPITAL EQUIPMENT','NON-CAPITAL EQUIPMENT','EMPLOYEE RELATED EXPENDITURES')
       AND regexp_matches(payee,'^[A-Z][A-Z''-]+ [A-Z][A-Z''-]+$')
       AND NOT regexp_matches(payee,'(INC|LLC|LLP|CORP|COMPANY|LTD|PLLC|ASSOC|GROUP|TRUST|BANK|COUNTY|UNIV|DEPT|SERVICE|SYSTEM|SOLUTION|TECH|CONSULT|ENTERPRISE|HOLDING|PARTNER|FUND|FOUNDATION|HOSPITAL|CLINIC|CENTER|CENTRE|INSTITUTE|SCHOOL|DISTRICT|HEALTH|MEDICAL|INSURANCE|CONSTRUCT|ELECTRIC|SUPPLY|EQUIP|PRODUCT|INDUSTR|MGMT|MANAGEMENT|CAPITAL|FINANCIAL|REALTY|PROPERT|MOTOR|AUTO|ENERGY|POWER|WATER|COMMUNICAT|MEDIA|PRESS|ENGINEER|ARCHITECT|PHARM|NETWORK|SOFTWARE|DESIGN|CONTRACT|BUILDER|FARM|RANCH|MARKET|STORE|FOOD|INTERNATIONAL|NATIONAL|GLOBAL|AMERICA|DIGITAL|CREATIVE|DEVELOP|LEARNING|DISTRIBUT|NEXTEL|INSIGHT|VENDOR|CUSTOMER|MISC|VARIOUS|REDACTED|TRANSPORT|MINISTR|CASINO|RISK|LENS|PIPELINE|STORAGE|TRAURIG|ROCK|CHEVROLET|KENWORTH)')) AS mk_person_name,
    (amount>=100000 AND category1 IN ('PROFESSIONAL AND OUTSIDE SERVICES','OTHER OPERATING EXPENDITURES','CAPITAL OUTLAY','CAPITAL EQUIPMENT','NON-CAPITAL EQUIPMENT')
       AND payment_method IN ('ACH','WARRANT','PAYMENT')
       AND (payee IS NULL OR upper(payee) IN ('N/A','NA','NONE','NULL','UNKNOWN','TBD','VOID')
            OR regexp_matches(payee,'(^|\s)(MISC|MISCELLANEOUS|VARIOUS|VENDOR|CUSTOMER|PAYEE|PLACEHOLDER|TEMP|TEMPORARY|TEST|DUMMY|DEFAULT|SUNDRY|PCARD|FUEL VENDOR)(\s|$)'))) AS mk_masked_payee
  FROM hv h
  LEFT JOIN benford b USING(agency)
),
fam AS (
  SELECT m.*,
    GREATEST(if(mk_round_1m,5,0), if(mk_round_100k,4,0), if(mk_whole_dollar,3,0))               AS f_round,
    GREATEST(if(mk_dup_diffday_disc,5,0), if(mk_dup_sameday_ext,3,0), if(mk_dup_diffday_all,2,0)) AS f_dup,
    GREATEST(if(mk_sole_source,4,0), if(mk_dependency,2,0))                                       AS f_conc,
    GREATEST(if(mk_newvendor_dominant,5,0), if(mk_short_tenure,4,0), if(mk_newvendor_large,3,0))  AS f_newvendor_raw,
    GREATEST(if(mk_p13_disc,4,0), if(mk_p13_all,3,0), if(mk_june_round,3,0))                      AS f_yearend,
    if(mk_peer_outlier,4,0)                                                                       AS f_peer,
    GREATEST(if(mk_triple_gap,5,0), if(mk_placeholder_payee,4,0), if(mk_no_contract,2,0), if(mk_offcontract_base,2,0)) AS f_account,
    GREATEST(if(mk_manual_disc,4,0), if(mk_manual_nonent,3,0), if(mk_null_warrant,2,0))           AS f_rail_raw,
    GREATEST(if(mk_person_name,3,0), if(mk_masked_payee,3,0))                                     AS f_entity_raw
  FROM m
),
scored AS (
  SELECT fam.*,
    -- FY2021 new-vendor halving (post-NULL-vid-gap re-appearance artifact)
    (CASE WHEN fiscal_year=2021 THEN CAST(floor(f_newvendor_raw/2.0) AS INT) ELSE f_newvendor_raw END) AS f_newvendor,
    -- cross-family dedup: triple_gap subsumes manual-rail & masked-payee components
    (CASE WHEN mk_triple_gap THEN 0 ELSE f_rail_raw END)                                          AS f_rail_net,
    (CASE WHEN mk_triple_gap THEN if(mk_person_name,3,0) ELSE f_entity_raw END)                   AS f_entity_net,
    if(agency_benford_flag AND amount>=100000 AND category1 NOT IN ('AID TO ORGANIZATIONS AND INDIVIDUALS','TRANSFERS OUT','DEBT SERVICE'),2,0) AS f_benford,
    -- documented category risk multiplier
    (CASE category1
       WHEN 'DEBT SERVICE' THEN 0.3
       WHEN 'AID TO ORGANIZATIONS AND INDIVIDUALS' THEN 0.4
       WHEN 'TRANSFERS OUT' THEN 0.4
       WHEN 'COST ALLOCATION AND INDIRECT COSTS' THEN 0.5
       WHEN 'CONVERSION' THEN 0.5
       WHEN 'PERSONAL SERVICES' THEN 0.7
       WHEN 'EMPLOYEE RELATED EXPENDITURES' THEN 0.9
       WHEN 'FOOD' THEN 0.9
       WHEN 'TRAVEL - IN-STATE' THEN 1.0
       WHEN 'TRAVEL - OUT-OF-STATE' THEN 1.0
       WHEN 'NON-CAPITAL EQUIPMENT' THEN 1.2
       WHEN 'CAPITAL EQUIPMENT' THEN 1.2
       WHEN 'CAPITAL OUTLAY' THEN 1.3
       WHEN 'OTHER OPERATING EXPENDITURES' THEN 1.4
       WHEN 'PROFESSIONAL AND OUTSIDE SERVICES' THEN 1.5
       ELSE 1.0 END)                                                                              AS category_multiplier
  FROM fam
),
final AS (
  SELECT scored.*,
    (f_round+f_dup+f_conc+f_newvendor+f_yearend+f_peer+f_account+f_rail_net+f_entity_net+f_benford) AS marker_sum,
    list_filter([
      if(mk_round_1m,'round_1m',NULL), if(mk_round_100k,'round_100k',NULL), if(mk_whole_dollar,'whole_dollar',NULL),
      if(mk_dup_diffday_disc,'dup_diffday_disc',NULL), if(mk_dup_sameday_ext,'dup_sameday_ext',NULL), if(mk_dup_diffday_all,'dup_diffday_all',NULL),
      if(mk_sole_source,'sole_source',NULL), if(mk_dependency,'vendor_dependency',NULL),
      if(mk_newvendor_dominant,'new_vendor_dominant',NULL), if(mk_short_tenure,'short_tenure_vendor',NULL), if(mk_newvendor_large,'new_vendor_large',NULL),
      if(mk_p13_disc,'period13_disc',NULL), if(mk_p13_all,'period13',NULL), if(mk_june_round,'june_round',NULL),
      if(mk_peer_outlier,'peer_outlier',NULL),
      if(mk_triple_gap,'triple_gap',NULL), if(mk_placeholder_payee,'placeholder_payee',NULL), if(mk_no_contract,'no_contract_named',NULL), if(mk_offcontract_base,'offcontract',NULL),
      if(NOT mk_triple_gap AND mk_manual_disc,'manual_rail_disc',NULL), if(NOT mk_triple_gap AND mk_manual_nonent AND NOT mk_manual_disc,'manual_rail',NULL), if(NOT mk_triple_gap AND mk_null_warrant AND NOT mk_manual_disc AND NOT mk_manual_nonent,'nonstd_rail',NULL),
      if(mk_person_name,'person_name_payee',NULL), if(NOT mk_triple_gap AND mk_masked_payee,'masked_payee',NULL),
      if(agency_benford_flag AND category1 NOT IN ('AID TO ORGANIZATIONS AND INDIVIDUALS','TRANSFERS OUT','DEBT SERVICE'),'agency_benford',NULL)
    ], x -> x IS NOT NULL) AS fired_markers
  FROM scored
)
SELECT
  record_number, transaction_id, transaction_reference_id, fiscal_year, posting_date,
  agency, org2, cabinet_name, category1, category2,
  appropriation_1_name, fund_1_name,
  payee, vendor_id_code, vid, contract_number, payment_method, fp_canon,
  amount, mag_band,
  vendor_share_of_agency_year, vendor_first_appearance, robust_z_in_agencycat,
  in_dup_diffday, agency_benford_flag,
  f_round, f_dup, f_conc, f_newvendor, f_yearend, f_peer, f_account, f_rail_net, f_entity_net, f_benford,
  marker_sum, category_multiplier,
  round(marker_sum * category_multiplier, 3) AS risk_score,
  fired_markers,
  len(fired_markers) AS n_markers,
  -- magnitude class
  (CASE WHEN amount >= 1000000 THEN 'MB_HIGH' ELSE 'MB_LOW' END) AS mag_class,
  -- risk band
  (CASE WHEN marker_sum*category_multiplier = 0 THEN 'R0_NONE'
        WHEN marker_sum*category_multiplier < 3 THEN 'R1_LOW'
        WHEN marker_sum*category_multiplier < 6 THEN 'R2_MODERATE'
        WHEN marker_sum*category_multiplier < 10 THEN 'R3_ELEVATED'
        ELSE 'R4_HIGH' END) AS risk_band,
  -- TIER (1 = top review priority ; 4 = high-value-but-clean ; 5 = unranked)
  (CASE
     WHEN amount>=1000000 AND marker_sum*category_multiplier >= 6 THEN 1
     WHEN (amount>=1000000 AND marker_sum*category_multiplier >= 3)
       OR (amount<1000000 AND marker_sum*category_multiplier >= 6) THEN 2
     WHEN marker_sum*category_multiplier > 0 THEN 3
     WHEN amount>=1000000 THEN 4
     ELSE 5 END) AS tier
FROM final;

-- ---------------------------------------------------------------------------
-- (3) SUMMARY
-- ---------------------------------------------------------------------------
.print '======== BENFORD-FLAGGED AGENCIES ========'
SELECT agency, printf('%.4f',mad1) AS mad1, printf('%.4f',mad2) AS mad2 FROM benford ORDER BY mad1 DESC;

.print ''
.print '======== TIER DISTRIBUTION ========'
SELECT tier,
  count(*) AS n_txn,
  printf('%.2f%%', 100.0*count(*)/sum(count(*)) OVER ()) AS pct,
  printf('%.0f', sum(amount)) AS exposure_usd,
  printf('%.2f', avg(risk_score)) AS avg_score,
  printf('%.2f', avg(n_markers)) AS avg_markers
FROM tx_tiered GROUP BY tier ORDER BY tier;

.print ''
.print '======== RISK SCORE DISTRIBUTION ========'
SELECT printf('%.2f',avg(risk_score)) AS mean,
  printf('%.2f',quantile_cont(risk_score,0.90)) AS p90,
  printf('%.2f',quantile_cont(risk_score,0.99)) AS p99,
  printf('%.2f',max(risk_score)) AS max,
  count(*) FILTER (WHERE risk_score=0) AS n_clean,
  printf('%.1f%%',100.0*count(*) FILTER (WHERE risk_score=0)/count(*)) AS pct_clean
FROM tx_tiered;
