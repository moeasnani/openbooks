CREATE OR REPLACE MACRO is_sentinel_amt(a) AS (abs(a) BETWEEN 99999999 AND 100000000);
CREATE OR REPLACE MACRO real_vendor(v) AS (v IS NOT NULL AND v NOT IN ('00000000000000000000','MISCPAYVEND','00000000000000000000'));

-- clean expenditure base
CREATE OR REPLACE TEMP VIEW ex AS
SELECT * FROM transactions
WHERE transaction_type='EX' AND amount > 0 AND NOT is_sentinel_amt(amount);

.print '################ 1. PER-AGENCY BENFORD DEVIATION (MAD of first digit, agencies >= 50k txns) ################'
.print '# MAD > 0.015 is commonly "nonconformity"; higher = more worth reviewing. Aggregate-clean data can still hide agency-level anomalies.'
WITH d AS (
  SELECT organization_level_1_name AS agency,
         CAST(left(replace(CAST(amount AS VARCHAR),'.',''),1) AS INTEGER) AS d1
  FROM ex
), dist AS (
  SELECT agency, d1, count(*) AS n, sum(count(*)) OVER (PARTITION BY agency) AS tot
  FROM d WHERE d1 BETWEEN 1 AND 9 GROUP BY agency, d1
)
SELECT agency,
  any_value(tot) AS txns,
  printf('%.4f', avg(abs(1.0*n/tot - log10(1.0+1.0/d1)))) AS benford_MAD
FROM dist GROUP BY agency
HAVING any_value(tot) >= 50000
ORDER BY benford_MAD DESC LIMIT 15;

.print ''
.print '################ 2. STRUCTURING: agencies with most "just-under-$5k" expenditures (4750-4999.99) ################'
SELECT organization_level_1_name AS agency,
  count(*) FILTER (WHERE amount >= 4750 AND amount < 5000) AS just_under_5k,
  count(*) FILTER (WHERE amount >= 5000 AND amount < 5250) AS just_over_5k,
  printf('%.2f', 1.0*count(*) FILTER (WHERE amount>=4750 AND amount<5000)/nullif(count(*) FILTER (WHERE amount>=5000 AND amount<5250),0)) AS under_over_ratio
FROM ex
GROUP BY agency
HAVING count(*) FILTER (WHERE amount >= 4750 AND amount < 5000) >= 500
ORDER BY under_over_ratio DESC LIMIT 15;

.print ''
.print '################ 3. INVOICE-LEVEL DUPLICATES (same vendor_id + invoice_number + amount, years w/ invoice: 2016-2018,2021) ################'
WITH dups AS (
  SELECT fiscal_year, vendor_id_code, invoice_number, amount, count(*) AS copies, max(posting_date) AS last_date
  FROM ex
  WHERE real_vendor(vendor_id_code) AND invoice_number IS NOT NULL
  GROUP BY ALL HAVING count(*) > 1
)
SELECT count(*) AS dup_groups, sum(copies-1) AS extra_payments,
  printf('%.0f', sum((copies-1)*amount)) AS potential_overpayment
FROM dups;
.print '--- top 12 invoice-level duplicate groups ---'
WITH dups AS (
  SELECT fiscal_year, vendor_id_code, invoice_number, amount, count(*) AS copies
  FROM ex WHERE real_vendor(vendor_id_code) AND invoice_number IS NOT NULL
  GROUP BY ALL HAVING count(*) > 1
)
SELECT fiscal_year, vendor_id_code, invoice_number, printf('%.2f',amount) AS amount, copies,
  printf('%.2f',(copies-1)*amount) AS extra
FROM dups ORDER BY (copies-1)*amount DESC LIMIT 12;

.print ''
.print '################ 4. YEAR-OVER-YEAR VENDOR SPIKES (vendor total jumped >= 10x and >= $5M, real vendors) ################'
WITH v AS (
  SELECT vendor_id_code, fiscal_year, sum(amount) AS yr_total
  FROM ex WHERE real_vendor(vendor_id_code) GROUP BY ALL
), j AS (
  SELECT vendor_id_code, fiscal_year, yr_total,
    lag(yr_total) OVER (PARTITION BY vendor_id_code ORDER BY fiscal_year) AS prev_total
  FROM v
)
SELECT vendor_id_code, fiscal_year,
  printf('%.0f', prev_total) AS prev_yr, printf('%.0f', yr_total) AS this_yr,
  printf('%.1fx', yr_total/nullif(prev_total,0)) AS jump
FROM j
WHERE prev_total > 0 AND yr_total >= 5e6 AND yr_total/prev_total >= 10
ORDER BY yr_total DESC LIMIT 15;

.print ''
.print '################ 5. PAYROLL: hourly_rate outliers (rate > $200/hr) ################'
SELECT
  count(*) FILTER (WHERE hourly_rate IS NOT NULL) AS rows_with_rate,
  printf('%.2f', max(hourly_rate)) AS max_rate,
  count(*) FILTER (WHERE hourly_rate > 200) AS over_200hr,
  count(*) FILTER (WHERE hourly_rate > 500) AS over_500hr
FROM transactions WHERE transaction_type='EX';
.print '--- top 10 hourly rates ---'
SELECT fiscal_year, organization_level_1_name AS agency, position_title, printf('%.2f',hourly_rate) AS hourly_rate
FROM transactions WHERE hourly_rate IS NOT NULL
ORDER BY hourly_rate DESC LIMIT 10;

.print ''
.print '################ 6. SENTINEL / DATA-QUALITY TALLY (the 99999999.xx placeholders, all years) ################'
SELECT fiscal_year,
  count(*) FILTER (WHERE is_sentinel_amt(amount)) AS sentinel_lines,
  printf('%.0f', sum(amount) FILTER (WHERE is_sentinel_amt(amount))) AS sentinel_amount
FROM transactions GROUP BY fiscal_year HAVING count(*) FILTER (WHERE is_sentinel_amt(amount))>0 ORDER BY 1;
