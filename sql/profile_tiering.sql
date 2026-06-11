SET threads TO 16;
SET memory_limit = '90GB';
CREATE OR REPLACE MACRO is_sentinel_amt(a) AS (abs(a) BETWEEN 99999999 AND 100000000);
CREATE OR REPLACE MACRO real_vendor(v) AS (v IS NOT NULL AND v NOT IN ('00000000000000000000','MISCPAYVEND'));

CREATE OR REPLACE TEMP VIEW ex AS
SELECT * FROM transactions
WHERE transaction_type='EX' AND amount > 0 AND NOT is_sentinel_amt(amount);

.print '======== A. EXPENDITURE AMOUNT DISTRIBUTION (clean EX, amount>0, non-sentinel) ========'
SELECT
  count(*)                                              AS ex_rows,
  printf('%.0f', sum(amount))                           AS total_spend,
  printf('%.2f', quantile_cont(amount, 0.50))           AS p50,
  printf('%.2f', quantile_cont(amount, 0.90))           AS p90,
  printf('%.2f', quantile_cont(amount, 0.99))           AS p99,
  printf('%.2f', quantile_cont(amount, 0.999))          AS p999,
  printf('%.2f', quantile_cont(amount, 0.9999))         AS p9999,
  printf('%.2f', max(amount))                           AS max_amt
FROM ex;

.print ''
.print '======== B. HIGH-VALUE STRATA: count & $ above thresholds, and share of total spend ========'
WITH t AS (SELECT sum(amount) AS grand FROM ex)
SELECT thr,
  (SELECT count(*) FROM ex WHERE amount >= thr)                          AS n_txn,
  printf('%.0f', (SELECT sum(amount) FROM ex WHERE amount >= thr))       AS sum_at_or_above,
  printf('%.1f%%', 100.0*(SELECT sum(amount) FROM ex WHERE amount >= thr)/(SELECT grand FROM t)) AS pct_of_spend
FROM (VALUES (25000),(100000),(250000),(1000000),(5000000),(10000000),(50000000),(100000000)) AS v(thr)
ORDER BY thr;

.print ''
.print '======== C. FIELD COVERAGE BY YEAR (non-null %, clean EX) ========'
SELECT fiscal_year,
  count(*)                                                                       AS n,
  printf('%.0f%%', 100.0*count(payment_method)/count(*))                         AS pay_method,
  printf('%.0f%%', 100.0*count(contract_number)/count(*))                        AS contract_no,
  printf('%.0f%%', 100.0*count(appropriation_type)/count(*))                     AS approp_type,
  printf('%.0f%%', 100.0*count(fiscal_period)/count(*))                          AS fiscal_period,
  printf('%.0f%%', 100.0*count(object_no)/count(*))                              AS object_no,
  printf('%.0f%%', 100.0*count(category_level_1_name)/count(*))                  AS cat1,
  printf('%.0f%%', 100.0*count(*) FILTER (WHERE real_vendor(vendor_id_code))/count(*)) AS real_vendor,
  printf('%.0f%%', 100.0*count(transaction_reference_id)/count(*))              AS ref_id,
  printf('%.0f%%', 100.0*count(invoice_number)/count(*))                         AS invoice_no
FROM ex GROUP BY fiscal_year ORDER BY fiscal_year;

.print ''
.print '======== D. PAYMENT_METHOD distribution (clean EX) ========'
SELECT coalesce(payment_method,'(null)') AS payment_method, count(*) AS n,
  printf('%.0f', sum(amount)) AS total
FROM ex GROUP BY 1 ORDER BY n DESC LIMIT 25;

.print ''
.print '======== E. APPROPRIATION_TYPE distribution (clean EX) ========'
SELECT coalesce(appropriation_type,'(null)') AS appropriation_type, count(*) AS n,
  printf('%.0f', sum(amount)) AS total
FROM ex GROUP BY 1 ORDER BY n DESC LIMIT 25;

.print ''
.print '======== F. FISCAL_PERIOD distribution (clean EX) — look for adjustment/period-13 ========'
SELECT coalesce(fiscal_period,'(null)') AS fiscal_period, count(*) AS n,
  printf('%.0f', sum(amount)) AS total
FROM ex GROUP BY 1 ORDER BY n DESC LIMIT 30;

.print ''
.print '======== G. CATEGORY_LEVEL_1 for high-value (>= $1M) — what dominates the tail ========'
SELECT coalesce(category_level_1_name,'(null)') AS category, count(*) AS n,
  printf('%.0f', sum(amount)) AS total
FROM ex WHERE amount >= 1000000 GROUP BY 1 ORDER BY sum(amount) DESC LIMIT 25;

.print ''
.print '======== H. PROTECTION_INDICATOR + payment_method for high-value (>= $1M) ========'
SELECT coalesce(protection_indicator,'(null)') AS prot, coalesce(payment_method,'(null)') AS pay, count(*) AS n
FROM ex WHERE amount >= 1000000 GROUP BY 1,2 ORDER BY n DESC LIMIT 20;
