-- ============================================================================
-- AG FINDING → CHECKBOOK SPENDING CONTEXT
-- Links each AG audit finding to actual checkbook spending dimensions, creating
-- a program-level bridge between auditor findings and treasury payments.
--
-- Creates: ag_finding_context (one row per finding with enriched spending data)
-- Run: duckdb warehouse.duckdb < sql/build_ag_context.sql
-- ============================================================================
SET threads TO 16; SET memory_limit='90GB';

-- ---------------------------------------------------------------------------
-- Step 1: Per-finding actual agency spending (the FY of the audit)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE ag_finding_context AS
WITH finding_text AS (
  SELECT
    f.finding_id,
    f.report_id,
    r.agency_checkbook AS agency,
    r.fiscal_year,
    f.finding_text,
    f.recommendation_text,
    f.questioned_cost_usd,
    f.questioned_cost_basis,
    f.questioned_cost_confidence,
    f.has_adverse_findings,
    -- Combine all text for keyword extraction
    COALESCE(f.finding_text,'') || ' ' || COALESCE(f.recommendation_text,'') || ' ' || COALESCE(f.questioned_cost_basis,'') AS full_text
  FROM ag_findings f
  JOIN ag_reports r ON r.report_id = f.report_id
),
-- Extract program/fund references from finding text
program_refs AS (
  SELECT
    ft.*,
    CASE
      WHEN lower(ft.full_text) LIKE '%housing trust%' THEN 'HOUSING TRUST'
      WHEN lower(ft.full_text) LIKE '%heritage fund%' THEN 'HERITAGE'
      WHEN lower(ft.full_text) LIKE '%medical marijuana%' THEN 'MEDICAL MARIJUANA'
      WHEN lower(ft.full_text) LIKE '%unemployment insurance%' THEN 'UNEMPLOYMENT INSURANCE'
      WHEN lower(ft.full_text) LIKE '%lottery%' THEN 'LOTTERY'
      WHEN lower(ft.full_text) LIKE '%state general fund%' THEN 'GENERAL FUND'
      ELSE NULL
    END AS fund_keyword,
    CASE
      WHEN lower(ft.full_text) LIKE '%quality jobs%' THEN 'Quality Jobs tax credits'
      WHEN lower(ft.full_text) LIKE '%snap%' OR lower(ft.full_text) LIKE '%supplemental nutrition%' THEN 'SNAP / food assistance'
      WHEN lower(ft.full_text) LIKE '%unemployment insurance%' OR lower(ft.full_text) LIKE '%ui overpayment%' THEN 'Unemployment Insurance'
      WHEN lower(ft.full_text) LIKE '%master lease%' THEN 'Master lease agreements'
      WHEN lower(ft.full_text) LIKE '%housing trust%' THEN 'Housing Trust Fund'
      WHEN lower(ft.full_text) LIKE '%medical marijuana%' THEN 'Medical Marijuana Fund'
      WHEN lower(ft.full_text) LIKE '%heritage%' THEN 'Heritage Fund'
      WHEN lower(ft.full_text) LIKE '%conflict-of-interest%' THEN 'Conflict-of-interest compliance'
      WHEN lower(ft.full_text) LIKE '%procurement%' THEN 'Procurement compliance'
      WHEN lower(ft.full_text) LIKE '%contract%' AND lower(ft.full_text) LIKE '%oversight%' THEN 'Contract oversight'
      ELSE NULL
    END AS program_area
  FROM finding_text ft
),
-- Actual agency expenditure in the audit fiscal year
agency_spend AS (
  SELECT
    pr.*,
    (SELECT SUM(amount) FROM transactions
     WHERE transaction_type='EX'
       AND organization_level_1_name = pr.agency
       AND fiscal_year = pr.fiscal_year) AS actual_agency_expenditure,
    (SELECT SUM(amount) FROM transactions
     WHERE transaction_type='EX'
       AND organization_level_1_name = pr.agency
       AND fiscal_year BETWEEN pr.fiscal_year - 1 AND pr.fiscal_year + 1) AS actual_agency_3yr_expenditure
  FROM program_refs pr
),
-- Fund-specific spending (when we matched a fund keyword)
fund_spend AS (
  SELECT
    a.*,
    CASE WHEN a.fund_keyword IS NOT NULL THEN
      (SELECT SUM(amount) FROM transactions
       WHERE transaction_type='EX'
         AND organization_level_1_name = a.agency
         AND fiscal_year = a.fiscal_year
         AND fund_1_name LIKE '%' || a.fund_keyword || '%')
    END AS actual_fund_expenditure
  FROM agency_spend a
)
SELECT
  finding_id,
  report_id,
  agency,
  fiscal_year,
  finding_text,
  recommendation_text,
  questioned_cost_usd,
  questioned_cost_basis,
  questioned_cost_confidence,
  has_adverse_findings,
  fund_keyword,
  program_area,
  ROUND(actual_agency_expenditure, 0) AS actual_agency_expenditure,
  ROUND(actual_agency_3yr_expenditure, 0) AS actual_agency_3yr_expenditure,
  ROUND(actual_fund_expenditure, 0) AS actual_fund_expenditure,
  -- Ratio: questioned cost as % of agency expenditure (null-safe)
  CASE
    WHEN actual_agency_expenditure > 0 AND questioned_cost_usd IS NOT NULL
    THEN ROUND(100.0 * questioned_cost_usd / actual_agency_expenditure, 2)
  END AS questioned_pct_of_agency_spend,
  -- Ratio: questioned cost as % of fund expenditure (when fund matched)
  CASE
    WHEN actual_fund_expenditure > 0 AND questioned_cost_usd IS NOT NULL
    THEN ROUND(100.0 * questioned_cost_usd / actual_fund_expenditure, 2)
  END AS questioned_pct_of_fund_spend
FROM fund_spend
ORDER BY questioned_cost_usd DESC NULLS LAST, fiscal_year DESC;

-- ---------------------------------------------------------------------------
-- Step 2: Agency-level AG spending summary (for tier signal boost - task 6)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE ag_agency_spending AS
SELECT
  fc.agency,
  COUNT(DISTINCT fc.report_id) AS n_audit_reports,
  MIN(fc.fiscal_year) AS first_audit_fy,
  MAX(fc.fiscal_year) AS last_audit_fy,
  COUNT(*) AS n_findings,
  COUNT(*) FILTER (WHERE fc.questioned_cost_usd IS NOT NULL) AS n_findings_with_cost,
  COUNT(*) FILTER (WHERE fc.has_adverse_findings) AS n_adverse_findings,
  ROUND(COALESCE(SUM(fc.questioned_cost_usd), 0), 0) AS total_questioned_cost,
  -- Actual checkbook spending during audited years
  (SELECT ROUND(SUM(amount), 0) FROM transactions
   WHERE transaction_type='EX' AND organization_level_1_name = fc.agency
     AND fiscal_year BETWEEN MIN(fc.fiscal_year) AND MAX(fc.fiscal_year)
  ) AS actual_spend_during_audit_period,
  -- Most recent adverse finding year (for tier boost)
  MAX(fc.fiscal_year) FILTER (WHERE fc.has_adverse_findings) AS most_recent_adverse_fy
FROM ag_finding_context fc
GROUP BY fc.agency
ORDER BY total_questioned_cost DESC NULLS LAST;

-- ---------------------------------------------------------------------------
-- Verification output
-- ---------------------------------------------------------------------------
.print '======== AG FINDING CONTEXT ========'
SELECT
  COUNT(*) AS total_findings,
  COUNT(*) FILTER (WHERE questioned_cost_usd IS NOT NULL) AS with_questioned_cost,
  COUNT(*) FILTER (WHERE fund_keyword IS NOT NULL) AS with_fund_match,
  COUNT(*) FILTER (WHERE program_area IS NOT NULL) AS with_program_area,
  COUNT(*) FILTER (WHERE actual_agency_expenditure > 0) AS with_spend_data,
  printf('%.0f', SUM(questioned_cost_usd)) AS total_questioned
FROM ag_finding_context;

.print ''
.print '======== TOP 15 FINDINGS BY QUESTIONED COST (with spending context) ========'
SELECT
  finding_id,
  agency,
  fiscal_year AS fy,
  printf('$.0f', questioned_cost_usd) AS questioned,
  printf('$.0f', actual_agency_expenditure) AS agency_spend,
  printf('%.1f%%', questioned_pct_of_agency_spend) AS pct_of_spend,
  program_area,
  has_adverse_findings AS adverse
FROM ag_finding_context
WHERE questioned_cost_usd IS NOT NULL
ORDER BY questioned_cost_usd DESC
LIMIT 15;

.print ''
.print '======== AGENCY AG SPENDING SUMMARY ========'
SELECT
  agency,
  n_audit_reports AS reports,
  n_findings AS findings,
  n_adverse_findings AS adverse,
  printf('$.0f', total_questioned_cost) AS total_questioned,
  printf('$.0f', actual_spend_during_audit_period) AS actual_spend,
  most_recent_adverse_fy AS last_adverse_fy
FROM ag_agency_spending
WHERE total_questioned_cost > 0
ORDER BY total_questioned_cost DESC
LIMIT 15;
