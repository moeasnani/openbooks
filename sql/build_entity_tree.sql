-- Agency × vendor-entity nodes (Tier 1/2) with nested transactions, for a browsable tree.
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

COPY (
  WITH txn AS (
    SELECT cab, agency, entity_key, payee, fiscal_year, posting_date, category1, amount, risk_score, tier,
           array_to_string(fired_markers,'|') markers, vendor_id_code,
           row_number() OVER (PARTITION BY agency, entity_key ORDER BY risk_score DESC, amount DESC) rn
    FROM base
  ),
  agg AS (
    SELECT cab, agency, entity_key, mode(payee) entity_name,
           count(*) n_txn, count(*) FILTER(tier=1) n_tier1, count(*) FILTER(tier=2) n_tier2,
           round(sum(amount),0) exposure, min(tier) top_tier, round(max(risk_score),2) max_score,
           count(DISTINCT vendor_id_code) n_ids, min(fiscal_year) fy0, max(fiscal_year) fy1
    FROM txn GROUP BY 1,2,3
  ),
  mk AS (
    WITH long AS (SELECT cab, agency, entity_key, unnest(fired_markers) m FROM base WHERE len(fired_markers)>0),
         c AS (SELECT cab, agency, entity_key, m, count(*) n FROM long GROUP BY 1,2,3,4),
         r AS (SELECT *, row_number() OVER (PARTITION BY cab,agency,entity_key ORDER BY n DESC) rn FROM c)
    SELECT cab, agency, entity_key, list(m ORDER BY rn) FILTER(rn<=4) markers FROM r GROUP BY 1,2,3
  ),
  nested AS (
    SELECT agency, entity_key,
      list({fy:fiscal_year, date:posting_date, cat:category1, amount:amount, score:risk_score, tier:tier, markers:markers} ORDER BY rn) FILTER(rn<=15) transactions
    FROM txn GROUP BY 1,2
  )
  SELECT a.cab cabinet, a.agency, a.entity_name, a.n_ids, a.n_txn, a.n_tier1, a.n_tier2, a.exposure,
         a.top_tier, a.max_score, a.fy0, a.fy1, mk.markers top_markers,
         v.verdict, v.overtaker_interest, v.public_context, ns.transactions
  FROM agg a
  LEFT JOIN mk     ON mk.cab=a.cab AND mk.agency=a.agency AND mk.entity_key=a.entity_key
  LEFT JOIN nested ns ON ns.agency=a.agency AND ns.entity_key=a.entity_key
  LEFT JOIN ent_verdict v ON v.entity_key=a.entity_key
  ORDER BY a.exposure DESC
) TO 'mart/entity_tree_nodes.json' (FORMAT JSON, ARRAY true);

.print 'nodes:';
SELECT count(*) nodes, count(DISTINCT agency) agencies, count(DISTINCT cab) cabinets,
       count(DISTINCT entity_key) entities FROM base;
