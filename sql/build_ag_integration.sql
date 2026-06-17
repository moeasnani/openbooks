-- ============================================================================
-- TASK 4: Reconcile orphaned ag_findings_xref
-- Parses report numbers from free-text fields and creates proper FK links
-- to ag_findings.finding_id.
--
-- TASK 6: Tier signal boost — add AG adverse finding flag to tier entities
-- Creates a view that flags vendors whose agency has adverse AG findings.
--
-- Run: duckdb warehouse.duckdb < sql/build_ag_integration.sql
-- ============================================================================
SET threads TO 16; SET lambda_syntax='ENABLE_SINGLE_ARROW';

-- ===========================================================================
-- TASK 4: Reconcile ag_findings_xref
-- ===========================================================================
-- The existing ag_findings_xref table has 8 rows mapping tier signals to AG
-- findings in free text (e.g. "2024-21,22,120"). Parse these into proper
-- finding_id references using a lateral unnest + left join.

CREATE OR REPLACE TABLE ag_findings_xref_resolved AS
WITH xref_parsed AS (
  SELECT
    x.our_signal,
    x.relationship,
    x.ag_finding AS finding_ref_text,
    x.ag_dollar,
    x.ag_detail,
    -- Extract report numbers and normalize to 2-digit year format
    regexp_extract_all(x.ag_finding, '\d{2,4}-\d{1,3}') AS raw_nums
  FROM ag_findings_xref x
),
xref_unnested AS (
  SELECT
    xp.our_signal,
    xp.relationship,
    xp.finding_ref_text,
    xp.ag_dollar,
    xp.ag_detail,
    -- Normalize: "2024-113" → "24-113"
    CASE WHEN length(split_part(unnested, '-', 1)) = 4
         THEN substring(split_part(unnested, '-', 1), 3, 2) || '-' || split_part(unnested, '-', 2)
         ELSE unnested
    END AS report_id_normalized
  FROM xref_parsed xp,
       UNNEST(xp.raw_nums) AS t(unnested)
),
-- Link to ag_findings via report_id
xref_with_findings AS (
  SELECT
    xu.our_signal,
    xu.relationship,
    xu.finding_ref_text,
    xu.ag_dollar,
    xu.ag_detail,
    xu.report_id_normalized,
    f.finding_id AS matched_finding_id
  FROM xref_unnested xu
  LEFT JOIN ag_findings f ON f.report_id = xu.report_id_normalized
)
SELECT
  our_signal,
  relationship,
  finding_ref_text,
  ag_dollar,
  ag_detail,
  list(DISTINCT report_id_normalized) AS report_ids,
  list(DISTINCT matched_finding_id) FILTER (matched_finding_id IS NOT NULL) AS matched_finding_ids
FROM xref_with_findings
GROUP BY our_signal, relationship, finding_ref_text, ag_dollar, ag_detail
ORDER BY our_signal;

-- ===========================================================================
-- TASK 6: Tier signal boost — AG adverse finding flag
-- ===========================================================================
-- Flag vendors whose primary agency has adverse AG findings. This doesn't
-- change the risk_score (the tiering model is adversarially calibrated), but
-- it surfaces an "AG concern" annotation that the UI can display alongside
-- the tier. The most_recent_adverse_fy enables temporal alignment (task 5):
-- a finding from FY2023 weighs more on FY2023 transactions than FY2025.

CREATE OR REPLACE VIEW tier_entities_ag_flag AS
SELECT
  te.*,
  COALESCE(
    (SELECT aas.most_recent_adverse_fy
     FROM ag_agency_spending aas
     WHERE aas.agency = te.agencies[1]
       AND aas.n_adverse_findings > 0),
    NULL
  ) AS ag_adverse_fy,
  COALESCE(
    (SELECT aas.total_questioned_cost
     FROM ag_agency_spending aas
     WHERE aas.agency = te.agencies[1]
       AND aas.n_adverse_findings > 0),
    0
  ) AS ag_total_questioned_cost,
  COALESCE(
    (SELECT aas.n_adverse_findings
     FROM ag_agency_spending aas
     WHERE aas.agency = te.agencies[1]
       AND aas.n_adverse_findings > 0),
    0
  ) AS ag_n_adverse_findings,
  -- Temporal alignment: is this entity active within ±1 year of an adverse finding?
  CASE WHEN COALESCE(
    (SELECT aas.most_recent_adverse_fy
     FROM ag_agency_spending aas
     WHERE aas.agency = te.agencies[1]
       AND aas.n_adverse_findings > 0),
    -999
  ) BETWEEN te.first_year - 1 AND te.last_year + 1 THEN 1 ELSE 0 END AS ag_temporal_match
FROM tier_entities te;

-- ===========================================================================
-- TASK 7: Export AG tables to overtaker feed bundle
-- ===========================================================================
COPY (SELECT * FROM ag_reports ORDER BY fiscal_year, report_id) TO 'overtaker_handoff/feed/ag_reports.csv' (HEADER, DELIMITER ',');
COPY (SELECT * FROM ag_findings ORDER BY questioned_cost_usd DESC NULLS LAST, report_id) TO 'overtaker_handoff/feed/ag_findings.csv' (HEADER, DELIMITER ',');
COPY (SELECT * FROM ag_finding_context ORDER BY questioned_cost_usd DESC NULLS LAST, fiscal_year DESC) TO 'overtaker_handoff/feed/ag_finding_context.csv' (HEADER, DELIMITER ',');
COPY (SELECT * FROM ag_agency_spending ORDER BY total_questioned_cost DESC NULLS LAST) TO 'overtaker_handoff/feed/ag_agency_spending.csv' (HEADER, DELIMITER ',');
COPY (SELECT * FROM ag_findings_xref_resolved ORDER BY our_signal) TO 'overtaker_handoff/feed/ag_findings_xref_resolved.csv' (HEADER, DELIMITER ',');

-- ===========================================================================
-- Verification
-- ===========================================================================
.print '======== TASK 4: XREF RECONCILIATION ========'
SELECT our_signal, relationship, finding_ref_text, report_ids, matched_finding_ids
FROM ag_findings_xref_resolved;

.print ''
.print '======== TASK 6: TIER AG FLAGS ========'
SELECT
  COUNT(*) AS total_entities,
  COUNT(*) FILTER (WHERE ag_adverse_fy IS NOT NULL) AS entities_with_ag_flag,
  COUNT(*) FILTER (WHERE ag_temporal_match = 1) AS entities_with_temporal_match,
  printf('%.0f', SUM(ag_total_questioned_cost)) AS total_questioned_in_flagged
FROM tier_entities_ag_flag;

.print ''
.print '======== TASK 7: FEED FILES ========'
.print 'Wrote to overtaker_handoff/feed/:'
.print '  ag_reports.csv'
.print '  ag_findings.csv'
.print '  ag_finding_context.csv'
.print '  ag_agency_spending.csv'
.print '  ag_findings_xref_resolved.csv'
