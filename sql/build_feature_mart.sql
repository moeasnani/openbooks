-- ============================================================================
-- FEATURE MART for high-value transaction tiering.
-- Output: mart/hv_base.parquet  (one row per clean EX transaction >= $25,000,
--   enriched with auditor features). Designed so the strategy panel can
--   calibrate markers against ~1M rows instead of scanning 92M.
-- ============================================================================
SET threads TO 16;
SET memory_limit = '90GB';
CREATE OR REPLACE MACRO is_sentinel_amt(a) AS (abs(a) BETWEEN 99999999 AND 100000000);
CREATE OR REPLACE MACRO real_vendor(v) AS (v IS NOT NULL AND v NOT IN ('00000000000000000000','MISCPAYVEND'));

-- Clean expenditure universe with canonicalized helper columns.
CREATE OR REPLACE TEMP VIEW exc AS
SELECT
  *,
  CASE WHEN real_vendor(vendor_id_code) THEN vendor_id_code END                  AS vid,
  TRY_CAST(split_part(coalesce(fiscal_period,''),'.',1) AS INTEGER)              AS fp_canon,
  NULLIF(regexp_replace(coalesce(appropriation_type,''),'\.0+$',''),'')          AS appr_type_canon,
  organization_level_1_name                                                      AS agency,
  category_level_1_name                                                          AS category1
FROM transactions
WHERE transaction_type='EX' AND amount > 0 AND NOT is_sentinel_amt(amount);

-- ---- population-level aggregates (computed over ALL clean EX) -------------
CREATE OR REPLACE TEMP TABLE vy AS                       -- vendor x year
SELECT vid, fiscal_year, sum(amount) AS vendor_year_total, count(*) AS vendor_year_txns
FROM exc WHERE vid IS NOT NULL GROUP BY 1,2;

CREATE OR REPLACE TEMP TABLE vfirst AS                   -- vendor first appearance year
SELECT vid, min(fiscal_year) AS vendor_first_year, count(DISTINCT fiscal_year) AS vendor_years_active
FROM exc WHERE vid IS NOT NULL GROUP BY 1;

CREATE OR REPLACE TEMP TABLE ay AS                       -- agency x year denominator
SELECT agency, fiscal_year, sum(amount) AS agency_year_total
FROM exc GROUP BY 1,2;

CREATE OR REPLACE TEMP TABLE vay AS                      -- vendor x agency x year (concentration)
SELECT vid, agency, fiscal_year, sum(amount) AS vendor_agency_year_total
FROM exc WHERE vid IS NOT NULL GROUP BY 1,2,3;

CREATE OR REPLACE TEMP TABLE apy AS                      -- appropriation x year
SELECT appropriation_1_code, fiscal_year,
       sum(amount) AS approp_year_total, count(*) AS approp_year_txns
FROM exc WHERE appropriation_1_code IS NOT NULL GROUP BY 1,2;

-- duplicate-payment signature: same vendor + reference + amount, >1 distinct txn id
CREATE OR REPLACE TEMP TABLE dup AS
SELECT vid, transaction_reference_id, amount,
       count(DISTINCT transaction_id)       AS dup_distinct_txn,
       count(*)                             AS dup_rows,
       (max(posting_date)-min(posting_date)) AS dup_day_span
FROM exc
WHERE vid IS NOT NULL AND transaction_reference_id IS NOT NULL
GROUP BY 1,2,3
HAVING count(DISTINCT transaction_id) > 1;

-- ---- high-value spine (>= $25,000) ---------------------------------------
CREATE OR REPLACE TEMP TABLE hv_raw AS
SELECT
  record_number, transaction_id, transaction_reference_id, invoice_number,
  fiscal_year, posting_date, fp_canon,
  agency,
  organization_level_2_name AS org2,
  cabinet_name,
  category1,
  category_level_2_name AS category2,
  appropriation_1_name, appropriation_1_code, appr_type_canon, appropriation_category_1_name,
  fund_1_name,
  payee_customer_vendor_name AS payee, payee_dba_name, vendor_id_code, vid,
  contract_number, contract_name,
  payment_method,
  object_no,
  amount
FROM exc
WHERE amount >= 25000;

-- agency x category robust stats over the >= $25K population (peer-outlier reference)
CREATE OR REPLACE TEMP TABLE accat AS
SELECT agency, category1,
       count(*)                              AS ac_n,
       median(amount)                        AS ac_median,
       quantile_cont(amount,0.25)            AS ac_q1,
       quantile_cont(amount,0.75)            AS ac_q3
FROM hv_raw GROUP BY 1,2;

-- ---- final enriched mart -------------------------------------------------
CREATE OR REPLACE TEMP TABLE hv_base AS
SELECT
  h.*,
  -- magnitude band
  CASE
    WHEN h.amount >= 100000000 THEN 'M1_100M+'
    WHEN h.amount >=  10000000 THEN 'M2_10M-100M'
    WHEN h.amount >=   1000000 THEN 'M3_1M-10M'
    WHEN h.amount >=    250000 THEN 'M4_250K-1M'
    WHEN h.amount >=    100000 THEN 'M5_100K-250K'
    ELSE 'M6_25K-100K'
  END                                                                            AS mag_band,
  -- round-number flags (whole-dollar and "psychological round" magnitudes)
  (h.amount = floor(h.amount))                                                   AS is_whole_dollar,
  (h.amount >= 1000     AND (h.amount % 1000)     = 0)                            AS is_round_1k,
  (h.amount >= 10000    AND (h.amount % 10000)    = 0)                            AS is_round_10k,
  (h.amount >= 100000   AND (h.amount % 100000)   = 0)                           AS is_round_100k,
  (h.amount >= 1000000  AND (h.amount % 1000000)  = 0)                           AS is_round_1m,
  CAST(left(replace(CAST(h.amount AS VARCHAR),'.',''),1) AS INTEGER)             AS first_digit,
  -- vendor features
  vy.vendor_year_total, vy.vendor_year_txns,
  vf.vendor_first_year, vf.vendor_years_active,
  (h.vid IS NOT NULL AND vf.vendor_first_year = h.fiscal_year)                    AS vendor_first_appearance,
  vay.vendor_agency_year_total,
  ay.agency_year_total,
  CASE WHEN ay.agency_year_total > 0 AND vay.vendor_agency_year_total IS NOT NULL
       THEN vay.vendor_agency_year_total / ay.agency_year_total END              AS vendor_share_of_agency_year,
  -- appropriation features
  apy.approp_year_total, apy.approp_year_txns,
  -- duplicate-signature features
  d.dup_distinct_txn, d.dup_rows, d.dup_day_span,
  (d.dup_distinct_txn IS NOT NULL)                                               AS in_dup_signature,
  (d.dup_distinct_txn IS NOT NULL AND d.dup_day_span > 0)                         AS in_dup_diffday,
  -- agency x category peer-outlier reference
  ac.ac_n, ac.ac_median, ac.ac_q1, ac.ac_q3,
  CASE WHEN (ac.ac_q3 - ac.ac_q1) > 0
       THEN (h.amount - ac.ac_median) / ((ac.ac_q3 - ac.ac_q1)/1.349) END        AS robust_z_in_agencycat
FROM hv_raw h
LEFT JOIN vy   ON vy.vid = h.vid AND vy.fiscal_year = h.fiscal_year
LEFT JOIN vfirst vf ON vf.vid = h.vid
LEFT JOIN vay  ON vay.vid = h.vid AND vay.agency = h.agency AND vay.fiscal_year = h.fiscal_year
LEFT JOIN ay   ON ay.agency = h.agency AND ay.fiscal_year = h.fiscal_year
LEFT JOIN apy  ON apy.appropriation_1_code = h.appropriation_1_code AND apy.fiscal_year = h.fiscal_year
LEFT JOIN dup  d ON d.vid = h.vid AND d.transaction_reference_id = h.transaction_reference_id AND d.amount = h.amount
LEFT JOIN accat ac ON ac.agency = h.agency AND ac.category1 = h.category1;

COPY hv_base TO 'mart/hv_base.parquet' (FORMAT PARQUET, COMPRESSION ZSTD);

.print '======== MART BUILT ========'
SELECT count(*) AS hv_rows,
       printf('%.0f', sum(amount)) AS hv_total,
       count(*) FILTER (WHERE amount>=100000) AS n_ge_100k,
       count(*) FILTER (WHERE in_dup_diffday) AS n_dup_diffday,
       count(*) FILTER (WHERE vendor_first_appearance) AS n_vendor_first,
       count(*) FILTER (WHERE vid IS NOT NULL) AS n_with_vendor
FROM hv_base;
