-- Vendor-first nodes: ONE row per canonical vendor (deduped across agencies AND vendor-IDs),
-- with a primary agency/cabinet, the full agency list, and transactions nested (top 30 by score).
SET threads TO 8; SET lambda_syntax='ENABLE_SINGLE_ARROW';
CREATE OR REPLACE TEMP TABLE vv AS SELECT *, upper(trim(payee)) entity_key FROM read_csv_auto('mart/vendor_verdicts.csv', header=true);
CREATE OR REPLACE TEMP TABLE ent_verdict AS
  SELECT entity_key, any_value(verdict) verdict, max(overtaker_interest) overtaker_interest, any_value(public_context) public_context
  FROM vv GROUP BY 1;
CREATE OR REPLACE TEMP TABLE base AS
  SELECT *, coalesce(cabinet_name,'(unassigned)') cab,
         upper(trim(regexp_replace(payee,'\s+',' ','g'))) entity_key
  FROM tx_tiered
  WHERE tier IN (1,2) AND payee IS NOT NULL AND trim(payee)<>''
    AND upper(trim(payee)) NOT IN ('N/A','NULL','NONE','VARIOUS','UNKNOWN','MISCELLANEOUS VENDOR','MISCELLANEOUS CUSTOMER');

-- primary agency/cabinet = where the vendor's flagged $ concentrates
CREATE OR REPLACE TEMP TABLE prim AS
WITH ea AS (SELECT entity_key, agency, cab, sum(amount) ex FROM base GROUP BY 1,2,3)
SELECT entity_key, arg_max(agency, ex) primary_agency, arg_max(cab, ex) primary_cabinet FROM ea GROUP BY 1;

COPY (
  WITH txn AS (
    SELECT entity_key, payee, agency, fiscal_year, posting_date, category1, amount, risk_score, tier,
           array_to_string(fired_markers,'|') markers, vendor_id_code,
           row_number() OVER (PARTITION BY entity_key ORDER BY risk_score DESC, amount DESC) rn
    FROM base
  ),
  agg AS (
    SELECT entity_key, mode(payee) entity_name,
           count(*) n_txn, count(*) FILTER(tier=1) n_tier1, count(*) FILTER(tier=2) n_tier2,
           round(sum(amount),0) exposure, round(sum(amount) FILTER(tier=1),0) usd_tier1,
           min(tier) top_tier, round(max(risk_score),2) max_score,
           count(DISTINCT vendor_id_code) n_ids, count(DISTINCT agency) n_agencies,
           list(DISTINCT agency) agencies, min(fiscal_year) fy0, max(fiscal_year) fy1
    FROM txn GROUP BY 1
  ),
  mk AS (
    WITH long AS (SELECT entity_key, unnest(fired_markers) m FROM base WHERE len(fired_markers)>0),
         c AS (SELECT entity_key, m, count(*) n FROM long GROUP BY 1,2),
         r AS (SELECT *, row_number() OVER (PARTITION BY entity_key ORDER BY n DESC) rn FROM c)
    SELECT entity_key, list(m ORDER BY rn) FILTER(rn<=5) markers FROM r GROUP BY 1
  ),
  nested AS (
    SELECT entity_key,
      list({fy:fiscal_year, date:posting_date, agency:agency, cat:category1, amount:amount, score:risk_score, tier:tier, markers:markers} ORDER BY rn) FILTER(rn<=30) transactions
    FROM txn GROUP BY 1
  )
  SELECT a.entity_name, p.primary_agency, p.primary_cabinet, a.agencies, a.n_agencies, a.n_ids,
         a.n_txn, a.n_tier1, a.n_tier2, a.exposure, a.usd_tier1, a.top_tier, a.max_score, a.fy0, a.fy1,
         mk.markers top_markers, v.verdict, v.overtaker_interest, v.public_context, ns.transactions
  FROM agg a
  LEFT JOIN prim p USING(entity_key)
  LEFT JOIN mk USING(entity_key)
  LEFT JOIN nested ns USING(entity_key)
  LEFT JOIN ent_verdict v USING(entity_key)
  ORDER BY a.usd_tier1 DESC, a.max_score DESC
) TO 'mart/entity_nodes_vfirst.json' (FORMAT JSON, ARRAY true);
.print 'vendor nodes:'; SELECT count(DISTINCT entity_key) FROM base;
