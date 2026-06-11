-- ============================================================================
-- Export the high-value tiering feed for Overtaker ingestion.
-- Writes CSVs into overtaker_handoff/feed/. Run after tiering_v2 + build_rollups.
-- ============================================================================
SET threads TO 8;
CREATE OR REPLACE TEMP TABLE vv AS SELECT * FROM read_csv_auto('mart/vendor_verdicts.csv', header=true);

-- (1) ENTITY (agency) SCORECARD ------------------------------------------------
COPY (
  SELECT agency, cabinet, hv_txn, hv_exposure,
         n_tier1, usd_tier1, n_tier2, usd_tier2, n_tier3, n_flagged,
         tier12_exposure, tier12_pct_of_hv, avg_risk_score, max_risk_score,
         distinct_flagged_vendors, array_to_string(top_markers,'|') AS top_markers
  FROM tier_agency_summary ORDER BY usd_tier1 DESC
) TO 'overtaker_handoff/feed/tier_agency_scorecard.csv' (HEADER, DELIMITER ',');

-- (2) AGENCY x YEAR TREND (bondholder time-series) -----------------------------
COPY (
  SELECT agency, fiscal_year, hv_txn, hv_exposure, n_tier1, usd_tier1,
         n_tier12, usd_tier12, tier12_pct_of_hv, avg_risk_score
  FROM tier_agency_year ORDER BY agency, fiscal_year
) TO 'overtaker_handoff/feed/tier_agency_year.csv' (HEADER, DELIMITER ',');

-- (3) FLAGGED VENDORS (+ verification verdict where reviewed) -------------------
COPY (
  SELECT f.vid, f.payee, f.hv_exposure, f.flagged_exposure, f.n_flagged,
         f.n_tier1, f.usd_tier1, f.n_tier2, f.top_tier, f.max_risk_score,
         f.agencies_served, f.top_agency, f.first_year_seen, f.peak_agency_share,
         array_to_string(f.top_markers,'|') AS top_markers,
         vv.verdict AS verify_verdict, vv.overtaker_interest, vv.recommended_action,
         vv.public_context
  FROM tier_vendor_flagged f LEFT JOIN vv USING(vid)
  ORDER BY f.usd_tier1 DESC, f.max_risk_score DESC
) TO 'overtaker_handoff/feed/tier_vendor_flagged.csv' (HEADER, DELIMITER ',');

-- (4) MARQUEE TRANSACTIONS (Tier-1 leaderboard + verification status) ----------
COPY (
  SELECT t.rank, t.fiscal_year, t.posting_date, t.agency, t.category1, t.payee,
         t.appropriation_1_name, t.amount, t.risk_score, t.markers,
         coalesce(vv.verdict,'screened_unreviewed') AS verify_status,
         vv.overtaker_interest, vv.public_context
  FROM tier_top_transactions t LEFT JOIN vv ON vv.vid = t.vid
  ORDER BY t.rank
) TO 'overtaker_handoff/feed/tier_top_transactions.csv' (HEADER, DELIMITER ',');

-- (5) PROGRAM / APPROPRIATION ROLLUP -------------------------------------------
COPY (
  SELECT appropriation, lead_agency, hv_txn, hv_exposure, n_tier1, tier12_exposure,
         max_risk_score, distinct_vendors
  FROM tier_program_summary ORDER BY tier12_exposure DESC
) TO 'overtaker_handoff/feed/tier_program_summary.csv' (HEADER, DELIMITER ',');

-- (6) TIER DISTRIBUTION (overall + per year) -----------------------------------
COPY (
  SELECT 'ALL' AS fiscal_year, tier, count(*) AS n_txn, round(sum(amount),0) AS exposure
  FROM tx_tiered GROUP BY tier
  UNION ALL
  SELECT CAST(fiscal_year AS VARCHAR), tier, count(*), round(sum(amount),0)
  FROM tx_tiered GROUP BY fiscal_year, tier
  ORDER BY fiscal_year, tier
) TO 'overtaker_handoff/feed/tier_distribution.csv' (HEADER, DELIMITER ',');

.print 'feed CSVs written to overtaker_handoff/feed/';
