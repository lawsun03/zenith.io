set -e
PY=.venv/Scripts/python.exe
BARS=bars/bars_MNQ_dbv_2021_2026.csv
OUT=research/equity_b99
for R in 1.0 1.5 2.0 2.5; do
  TAG=$(echo $R | tr '.' 'p')
  echo "================ ORB r=$R EQUITY+TRADES (risk 0.75) ================"
  $PY scripts/equity_export.py --bars $BARS --config NO_SUCH_CONFIG.json \
      --set engine=orb --set orb_r_multiple=$R --partial-r 0 --risk-pct 0.75 \
      --exclude-years 2022 \
      --out $OUT/orb_r${TAG}.csv \
      --trade-csv $OUT/trades_r${TAG}.csv 2> $OUT/_eq_r${TAG}.err
  for H in 0 200 400; do
    echo "--- funded_sim h=$H r=$R ---"
    $PY scripts/funded_sim.py $OUT/orb_r${TAG}.csv --haircut $H 2>/dev/null | head -6
  done > $OUT/_funded_r${TAG}.log
  echo "================ ORB r=$R COMBINE (risk 1.25) ================"
  $PY scripts/run_monthly_combine.py --bars $BARS --config NO_SUCH_CONFIG.json \
      --set engine=orb --set orb_r_multiple=$R --partial-r 0 --risk-pct 1.25 \
      2> $OUT/_combine_r${TAG}.err | tail -8 > $OUT/_combine_r${TAG}.log
  cat $OUT/_funded_r${TAG}.log
  cat $OUT/_combine_r${TAG}.log
done
echo "ALL DONE"
