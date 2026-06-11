-- ============================================================================
-- ENTITY ROLLUPS from tx_tiered — the "high-level interesting data" for Overtaker.
-- Entities: agency, vendor, appropriation/program. Plus the marquee transaction list.
-- Run: duckdb warehouse.duckdb < sql/build_rollups.sql
-- ============================================================================
SET threads TO 16;

-- marker long-form (one row per fired marker) for "top markers" rollups
CREATE OR REPLACE TEMP TABLE marker_long AS
SELECT agency, cabinet_name, vid, payee, appropriation_1_name, tier, risk_score, amount, unnest(fired_markers) AS marker
FROM tx_tiered WHERE len(fired_markers) > 0;

CREATE OR REPLACE TEMP TABLE agency_top_markers AS
WITH c AS (SELECT agency, marker, count(*) n, sum(amount) usd FROM marker_long GROUP BY 1,2),
     r AS (SELECT agency, marker, n, usd, row_number() OVER (PARTITION BY agency ORDER BY n DESC) rn FROM c)
SELECT agency, list(marker ORDER BY n DESC) FILTER (rn<=5) AS top_markers FROM r GROUP BY agency;

-- ---------------------------------------------------------------------------
-- (A) AGENCY SUMMARY  (entity = agency, all years)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE tier_agency_summary AS
SELECT
  t.agency,
  any_value(t.cabinet_name)                                            AS cabinet,
  count(*)                                                             AS hv_txn,
  round(sum(t.amount),0)                                              AS hv_exposure,
  count(*) FILTER (t.tier=1)                                          AS n_tier1,
  round(sum(t.amount) FILTER (t.tier=1),0)                            AS usd_tier1,
  count(*) FILTER (t.tier=2)                                          AS n_tier2,
  round(sum(t.amount) FILTER (t.tier=2),0)                            AS usd_tier2,
  count(*) FILTER (t.tier=3)                                          AS n_tier3,
  count(*) FILTER (t.risk_score>0)                                    AS n_flagged,
  round(sum(t.amount) FILTER (t.tier IN (1,2)),0)                     AS tier12_exposure,
  round(100.0*sum(t.amount) FILTER (t.tier IN (1,2))/nullif(sum(t.amount),0),2) AS tier12_pct_of_hv,
  round(avg(t.risk_score),3)                                         AS avg_risk_score,
  round(max(t.risk_score),2)                                         AS max_risk_score,
  count(DISTINCT t.vid) FILTER (t.risk_score>0 AND t.vid IS NOT NULL) AS distinct_flagged_vendors,
  am.top_markers
FROM tx_tiered t LEFT JOIN agency_top_markers am USING(agency)
GROUP BY t.agency, am.top_markers
ORDER BY usd_tier1 DESC;

-- ---------------------------------------------------------------------------
-- (B) AGENCY x YEAR TREND  (for the bondholder time-series panel)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE tier_agency_year AS
SELECT agency, fiscal_year,
  count(*) AS hv_txn,
  round(sum(amount),0) AS hv_exposure,
  count(*) FILTER (tier=1) AS n_tier1,
  round(sum(amount) FILTER (tier=1),0) AS usd_tier1,
  count(*) FILTER (tier IN (1,2)) AS n_tier12,
  round(sum(amount) FILTER (tier IN (1,2)),0) AS usd_tier12,
  round(100.0*sum(amount) FILTER (tier IN (1,2))/nullif(sum(amount),0),2) AS tier12_pct_of_hv,
  round(avg(risk_score),3) AS avg_risk_score
FROM tx_tiered GROUP BY agency, fiscal_year ORDER BY agency, fiscal_year;

-- ---------------------------------------------------------------------------
-- (C) FLAGGED VENDOR SUMMARY  (entity = vendor; the marquee list)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TEMP TABLE vendor_top_markers AS
WITH c AS (SELECT vid, marker, count(*) n FROM marker_long WHERE vid IS NOT NULL GROUP BY 1,2),
     r AS (SELECT vid, marker, n, row_number() OVER (PARTITION BY vid ORDER BY n DESC) rn FROM c)
SELECT vid, list(marker ORDER BY n DESC) FILTER (rn<=5) AS top_markers FROM r GROUP BY vid;

CREATE OR REPLACE TABLE tier_vendor_flagged AS
WITH base AS (
  SELECT vid,
    mode(payee)                          AS payee,
    count(*)                             AS hv_txn,
    round(sum(amount),0)                AS hv_exposure,
    count(*) FILTER (risk_score>0)       AS n_flagged,
    round(sum(amount) FILTER (risk_score>0),0) AS flagged_exposure,
    count(*) FILTER (tier=1)             AS n_tier1,
    round(sum(amount) FILTER (tier=1),0) AS usd_tier1,
    count(*) FILTER (tier=2)             AS n_tier2,
    min(tier)                            AS top_tier,
    round(max(risk_score),2)            AS max_risk_score,
    count(DISTINCT agency)               AS agencies_served,
    mode(agency)                         AS top_agency,
    min(fiscal_year)                     AS first_year_seen,
    max(vendor_share_of_agency_year)     AS peak_agency_share,
    bool_or(vendor_first_appearance)     AS ever_first_appearance
  FROM tx_tiered WHERE vid IS NOT NULL GROUP BY vid
)
SELECT b.*, vm.top_markers
FROM base b LEFT JOIN vendor_top_markers vm USING(vid)
WHERE b.n_tier1 > 0 OR b.n_tier2 > 0          -- marquee: any Tier-1/2 exposure
ORDER BY b.usd_tier1 DESC, b.max_risk_score DESC;

-- ---------------------------------------------------------------------------
-- (D) PROGRAM / APPROPRIATION SUMMARY  (entity = appropriation)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE tier_program_summary AS
SELECT
  coalesce(appropriation_1_name,'(no appropriation)') AS appropriation,
  any_value(agency) AS lead_agency,
  count(*) AS hv_txn,
  round(sum(amount),0) AS hv_exposure,
  count(*) FILTER (tier=1) AS n_tier1,
  round(sum(amount) FILTER (tier IN (1,2)),0) AS tier12_exposure,
  round(max(risk_score),2) AS max_risk_score,
  count(DISTINCT vid) FILTER (vid IS NOT NULL) AS distinct_vendors
FROM tx_tiered
GROUP BY 1 HAVING count(*) FILTER (tier=1) > 0
ORDER BY tier12_exposure DESC;

-- ---------------------------------------------------------------------------
-- (E) MARQUEE TRANSACTION LIST  (Tier-1, full detail + markers; verification slots)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE tier_top_transactions AS
SELECT
  row_number() OVER (ORDER BY risk_score DESC, amount DESC) AS rank,
  fiscal_year, posting_date, agency, cabinet_name, category1,
  payee, vid, vendor_id_code, appropriation_1_name, contract_number, payment_method, fp_canon,
  amount, mag_band, risk_score, tier, n_markers,
  array_to_string(fired_markers,',') AS markers,
  transaction_id, transaction_reference_id,
  CAST(NULL AS VARCHAR) AS verify_status,      -- filled by adversarial verification
  CAST(NULL AS VARCHAR) AS verify_note
FROM tx_tiered WHERE tier=1
ORDER BY risk_score DESC, amount DESC;

.print '======== AGENCY SUMMARY (top 15 by Tier-1 $) ========'
SELECT left(agency,28) AS agency, hv_txn, printf('%.0f',usd_tier1) AS usd_tier1, n_tier1,
  printf('%.1f%%',tier12_pct_of_hv) AS t12_pct, array_to_string(top_markers,',') AS top_markers
FROM tier_agency_summary ORDER BY usd_tier1 DESC LIMIT 15;
.print ''
.print '======== FLAGGED VENDORS (top 15 by Tier-1 $) ========'
SELECT left(payee,26) AS payee, printf('%.0f',usd_tier1) AS usd_tier1, n_tier1, top_tier,
  max_risk_score AS max_score, agencies_served AS agys, first_year_seen AS fy0,
  array_to_string(top_markers,',') AS markers
FROM tier_vendor_flagged ORDER BY usd_tier1 DESC LIMIT 15;
.print ''
.print '======== COUNTS ========'
SELECT (SELECT count(*) FROM tier_agency_summary) AS agencies,
       (SELECT count(*) FROM tier_vendor_flagged) AS flagged_vendors,
       (SELECT count(*) FROM tier_program_summary) AS programs_w_tier1,
       (SELECT count(*) FROM tier_top_transactions) AS marquee_txns;
