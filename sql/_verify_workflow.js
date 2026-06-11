export const meta = {
  name: 'az-tiering-adversarial-verify',
  description: 'Adversarially verify the top-tier AZ transactions/entities: data-grounded verdicts on flagged vendors, accountability-gap clusters, and per-marker false-positive red-team, then synthesize reclassifications + the Overtaker interesting-entities shortlist',
  phases: [
    { title: 'Verify', detail: 'vendor verdicts + accountability-cluster review + per-marker FP red-team' },
    { title: 'Synthesize', detail: 'compile reclassifications, marker fixes, and marquee interesting entities' },
  ],
}

const VENDORS = [{"vid": "IV0000002957", "payee": "BLUE CROSS BLUE SHIELD OF AZ INC", "usd_tier1": "1134471189", "n_tier1": "59", "max_score": "11.2", "agencies": "1", "fy0": "2021", "markers": "offcontract,new_vendor_large,new_vendor_dominant,peer_outlier,whole_dollar"}, {"vid": "VC0000004547", "payee": "UNITED HEALTHCARE", "usd_tier1": "1109162379", "n_tier1": "158", "max_score": "21.0", "agencies": "1", "fy0": "2016", "markers": "offcontract,sole_source,peer_outlier,nonstd_rail,whole_dollar"}, {"vid": "VC0000023979", "payee": "PUBLIC SAFETY PERSONNEL RETIREMENT SYSTEM", "usd_tier1": "1005000000", "n_tier1": "14", "max_score": "7.2", "agencies": "4", "fy0": "2016", "markers": "june_round,round_1m,round_100k,agency_benford"}, {"vid": "IV0000039519", "payee": "NAPHCARE INC", "usd_tier1": "981916190", "n_tier1": "88", "max_score": "21.0", "agencies": "1", "fy0": "2023", "markers": "sole_source,whole_dollar,peer_outlier,new_vendor_large,new_vendor_dominant"}, {"vid": "VC0000010584", "payee": "BANK OF AMERICA - AZ", "usd_tier1": "598333834", "n_tier1": "225", "max_score": "19.6", "agencies": "3", "fy0": "2016", "markers": "offcontract,whole_dollar,vendor_dependency,peer_outlier,round_100k"}, {"vid": "IV0000034229", "payee": "PULICE - FNF - FLATIRON JOINT VENTURE", "usd_tier1": "468325615", "n_tier1": "42", "max_score": "11.7", "agencies": "1", "fy0": "2021", "markers": "offcontract,no_contract_named,peer_outlier,whole_dollar,new_vendor_large"}, {"vid": "PZ000051407", "payee": "CONNECT 202 PARTNERS", "usd_tier1": "425432000", "n_tier1": "32", "max_score": "11.7", "agencies": "1", "fy0": "2016", "markers": "offcontract,no_contract_named,peer_outlier,whole_dollar,nonstd_rail"}, {"vid": "PZ000012471", "payee": "BLUE CROSS BLUE SHIELD OF AZ INC", "usd_tier1": "416125185", "n_tier1": "36", "max_score": "8.4", "agencies": "1", "fy0": "2016", "markers": "offcontract,peer_outlier,whole_dollar"}, {"vid": "IV0000053764", "payee": "KIEWIT-FANN JOINT VENTURE", "usd_tier1": "315527881", "n_tier1": "49", "max_score": "15.6", "agencies": "1", "fy0": "2023", "markers": "offcontract,no_contract_named,peer_outlier,new_vendor_large,whole_dollar"}, {"vid": "PZ000004410", "payee": "THE UNIVERSITY OF ARIZONA", "usd_tier1": "233982250", "n_tier1": "3", "max_score": "9.8", "agencies": "10", "fy0": "2016", "markers": "nonstd_rail,offcontract,whole_dollar,peer_outlier"}, {"vid": "IV0000046838", "payee": "ASHBRITT INC", "usd_tier1": "194730663", "n_tier1": "15", "max_score": "30.0", "agencies": "1", "fy0": "2023", "markers": "new_vendor_large,new_vendor_dominant,short_tenure_vendor,sole_source,peer_outlier"}, {"vid": "11368201", "payee": "ARIZONA STATE UNIVERSITY", "usd_tier1": "158035000", "n_tier1": "2", "max_score": "13.5", "agencies": "2", "fy0": "2016", "markers": "peer_outlier,whole_dollar,offcontract,sole_source,june_round"}, {"vid": "PZ000001945", "payee": "SECURITY TITLE AGENCY INC", "usd_tier1": "136584671", "n_tier1": "24", "max_score": "14.3", "agencies": "1", "fy0": "2016", "markers": "nonstd_rail,offcontract,whole_dollar,no_contract_named,peer_outlier"}, {"vid": "IV0000001650", "payee": "UNITED HEALTHCARE SERVICES INC", "usd_tier1": "121438766", "n_tier1": "15", "max_score": "11.2", "agencies": "2", "fy0": "2021", "markers": "offcontract,new_vendor_large,new_vendor_dominant,peer_outlier,whole_dollar"}, {"vid": "IV0000012155", "payee": "CENTURION OF ARIZONA LLC", "usd_tier1": "86467637", "n_tier1": "14", "max_score": "15.0", "agencies": "2", "fy0": "2021", "markers": "short_tenure_vendor,new_vendor_large,new_vendor_dominant,offcontract,no_contract_named"}, {"vid": "IV0000000997", "payee": "ARIZONA STATE UNIVERSITY", "usd_tier1": "85445141", "n_tier1": "27", "max_score": "19.5", "agencies": "13", "fy0": "2021", "markers": "offcontract,new_vendor_large,peer_outlier,whole_dollar,no_contract_named"}, {"vid": "IV0000002317", "payee": "FNF CONSTRUCTION INC", "usd_tier1": "77524854", "n_tier1": "16", "max_score": "9.1", "agencies": "1", "fy0": "2021", "markers": "offcontract,no_contract_named,new_vendor_large,whole_dollar,peer_outlier"}, {"vid": "IV0000000572", "payee": "SECURITY TITLE AGENCY INC", "usd_tier1": "75902507", "n_tier1": "17", "max_score": "14.3", "agencies": "1", "fy0": "2021", "markers": "offcontract,whole_dollar,new_vendor_large,no_contract_named,peer_outlier"}, {"vid": "IV0000039359", "payee": "VIZIENT INC", "usd_tier1": "72080770", "n_tier1": "19", "max_score": "9.0", "agencies": "1", "fy0": "2021", "markers": "short_tenure_vendor,new_vendor_large,peer_outlier"}, {"vid": "VC0000069791", "payee": "GBC PROPERTIES LLC", "usd_tier1": "71182163", "n_tier1": "5", "max_score": "14.3", "agencies": "1", "fy0": "2024", "markers": "offcontract,no_contract_named,peer_outlier,whole_dollar"}, {"vid": "PZ000001572", "payee": "NORTHERN ARIZONA UNIVERSITY", "usd_tier1": "68828306", "n_tier1": "6", "max_score": "8.4", "agencies": "7", "fy0": "2016", "markers": "offcontract,nonstd_rail,peer_outlier"}, {"vid": "IV0000023826", "payee": "COFFMAN AMES JOINT VENTURE", "usd_tier1": "65732589", "n_tier1": "9", "max_score": "13.0", "agencies": "1", "fy0": "2021", "markers": "short_tenure_vendor,offcontract,new_vendor_large,no_contract_named,peer_outlier"}, {"vid": "IV0000006558", "payee": "FISHER SAND AND GRAVEL CO", "usd_tier1": "65474192", "n_tier1": "10", "max_score": "7.8", "agencies": "1", "fy0": "2021", "markers": "offcontract,no_contract_named,new_vendor_large,whole_dollar,peer_outlier"}, {"vid": "IV0000057235", "payee": "SUNDT CS A JOINT VENTURE", "usd_tier1": "59325723", "n_tier1": "12", "max_score": "10.4", "agencies": "1", "fy0": "2023", "markers": "offcontract,no_contract_named,new_vendor_large,peer_outlier,whole_dollar"}, {"vid": "IV0000000009", "payee": "GRANITE CONSTRUCTION CO", "usd_tier1": "50516007", "n_tier1": "9", "max_score": "15.0", "agencies": "1", "fy0": "2021", "markers": "offcontract,no_contract_named,new_vendor_large,whole_dollar,peer_outlier"}, {"vid": "IV0000005722", "payee": "PUBLIC CONSULTING GROUP INC", "usd_tier1": "44251482", "n_tier1": "12", "max_score": "9.0", "agencies": "1", "fy0": "2023", "markers": "offcontract,no_contract_named,peer_outlier"}, {"vid": "IV0000061315", "payee": "LOCAL INITIATIVES SUPPORT CORPORATION", "usd_tier1": "39642178", "n_tier1": "6", "max_score": "16.5", "agencies": "3", "fy0": "2023", "markers": "nonstd_rail,offcontract,new_vendor_large,peer_outlier,no_contract_named"}, {"vid": "VC0000003001", "payee": "DRIGGS TITLE AGENCY INC", "usd_tier1": "38731970", "n_tier1": "5", "max_score": "14.3", "agencies": "2", "fy0": "2021", "markers": "offcontract,whole_dollar,no_contract_named,peer_outlier,agency_benford"}, {"vid": "PZ000010983", "payee": "MARICOPA COUNTY", "usd_tier1": "34181396", "n_tier1": "10", "max_score": "14.3", "agencies": "19", "fy0": "2016", "markers": "offcontract,nonstd_rail,whole_dollar,no_contract_named,peer_outlier"}, {"vid": "IV0000030792", "payee": "SUNDT CONSTRUCTION INC", "usd_tier1": "30616414", "n_tier1": "4", "max_score": "7.8", "agencies": "1", "fy0": "2021", "markers": "offcontract,new_vendor_large,no_contract_named,peer_outlier"}, {"vid": "VC0000010109", "payee": "ZEITLIN AND ZEITLIN PC", "usd_tier1": "29570795", "n_tier1": "3", "max_score": "16.9", "agencies": "1", "fy0": "2016", "markers": "offcontract,no_contract_named,nonstd_rail,whole_dollar,peer_outlier"}, {"vid": "IV0000006755", "payee": "THE WEITZ CO", "usd_tier1": "27177386", "n_tier1": "17", "max_score": "13.0", "agencies": "1", "fy0": "2021", "markers": "sole_source,short_tenure_vendor,offcontract,new_vendor_large,new_vendor_dominant"}, {"vid": "IV0000000903", "payee": "CGI TECHNOLOGIES AND SOLUTIONS INC", "usd_tier1": "26836091", "n_tier1": "17", "max_score": "18.0", "agencies": "1", "fy0": "2021", "markers": "whole_dollar,peer_outlier,new_vendor_large,round_1m,offcontract"}, {"vid": "VC0000006143", "payee": "DEPARTMENT OF HEALTH AND HUMAN SERVICES", "usd_tier1": "26177413", "n_tier1": "4", "max_score": "11.2", "agencies": "3", "fy0": "2016", "markers": "offcontract,nonstd_rail,sole_source,whole_dollar,peer_outlier"}, {"vid": "IV0000000310", "payee": "DELOITTE CONSULTING", "usd_tier1": "26149962", "n_tier1": "7", "max_score": "12.0", "agencies": "2", "fy0": "2021", "markers": "nonstd_rail,whole_dollar,dup_sameday_ext,peer_outlier,offcontract"}, {"vid": "IV0000008944", "payee": "COFFMAN SPECIALTIES INC", "usd_tier1": "25284362", "n_tier1": "4", "max_score": "7.8", "agencies": "1", "fy0": "2025", "markers": "offcontract,no_contract_named,peer_outlier"}, {"vid": "IV0000009965", "payee": "GEO SECURE SERVICES LLC", "usd_tier1": "24488640", "n_tier1": "23", "max_score": "7.5", "agencies": "2", "fy0": "2021", "markers": "whole_dollar,new_vendor_large,offcontract,nonstd_rail"}, {"vid": "IV0000016996", "payee": "MOTOROLA SOLUTIONS INC", "usd_tier1": "24203918", "n_tier1": "10", "max_score": "15.4", "agencies": "8", "fy0": "2021", "markers": "nonstd_rail,agency_benford,new_vendor_large,peer_outlier,whole_dollar"}, {"vid": "VC0000080576", "payee": "CRE-WPL ESTRELLA JV LLC", "usd_tier1": "24010375", "n_tier1": "2", "max_score": "15.6", "agencies": "1", "fy0": "2024", "markers": "short_tenure_vendor,new_vendor_large,offcontract,peer_outlier,no_contract_named"}, {"vid": "VC0000004097", "payee": "J AND H MARSH AND MCLENNAN", "usd_tier1": "22998018", "n_tier1": "9", "max_score": "9.8", "agencies": "1", "fy0": "2016", "markers": "offcontract,nonstd_rail,whole_dollar"}, {"vid": "VC0000022758", "payee": "STATE TREASURER", "usd_tier1": "21033883", "n_tier1": "15", "max_score": "19.6", "agencies": "2", "fy0": "2016", "markers": "person_name_payee,offcontract,nonstd_rail,round_100k,whole_dollar"}, {"vid": "IV0000016232", "payee": "GCOM SOFTWARE LLC", "usd_tier1": "17785919", "n_tier1": "7", "max_score": "19.6", "agencies": "1", "fy0": "2024", "markers": "agency_benford,peer_outlier,new_vendor_large,whole_dollar,new_vendor_dominant"}, {"vid": "PZ000057277", "payee": "ACTIVE RESOURCE MANAGEMENT, LLC", "usd_tier1": "12500000", "n_tier1": "1", "max_score": "26.6", "agencies": "1", "fy0": "2017", "markers": "vendor_dependency,offcontract,new_vendor_dominant,sole_source,round_100k"}, {"vid": "IV0000000660", "payee": "PITNEY BOWES", "usd_tier1": "11572900", "n_tier1": "9", "max_score": "26.6", "agencies": "4", "fy0": "2023", "markers": "person_name_payee,offcontract,nonstd_rail,round_100k,whole_dollar"}, {"vid": "IV0000002963", "payee": "BANNER HEALTH", "usd_tier1": "10000000", "n_tier1": "1", "max_score": "21.0", "agencies": "2", "fy0": "2021", "markers": "round_1m,no_contract_named,peer_outlier,nonstd_rail,offcontract"}, {"vid": "IV0000082779", "payee": "ARIZONA JEWISH HISTORICAL SOCIETY", "usd_tier1": "7000000", "n_tier1": "1", "max_score": "22.5", "agencies": "1", "fy0": "2025", "markers": "new_vendor_large,nonstd_rail,short_tenure_vendor,round_1m,peer_outlier"}, {"vid": "VC0000082646", "payee": "FONDOMONTE ARIZONA LLC", "usd_tier1": "7000000", "n_tier1": "1", "max_score": "30.8", "agencies": "1", "fy0": "2025", "markers": "new_vendor_large,nonstd_rail,round_1m,new_vendor_dominant,offcontract"}, {"vid": "IV0000055200", "payee": "DONORSCHOOSE ORG", "usd_tier1": "5736826", "n_tier1": "2", "max_score": "21.0", "agencies": "1", "fy0": "2023", "markers": "new_vendor_large,nonstd_rail,person_name_payee,short_tenure_vendor,round_1m"}, {"vid": "PZ000033197", "payee": "PULICE CONSTRUCTION INC", "usd_tier1": "2000000", "n_tier1": "1", "max_score": "21.0", "agencies": "1", "fy0": "2016", "markers": "offcontract,no_contract_named,whole_dollar,june_round,peer_outlier"}, {"vid": "PZ000053526", "payee": "KIEWIT DEVELOPMENT COMPANY", "usd_tier1": "2000000", "n_tier1": "1", "max_score": "24.0", "agencies": "1", "fy0": "2016", "markers": "offcontract,june_round,no_contract_named,peer_outlier,nonstd_rail"}, {"vid": "VC0000078458", "payee": "TRULAW PC", "usd_tier1": "1900000", "n_tier1": "1", "max_score": "19.6", "agencies": "1", "fy0": "2024", "markers": "person_name_payee,offcontract,new_vendor_large,nonstd_rail,round_100k"}, {"vid": "VC0000063751", "payee": "NAME REDACTED", "usd_tier1": "1690400", "n_tier1": "1", "max_score": "19.6", "agencies": "1", "fy0": "2021", "markers": "nonstd_rail,new_vendor_large,peer_outlier,whole_dollar,agency_benford"}, {"vid": "IV0000001534", "payee": "KINELLA CONSTRUCTION LLC", "usd_tier1": "1500000", "n_tier1": "1", "max_score": "22.4", "agencies": "2", "fy0": "2023", "markers": "offcontract,sole_source,round_100k,nonstd_rail,peer_outlier"}, {"vid": "VC0000045869", "payee": "WEIL GOTSHAL & MANGES LLP", "usd_tier1": "1000000", "n_tier1": "1", "max_score": "21.0", "agencies": "1", "fy0": "2018", "markers": "new_vendor_large,offcontract,june_round,nonstd_rail,round_100k"}, {"vid": "PZ000023536", "payee": "SEATTLE UNIVERSITY SCHOOL OF LAW", "usd_tier1": "", "n_tier1": "0", "max_score": "19.6", "agencies": "1", "fy0": "2018", "markers": "round_100k,new_vendor_large,nonstd_rail,june_round,offcontract"}, {"vid": "VC0000074486", "payee": "KELLY & LYONS PLLC", "usd_tier1": "", "n_tier1": "0", "max_score": "19.6", "agencies": "1", "fy0": "2023", "markers": "round_100k,nonstd_rail,offcontract,new_vendor_large,june_round"}, {"vid": "VC0000067061", "payee": "RASMUSSEN INJURY LAW PLLC", "usd_tier1": "", "n_tier1": "0", "max_score": "19.6", "agencies": "1", "fy0": "2024", "markers": "offcontract,new_vendor_large,round_100k,june_round,nonstd_rail"}, {"vid": "VC0000083776", "payee": "IRCS CENTER FOR ECONOMIC OPPORTUNITY INC", "usd_tier1": "", "n_tier1": "0", "max_score": "21.0", "agencies": "1", "fy0": "2025", "markers": "offcontract,june_round,nonstd_rail,round_100k,new_vendor_large"}, {"vid": "IV0000004992", "payee": "HAGENS BERMAN SOBOL SHAPIRO LLP", "usd_tier1": "", "n_tier1": "0", "max_score": "21.0", "agencies": "1", "fy0": "2023", "markers": "agency_benford,whole_dollar,new_vendor_large,offcontract,peer_outlier"}, {"vid": "MISCCUSTOM", "payee": "MISCELLANEOUS CUSTOMER", "usd_tier1": "", "n_tier1": "0", "max_score": "24.0", "agencies": "5", "fy0": "2016", "markers": "offcontract,nonstd_rail,masked_payee,placeholder_payee,round_100k"}];
const AGENCIES = [{"agency": "DEPT OF ECONOMIC SECURITY", "n_tier12": "3746", "usd_gap": "1791204557", "n_triple_gap": "475", "n_placeholder": "3392"}, {"agency": "DEPT OF TRANSPORTATION", "n_tier12": "4530", "usd_gap": "1603612084", "n_triple_gap": "1523", "n_placeholder": "2299"}, {"agency": "DEPT OF ADMINISTRATION", "n_tier12": "2194", "usd_gap": "975350842", "n_triple_gap": "433", "n_placeholder": "818"}, {"agency": "LOTTERY COMMISSION", "n_tier12": "2037", "usd_gap": "746786556", "n_triple_gap": "471", "n_placeholder": "631"}, {"agency": "DEPT OF CORRECTIONS", "n_tier12": "1542", "usd_gap": "672282617", "n_triple_gap": "262", "n_placeholder": "653"}, {"agency": "DEPT OF CHILD SAFETY", "n_tier12": "1415", "usd_gap": "530537622", "n_triple_gap": "253", "n_placeholder": "1337"}, {"agency": "DEPT OF HEALTH SERVICES", "n_tier12": "1229", "usd_gap": "511335664", "n_triple_gap": "628", "n_placeholder": "718"}, {"agency": "ARIZONA STATE RETIREMENT SYSTEM (ASRS)", "n_tier12": "850", "usd_gap": "382077046", "n_triple_gap": "532", "n_placeholder": "681"}, {"agency": "AHCCCS", "n_tier12": "1006", "usd_gap": "346728045", "n_triple_gap": "641", "n_placeholder": "777"}, {"agency": "DEPT OF PUBLIC SAFETY", "n_tier12": "1116", "usd_gap": "192544738", "n_triple_gap": "510", "n_placeholder": "690"}, {"agency": "SUPREME COURT", "n_tier12": "688", "usd_gap": "192067120", "n_triple_gap": "515", "n_placeholder": "622"}, {"agency": "DEPT OF EMERGENCY AND MILITARY AFFAIRS", "n_tier12": "224", "usd_gap": "128652458", "n_triple_gap": "69", "n_placeholder": "84"}, {"agency": "EARLY CHILDHOOD DEVELOP AND HEALTH BOARD", "n_tier12": "386", "usd_gap": "105618834", "n_triple_gap": "214", "n_placeholder": "357"}, {"agency": "DEPT OF REVENUE", "n_tier12": "318", "usd_gap": "89143077", "n_triple_gap": "100", "n_placeholder": "157"}, {"agency": "DEPT OF EDUCATION", "n_tier12": "483", "usd_gap": "79828041", "n_triple_gap": "127", "n_placeholder": "161"}];

const FRAME = `
You are a forensic auditor VERIFYING leads from an automated transaction-tiering model on the
State of Arizona checkbook (overtaker.ai, for municipal bondholders). DISCIPLINE (the team's hard
rule): "READ the appropriation / description / contract — NEVER infer a reason." A flag is a LEAD,
never a finding of wrongdoing; your job is to decide, from the DATA, whether each lead is:
  - genuine_review      : a real, review-worthy item (discretionary, concentrated, unusual) that a
                          bondholder/auditor would legitimately want to see — KEEP it.
  - explained_benign    : the data explains it as routine (entitlement/Medicaid capitation, pension
                          paydown, debt service, intergovernmental transfer, scheduled disbursement,
                          a documented major contract) — DOWNWEIGHT/annotate.
  - false_positive_marker: a marker MISFIRED for a structural reason (e.g. payee 'PITNEY BOWES' tripped
                          the person-name regex; 'offcontract' fired only because contract_number is
                          ~4% populated; a vendor looks 'new' in FY2021 only because vendor_id was NULL
                          in FY2019-2020) — flag the marker to fix.
  - mixed               : some of the entity's flagged items are genuine, some explained.

KNOWN STRUCTURAL CAVEATS you must apply: (1) FY2019 & FY2020 have NULL vendor_id system-wide, so any
vendor whose first_year_seen is 2021 may be an OLD vendor re-surfacing, not new. (2) contract_number
is only ~2-6% populated, so 'offcontract'/'no_contract_named' is weak alone. (3) intergovernmental
payments (to universities, counties, other state agencies, the US Treasury/HHS, water districts,
airport authorities) are usually legitimate IGAs/pass-throughs. (4) Medicaid managed-care (AHCCCS ->
health plans), pension (PSPRS/ASRS), Lottery (-> banks), and debt service are benign-by-structure.

DATA ACCESS (run from /Users/moeasnani/Documents/Openbooks; query ONLY these parquet files, never warehouse.duckdb):
  - mart/tier_detail.parquet : every Tier-1/2/3 transaction with transaction_description, appropriation_1_name,
    contract_name, contract_number, category1, payment_method, amount, risk_score, tier, fired_markers,
    vendor_share_of_agency_year, robust_z_in_agencycat, vid, payee, agency, fiscal_year.
  - mart/hv_base.parquet     : ALL high-value (>=$25K) rows (use for a vendor's full history / context).
Example:
  duckdb -c "SELECT fiscal_year,agency,category1,left(appropriation_1_name,40) ap,left(transaction_description,50) d,contract_number,payment_method,printf('%.0f',amount) amt,risk_score,array_to_string(fired_markers,',') m FROM read_parquet('mart/tier_detail.parquet') WHERE vid='THEVID' ORDER BY amount DESC LIMIT 40"
`;

const VENDOR_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['vid','payee','verdict','confidence','data_explanation','false_positive_markers','public_context','overtaker_interest','recommended_action'],
  properties:{
    vid:{type:'string'}, payee:{type:'string'},
    verdict:{type:'string', enum:['genuine_review','explained_benign','false_positive_marker','mixed']},
    confidence:{type:'string', enum:['high','medium','low']},
    data_explanation:{type:'string', description:'what the appropriation/description/contract/pattern actually shows — grounded, specific'},
    false_positive_markers:{type:'array', items:{type:'string'}, description:'markers that misfired for this entity (empty if none)'},
    public_context:{type:'string', description:'1-2 sentences of real-world context IF this is a recognizable named private entity (at most ONE quick web search); else empty string'},
    overtaker_interest:{type:'number', description:'1-5, how notable at a high level for bondholders'},
    recommended_action:{type:'string', enum:['keep','downgrade','annotate','suppress_marker']},
  },
};
const AGENCY_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['agency','verdict','is_systemic_convention','data_explanation','overtaker_interest','note'],
  properties:{
    agency:{type:'string'},
    verdict:{type:'string', enum:['genuine_review','explained_benign','mixed']},
    is_systemic_convention:{type:'boolean', description:'are the null-payee/manual-rail items a routine accounting convention rather than a per-item concern?'},
    data_explanation:{type:'string'},
    overtaker_interest:{type:'number'},
    note:{type:'string'},
  },
};
const REDTEAM_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['marker_family','keep_marker','est_false_positive_share','fp_patterns','recommended_suppressions','notes'],
  properties:{
    marker_family:{type:'string'},
    keep_marker:{type:'boolean'},
    est_false_positive_share:{type:'string', description:'rough % of fires that look benign-by-structure'},
    fp_patterns:{type:'array', items:{type:'object', additionalProperties:false, required:['pattern','example','why_benign'],
      properties:{pattern:{type:'string'}, example:{type:'string'}, why_benign:{type:'string'}}}},
    recommended_suppressions:{type:'array', items:{type:'string'}, description:'concrete additional guardrails (exclusions) to add'},
    notes:{type:'string'},
  },
};

const REDTEAM_FAMILIES = [
  {key:'round', desc:'round_1m / round_100k / whole_dollar — round/negotiated amounts. Find benign round payers (grants booked round, debt service, scheduled allotments that slipped the category guard).'},
  {key:'duplicate', desc:'dup_diffday_disc / dup_sameday_ext — repeated vendor+reference+amount. Find legitimate batch/installment structures wrongly caught.'},
  {key:'concentration', desc:'sole_source / vendor_dependency — high vendor share of agency. Find benign monopolies (sole legit provider, utility, the agency itself, IGAs).'},
  {key:'new_vendor', desc:'new_vendor_dominant / new_vendor_large / short_tenure — first-appearance vendors. CRITICAL: quantify how many are FY2021 vendor_id-gap artifacts (old vendors resurfacing).'},
  {key:'yearend', desc:'period13 / period13_disc / june_round — closing/year-end timing. Find legitimate accruals/true-ups.'},
  {key:'peer_outlier', desc:'peer_outlier (robust_z>=20 within agency x category). Find categories where extreme z is normal (lumpy capital, one big legit contract among small ones).'},
  {key:'manual_rail', desc:'manual_rail_disc / manual_rail / nonstd_rail — JV/INTERNAL/NULL/WARRANT payment method. Find legitimate inter-fund / cost-allocation uses slipping the guard.'},
  {key:'triple_gap', desc:'triple_gap (null vid + manual rail + discretionary). Decide: genuine transparency gap or routine internal-accounting convention? Look at descriptions.'},
  {key:'placeholder_payee', desc:'placeholder_payee (N/A / MISC / null payee on discretionary). Find where null payee is a system convention vs a real masked external payment.'},
  {key:'offcontract', desc:'offcontract / no_contract_named (contract_number NULL). Quantify how weak this is given ~4% contract coverage; identify which agencies never populate it.'},
  {key:'person_name', desc:'person_name_payee (two-word caps payee). Find FALSE POSITIVES: companies that look like person names (PITNEY BOWES, MOTOROLA SOLUTIONS?, two-word firms). List them.'},
  {key:'benford', desc:'agency_benford (6 flagged agencies). Sanity-check: is the digit nonconformity explained by a few round-number programs, or genuinely diffuse?'},
];

// ---------------------------------------------------------------------------
phase('Verify');

const vendorThunks = VENDORS.map(v => () =>
  agent(
    `${FRAME}
VENDOR TO VERIFY: payee="${v.payee}"  vid=${v.vid}
Model stats: Tier-1 $${v.usd_tier1} across ${v.n_tier1} txns; max risk_score ${v.max_score}; serves ${v.agencies} agency(ies); first_year_seen ${v.fy0}; top markers: ${v.markers}.
Pull this vendor's flagged rows from mart/tier_detail.parquet (and its full history from mart/hv_base.parquet if useful). Read the appropriations / descriptions / contract names. Decide the verdict grounded in what the data SAYS. If recognizable & named & private, you MAY do ONE quick web search for public context (skip for govt/bank/health-plan). Return a vendor verdict.`,
    { label:`vendor:${(v.payee||v.vid).slice(0,22)}`, phase:'Verify', schema:VENDOR_SCHEMA, agentType:'general-purpose' }
  ).then(r => r ? {...r, _kind:'vendor'} : null)
);

const agencyThunks = AGENCIES.map(a => () =>
  agent(
    `${FRAME}
ACCOUNTABILITY-GAP CLUSTER TO REVIEW: agency="${a.agency}"
This agency has ${a.n_triple_gap} 'triple_gap' (null-vendor + manual-rail + discretionary) and ${a.n_placeholder} 'placeholder_payee' high-value items, ~$${a.usd_gap} combined. Pull a representative sample from mart/tier_detail.parquet (WHERE agency='${a.agency}' AND (list_contains(fired_markers,'triple_gap') OR list_contains(fired_markers,'placeholder_payee'))). Read the transaction_description / appropriation / category. DECIDE: are these a routine internal-accounting convention (inter-fund moves, cost allocations, redacted-payee programs) — i.e. a data-transparency texture — or genuinely review-worthy missing-accountability items? Return an agency verdict.`,
    { label:`gap:${a.agency.slice(0,20)}`, phase:'Verify', schema:AGENCY_SCHEMA, agentType:'general-purpose' }
  ).then(r => r ? {...r, _kind:'agency'} : null)
);

const redteamThunks = REDTEAM_FAMILIES.map(f => () =>
  agent(
    `${FRAME}
RED-TEAM MARKER FAMILY: ${f.key}
${f.desc}
Pull a sample of rows where this family fired from mart/tier_detail.parquet (filter fired_markers with list_contains). Hunt for SYSTEMATIC false positives — benign-by-structure patterns the marker catches by mistake. Quantify roughly how often it misfires, give concrete examples (payee + why benign), and propose precise additional SQL guardrails (exclusions) that would suppress the FPs WITHOUT killing the genuine signal. Be adversarial: your goal is to break the marker. Return a red-team report.`,
    { label:`redteam:${f.key}`, phase:'Verify', schema:REDTEAM_SCHEMA, agentType:'general-purpose' }
  ).then(r => r ? {...r, _kind:'redteam'} : null)
);

const all = (await parallel([...vendorThunks, ...agencyThunks, ...redteamThunks])).filter(Boolean);
const vendorVerdicts = all.filter(x => x._kind==='vendor');
const agencyVerdicts = all.filter(x => x._kind==='agency');
const redteam = all.filter(x => x._kind==='redteam');
log(`Verified ${vendorVerdicts.length} vendors, ${agencyVerdicts.length} gap-clusters, ${redteam.length} marker red-teams`);

phase('Synthesize');
const SYNTH_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['verification_summary','reclassifications','marker_fixes','marquee_interesting_entities','overtaker_headline_findings'],
  properties:{
    verification_summary:{type:'string'},
    reclassifications:{type:'array', items:{type:'object', additionalProperties:false, required:['entity','from','to','reason'],
      properties:{entity:{type:'string'}, from:{type:'string'}, to:{type:'string'}, reason:{type:'string'}}}},
    marker_fixes:{type:'array', items:{type:'object', additionalProperties:false, required:['marker','fix','severity'],
      properties:{marker:{type:'string'}, fix:{type:'string'}, severity:{type:'string'}}}},
    marquee_interesting_entities:{type:'array', items:{type:'object', additionalProperties:false,
      required:['name','kind','exposure','tier','why_interesting','public_context'],
      properties:{name:{type:'string'}, kind:{type:'string'}, exposure:{type:'string'}, tier:{type:'string'},
        why_interesting:{type:'string'}, public_context:{type:'string'}}}},
    overtaker_headline_findings:{type:'array', items:{type:'string'}},
  },
};
const model = await agent(
  `${FRAME}
You are the VERIFICATION SYNTHESIZER. Below are all data-grounded verdicts. Compile them.

VENDOR VERDICTS (${vendorVerdicts.length}):
${JSON.stringify(vendorVerdicts, null, 0)}

ACCOUNTABILITY-GAP VERDICTS (${agencyVerdicts.length}):
${JSON.stringify(agencyVerdicts, null, 0)}

MARKER RED-TEAM (${redteam.length}):
${JSON.stringify(redteam, null, 0)}

Produce: (1) a verification_summary; (2) reclassifications (entities whose tier/flag should change, with reason);
(3) marker_fixes (concrete guardrail changes ranked by severity — e.g. person_name FP fix, FY2021 new-vendor
artifact, offcontract weakness); (4) marquee_interesting_entities — the SHORTLIST a bondholder should actually
see at a high level (the genuine_review items with real public salience: Fondomonte, AshBritt, prison-healthcare
concentration, highway-JV capital, redacted/individual payees, etc.), each with exposure, tier, why it's
interesting, and public context; (5) overtaker_headline_findings — 5-8 crisp, neutral, source-grounded headline
takeaways for the product. Keep the neutral "leads not findings" framing throughout.`,
  { label:'synthesize:verification', phase:'Synthesize', schema:SYNTH_SCHEMA, agentType:'general-purpose' }
);

return { vendorVerdicts, agencyVerdicts, redteam, model };
