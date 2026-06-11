-- ============================================================================
-- CANONICAL VENDOR-ENTITY layer for elegant drill-down display.
-- Collapses (a) per-transaction repetition and (b) same-name/different-vendor_id
-- fragmentation into ONE row per vendor entity, with its transactions nested.
-- Output: tier_entities (flat) + entity_transactions (children) tables/CSVs.
-- ============================================================================
SET threads TO 8;
SET lambda_syntax='ENABLE_SINGLE_ARROW';
CREATE OR REPLACE TEMP TABLE vv AS SELECT *, upper(trim(payee)) AS entity_key FROM read_csv_auto('mart/vendor_verdicts.csv', header=true);

-- named transactions only; canonical key = normalized payee name (merges vid variants)
CREATE OR REPLACE TEMP TABLE ent_tx AS
SELECT *, upper(trim(regexp_replace(payee,'\s+',' ','g'))) AS entity_key
FROM tx_tiered
WHERE payee IS NOT NULL AND trim(payee) <> ''
  AND upper(trim(payee)) NOT IN ('N/A','NULL','NONE','VARIOUS','UNKNOWN','MISCELLANEOUS VENDOR','MISCELLANEOUS CUSTOMER');

-- top markers per entity
CREATE OR REPLACE TEMP TABLE ent_markers AS
WITH long AS (SELECT entity_key, unnest(fired_markers) AS marker FROM ent_tx WHERE len(fired_markers)>0),
     c AS (SELECT entity_key, marker, count(*) n FROM long GROUP BY 1,2),
     r AS (SELECT entity_key, marker, row_number() OVER (PARTITION BY entity_key ORDER BY n DESC) rn FROM c)
SELECT entity_key, list(marker ORDER BY rn) FILTER (rn<=5) AS top_markers FROM r GROUP BY entity_key;

-- one verdict per entity (any reviewed vid under this name)
CREATE OR REPLACE TEMP TABLE ent_verdict AS
SELECT entity_key, any_value(verdict) verdict, max(overtaker_interest) overtaker_interest,
       any_value(recommended_action) recommended_action, any_value(public_context) public_context
FROM vv GROUP BY entity_key;

-- ---- flat entity rollup (display: one row per vendor) ----------------------
CREATE OR REPLACE TABLE tier_entities AS
SELECT
  e.entity_key,
  mode(e.payee)                                            AS entity_name,
  count(DISTINCT e.vid) FILTER (e.vid IS NOT NULL)         AS n_vendor_ids,
  list(DISTINCT e.vendor_id_code) FILTER (e.vendor_id_code IS NOT NULL) AS vendor_id_variants,
  count(*)                                                 AS hv_txn,
  round(sum(e.amount),0)                                   AS hv_exposure,
  count(*) FILTER (e.risk_score>0)                         AS n_flagged,
  round(sum(e.amount) FILTER (e.risk_score>0),0)           AS flagged_exposure,
  count(*) FILTER (e.tier=1)                               AS n_tier1,
  round(sum(e.amount) FILTER (e.tier=1),0)                 AS usd_tier1,
  count(*) FILTER (e.tier=2)                               AS n_tier2,
  min(e.tier) FILTER (e.risk_score>0)                      AS top_tier,
  round(max(e.risk_score),2)                               AS max_risk_score,
  min(e.fiscal_year)                                       AS first_year,
  max(e.fiscal_year)                                       AS last_year,
  count(DISTINCT e.agency)                                 AS n_agencies,
  list(DISTINCT e.agency)                                  AS agencies,
  m.top_markers,
  v.verdict        AS verify_verdict,
  v.overtaker_interest,
  v.recommended_action,
  v.public_context
FROM ent_tx e
LEFT JOIN ent_markers m USING(entity_key)
LEFT JOIN ent_verdict v USING(entity_key)
GROUP BY e.entity_key, m.top_markers, v.verdict, v.overtaker_interest, v.recommended_action, v.public_context
HAVING count(*) FILTER (e.risk_score>0) > 0           -- only entities with >=1 flagged item
ORDER BY usd_tier1 DESC, max_risk_score DESC;

-- ---- child transactions (the drill-down rows) -----------------------------
CREATE OR REPLACE TABLE entity_transactions AS
SELECT entity_key, fiscal_year, posting_date, agency, category1, appropriation_1_name,
       vendor_id_code, amount, risk_score, tier, array_to_string(fired_markers,'|') AS markers
FROM ent_tx WHERE tier IN (1,2)
ORDER BY entity_key, risk_score DESC, amount DESC;

-- ---- exports ---------------------------------------------------------------
COPY (
  SELECT entity_name, n_vendor_ids, array_to_string(vendor_id_variants,'|') AS vendor_ids,
         hv_txn, hv_exposure, n_flagged, flagged_exposure, n_tier1, usd_tier1, n_tier2,
         top_tier, max_risk_score, first_year, last_year, n_agencies,
         array_to_string(agencies,'|') AS agencies, array_to_string(top_markers,'|') AS top_markers,
         verify_verdict, overtaker_interest, recommended_action, public_context, entity_key
  FROM tier_entities ORDER BY usd_tier1 DESC, max_risk_score DESC
) TO 'overtaker_handoff/feed/tier_entities.csv' (HEADER, DELIMITER ',');

.print '== entity counts ==';
SELECT count(*) AS flagged_entities,
       (SELECT count(*) FROM tier_entities WHERE n_vendor_ids>1) AS entities_merging_multiple_ids,
       (SELECT count(*) FROM tier_top_transactions) AS old_flat_tier1_rows,
       (SELECT count(*) FROM tier_entities WHERE n_tier1>0) AS new_tier1_entities
FROM tier_entities;
.print '== examples where >1 vendor_id was merged ==';
SELECT left(entity_name,30) entity, n_vendor_ids, array_to_string(vendor_id_variants,', ') ids, n_tier1
FROM tier_entities WHERE n_vendor_ids>1 ORDER BY usd_tier1 DESC LIMIT 8;
