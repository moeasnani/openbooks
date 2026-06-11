-- Unified view over all converted Parquet (always reflects whatever is in parquet/).
-- Guard against leaked header rows: only the two valid transaction types are kept.
CREATE OR REPLACE VIEW transactions AS
SELECT * FROM read_parquet('parquet/transactions_*.parquet')
WHERE transaction_type IN ('EX','RV');

-- Summary: agency x fund x top-category x month, split by revenue/expenditure
CREATE OR REPLACE TABLE spend_by_month AS
SELECT
  fiscal_year,
  transaction_type,
  organization_level_1_name AS agency,
  fund_1_name               AS fund,
  category_level_1_name     AS category,
  date_trunc('month', posting_date) AS month,
  count(*)        AS txn_count,
  sum(amount)     AS total_amount
FROM transactions
GROUP BY ALL;

-- Summary: vendor rollup per year (expenditures only)
CREATE OR REPLACE TABLE vendor_summary AS
SELECT
  fiscal_year,
  payee_customer_vendor_name AS vendor,
  vendor_id_code,
  count(*)    AS txn_count,
  sum(amount) AS total_amount
FROM transactions
WHERE transaction_type = 'EX'
GROUP BY ALL;

-- Summary: agency rollup per year, split by type
CREATE OR REPLACE TABLE agency_summary AS
SELECT
  fiscal_year,
  transaction_type,
  organization_level_1_name AS agency,
  count(*)    AS txn_count,
  sum(amount) AS total_amount
FROM transactions
GROUP BY ALL;
