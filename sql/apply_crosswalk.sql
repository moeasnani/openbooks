-- Apply the parent-company crosswalk: rebuild vendor entities keyed by PARENT.
SET threads TO 8; SET lambda_syntax='ENABLE_SINGLE_ARROW';
CREATE OR REPLACE TEMP TABLE xwalk AS SELECT * FROM read_csv_auto('mart/entity_crosswalk.csv', header=true);
CREATE OR REPLACE TEMP TABLE vv AS SELECT *, upper(trim(payee)) ek FROM read_csv_auto('mart/vendor_verdicts.csv', header=true);

CREATE OR REPLACE TEMP TABLE named AS
SELECT t.*, upper(trim(regexp_replace(payee,'\s+',' ','g'))) entity_key, coalesce(cabinet_name,'(unassigned)') cab
FROM tx_tiered t
WHERE payee IS NOT NULL AND trim(payee)<>''
  AND upper(trim(payee)) NOT IN ('N/A','NULL','NONE','VARIOUS','UNKNOWN','MISCELLANEOUS VENDOR','MISCELLANEOUS CUSTOMER');

-- attach parent identity (fallback to self when not in a multi-name group)
CREATE OR REPLACE TEMP TABLE base AS
SELECT n.*, coalesce(x.parent_key, n.entity_key) pkey, coalesce(x.parent_name, n.payee) pname,
       coalesce(x.group_size,1) group_size
FROM named n LEFT JOIN xwalk x ON x.entity_key = n.entity_key;

-- verdict per parent (any reviewed child)
CREATE OR REPLACE TEMP TABLE pverdict AS
WITH vp AS (SELECT coalesce(x.parent_key, vv.ek) pkey, vv.verdict, vv.overtaker_interest, vv.public_context
            FROM vv LEFT JOIN xwalk x ON x.entity_key = vv.ek)
SELECT pkey, any_value(verdict) verdict, max(overtaker_interest) overtaker_interest, any_value(public_context) public_context
FROM vp WHERE verdict IS NOT NULL GROUP BY 1;

-- primary agency/cabinet per parent
CREATE OR REPLACE TEMP TABLE prim AS
WITH ea AS (SELECT pkey, agency, cab, sum(amount) ex FROM base GROUP BY 1,2,3)
SELECT pkey, arg_max(agency, ex) primary_agency, arg_max(cab, ex) primary_cabinet FROM ea GROUP BY 1;

-- ===== (1) FLAT entity rollup (one row per parent, flagged only) -> feed CSV =====
CREATE OR REPLACE TABLE tier_entities AS
WITH mk AS (
  WITH long AS (SELECT pkey, unnest(fired_markers) m FROM base WHERE len(fired_markers)>0),
       c AS (SELECT pkey, m, count(*) n FROM long GROUP BY 1,2),
       r AS (SELECT *, row_number() OVER (PARTITION BY pkey ORDER BY n DESC) rn FROM c)
  SELECT pkey, list(m ORDER BY rn) FILTER(rn<=5) top_markers FROM r GROUP BY 1
)
SELECT b.pkey AS entity_key, any_value(b.pname) AS entity_name,
       max(b.group_size) AS names_merged,
       count(DISTINCT b.vendor_id_code) AS n_vendor_ids,
       count(*) AS hv_txn, round(sum(b.amount),0) AS hv_exposure,
       count(*) FILTER(b.risk_score>0) AS n_flagged, round(sum(b.amount) FILTER(b.risk_score>0),0) AS flagged_exposure,
       count(*) FILTER(b.tier=1) AS n_tier1, round(sum(b.amount) FILTER(b.tier=1),0) AS usd_tier1,
       count(*) FILTER(b.tier=2) AS n_tier2, min(b.tier) FILTER(b.risk_score>0) AS top_tier,
       round(max(b.risk_score),2) AS max_risk_score, min(b.fiscal_year) AS first_year, max(b.fiscal_year) AS last_year,
       count(DISTINCT b.agency) AS n_agencies, list(DISTINCT b.agency) AS agencies,
       mk.top_markers, v.verdict AS verify_verdict, v.overtaker_interest, v.public_context
FROM base b LEFT JOIN mk USING(pkey) LEFT JOIN pverdict v USING(pkey)
GROUP BY b.pkey, mk.top_markers, v.verdict, v.overtaker_interest, v.public_context
HAVING count(*) FILTER(b.risk_score>0) > 0
ORDER BY usd_tier1 DESC, max_risk_score DESC;

COPY (
  SELECT entity_name, names_merged, n_vendor_ids, hv_txn, hv_exposure, n_flagged, flagged_exposure,
         n_tier1, usd_tier1, n_tier2, top_tier, max_risk_score, first_year, last_year, n_agencies,
         array_to_string(agencies,'|') agencies, array_to_string(top_markers,'|') top_markers,
         verify_verdict, overtaker_interest, public_context, entity_key
  FROM tier_entities ORDER BY usd_tier1 DESC, max_risk_score DESC
) TO 'overtaker_handoff/feed/tier_entities.csv' (HEADER, DELIMITER ',');

-- ===== (2) VENDOR-FIRST nodes (Tier 1/2, nested tx) -> mart json =====
COPY (
  WITH b2 AS (SELECT * FROM base WHERE tier IN (1,2)),
  txn AS (
    SELECT pkey, pname, agency, fiscal_year, posting_date, category1, amount, risk_score, tier,
           array_to_string(fired_markers,'|') markers, vendor_id_code,
           row_number() OVER (PARTITION BY pkey ORDER BY risk_score DESC, amount DESC) rn
    FROM b2
  ),
  agg AS (
    SELECT pkey, any_value(pname) entity_name, max(group_size) names_merged,
           count(*) n_txn, count(*) FILTER(tier=1) n_tier1, count(*) FILTER(tier=2) n_tier2,
           round(sum(amount),0) exposure, round(sum(amount) FILTER(tier=1),0) usd_tier1,
           min(tier) top_tier, round(max(risk_score),2) max_score,
           count(DISTINCT vendor_id_code) n_ids, count(DISTINCT agency) n_agencies,
           list(DISTINCT agency) agencies, min(fiscal_year) fy0, max(fiscal_year) fy1
    FROM b2 GROUP BY pkey
  ),
  mk AS (
    WITH long AS (SELECT pkey, unnest(fired_markers) m FROM b2 WHERE len(fired_markers)>0),
         c AS (SELECT pkey, m, count(*) n FROM long GROUP BY 1,2),
         r AS (SELECT *, row_number() OVER (PARTITION BY pkey ORDER BY n DESC) rn FROM c)
    SELECT pkey, list(m ORDER BY rn) FILTER(rn<=5) markers FROM r GROUP BY 1
  ),
  nested AS (
    SELECT pkey, list({fy:fiscal_year, date:posting_date, agency:agency, cat:category1, amount:amount, score:risk_score, tier:tier, markers:markers} ORDER BY rn) FILTER(rn<=30) transactions
    FROM txn GROUP BY 1
  )
  SELECT a.entity_name, a.names_merged, p.primary_agency, p.primary_cabinet, a.agencies, a.n_agencies, a.n_ids,
         a.n_txn, a.n_tier1, a.n_tier2, a.exposure, a.usd_tier1, a.top_tier, a.max_score, a.fy0, a.fy1,
         mk.markers top_markers, v.verdict, v.overtaker_interest, v.public_context, ns.transactions
  FROM agg a LEFT JOIN prim p USING(pkey) LEFT JOIN mk USING(pkey)
             LEFT JOIN nested ns USING(pkey) LEFT JOIN pverdict v USING(pkey)
  ORDER BY a.usd_tier1 DESC, a.max_score DESC
) TO 'mart/entity_nodes_vfirst.json' (FORMAT JSON, ARRAY true);

.print 'rebuilt:';
SELECT (SELECT count(*) FROM tier_entities) flat_entities,
       (SELECT count(*) FROM tier_entities WHERE names_merged>1) entities_with_merged_names,
       (SELECT count(*) FROM tier_entities WHERE n_tier1>0) tier1_entities;
