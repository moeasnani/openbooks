#!/bin/bash
cd ~/Documents/Openbooks
LOG=sql/convert.log; : > "$LOG"
# file|variant|outtag
JOBS=(
"State of Arizona FY2016.csv|base38|FY2016"
"State of Arizona FY2017.csv|fy2017_18|FY2017"
"State of Arizona FY2018.csv|fy2017_18|FY2018"
"State of Arizona FY2019.csv|base38|FY2019"
"State of Arizona FY2020.csv|base38|FY2020"
"State of Arizona FY2021.csv|fy2021|FY2021"
"State_of_Arizona_FY23_000.csv|full57|FY2023_000"
"State_of_Arizona_FY23_001.csv|full57|FY2023_001"
"State_of_Arizona_FY24_000.csv|full57|FY2024_000"
"state_of_arizona_fy24_001.csv|full57|FY2024_001"
"State_of_Arizona_FY25_000.csv|full57|FY2025_000"
"State_of_Arizona_FY25_001.csv|full57|FY2025_001"
)
for j in "${JOBS[@]}"; do
  IFS='|' read -r infile variant tag <<< "$j"
  out="parquet/transactions_${tag}.parquet"
  echo "[$(date +%H:%M:%S)] START $tag ($variant) <- $infile" | tee -a "$LOG"
  proj=$(python3 sql/gen_projections.py "$variant" "$infile")
  {
    echo "CREATE OR REPLACE MACRO nz(x) AS NULLIF(NULLIF(trim(x), 'NULL'), '');"
    echo "SET threads TO 16; SET memory_limit='96GB';"
    echo "COPY ($proj) TO '$out' (FORMAT parquet, COMPRESSION zstd);"
  } | duckdb >>"$LOG" 2>&1
  if [ $? -eq 0 ]; then
    echo "[$(date +%H:%M:%S)] OK    $tag -> $out ($(du -h "$out" | cut -f1))" | tee -a "$LOG"
  else
    echo "[$(date +%H:%M:%S)] FAIL  $tag (see $LOG)" | tee -a "$LOG"
  fi
done
echo "ALL_DONE" | tee -a "$LOG"
