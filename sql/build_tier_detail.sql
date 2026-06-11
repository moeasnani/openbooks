SET threads TO 16; SET memory_limit='90GB';
-- Enrich tier-1/2/3 rows with transaction_description (+ contract_name) for verification agents.
CREATE OR REPLACE TABLE tier_detail AS
SELECT tt.fiscal_year, tt.posting_date, tt.agency, tt.cabinet_name, tt.category1, tt.category2,
       tt.payee, tt.vid, tt.vendor_id_code, tt.appropriation_1_name, tt.contract_number,
       tt.payment_method, tt.fp_canon, tt.amount, tt.mag_band, tt.risk_score, tt.tier,
       tt.fired_markers, tt.vendor_share_of_agency_year, tt.vendor_first_appearance, tt.robust_z_in_agencycat,
       tt.transaction_id, tt.transaction_reference_id,
       td.transaction_description, td.contract_name, td.invoice_number
FROM tx_tiered tt
LEFT JOIN (
  SELECT transaction_id, amount, posting_date, organization_level_1_name AS agency,
         any_value(transaction_description) AS transaction_description,
         any_value(contract_name)           AS contract_name,
         any_value(invoice_number)          AS invoice_number
  FROM transactions WHERE transaction_type='EX'
  GROUP BY 1,2,3,4
) td ON td.transaction_id=tt.transaction_id AND td.amount=tt.amount
     AND td.posting_date=tt.posting_date AND td.agency=tt.agency
WHERE tt.tier IN (1,2,3);

COPY tier_detail TO 'mart/tier_detail.parquet' (FORMAT PARQUET, COMPRESSION ZSTD);
.print '== tier_detail rows =='; SELECT count(*) AS n, count(transaction_description) AS with_desc FROM tier_detail;

.print '== VENDOR TARGETS (top 50 flagged vendors) =='
COPY (
  WITH ranked AS (
    SELECT vid, payee, usd_tier1, n_tier1, n_tier2, max_risk_score, agencies_served, first_year_seen,
           array_to_string(top_markers,',') AS markers,
           row_number() OVER (ORDER BY usd_tier1 DESC) AS r_usd,
           row_number() OVER (ORDER BY max_risk_score DESC, usd_tier1 DESC) AS r_score
    FROM tier_vendor_flagged
  )
  SELECT vid, payee, usd_tier1, n_tier1, n_tier2, max_risk_score, agencies_served, first_year_seen, markers
  FROM ranked WHERE r_usd<=40 OR r_score<=25
  ORDER BY usd_tier1 DESC
) TO 'mart/vendor_targets.csv' (HEADER, DELIMITER ',');

.print '== ACCOUNTABILITY-GAP AGENCY CLUSTERS (null-payee / triple-gap heavy) =='
COPY (
  SELECT agency,
         count(*) FILTER (tier IN (1,2)) AS n_tier12,
         round(sum(amount) FILTER (tier IN (1,2)),0) AS usd_tier12,
         count(*) FILTER (list_contains(fired_markers,'triple_gap')) AS n_triple_gap,
         count(*) FILTER (list_contains(fired_markers,'placeholder_payee')) AS n_placeholder,
         round(sum(amount) FILTER (list_contains(fired_markers,'triple_gap') OR list_contains(fired_markers,'placeholder_payee')),0) AS usd_gap
  FROM tx_tiered
  GROUP BY agency
  HAVING count(*) FILTER (list_contains(fired_markers,'triple_gap') OR list_contains(fired_markers,'placeholder_payee')) >= 20
  ORDER BY usd_gap DESC LIMIT 15
) TO 'mart/accountability_targets.csv' (HEADER, DELIMITER ',');
