set -e
PY=.venv/Scripts/python.exe
BARS=bars/bars_MNQ_dbv_2021_2026.csv
for R in 1.0 1.5 2.0 2.5; do
  TAG=$(echo $R | tr '.' 'p')
  echo "================ ORB r=$R FUNDED (risk 0.75) ================"
  $PY scripts/equity_export.py --bars $BARS --config NO_SUCH_CONFIG.json \
      --set engine=orb --set orb_r_multiple=$R --partial-r 0 --risk-pct 0.75 \
      --exclude-years 2022 --out research/equity_b99/orb_r${TAG}.csv
  for H in 0 200 400; do
    echo "--- funded_sim h=$H r=$R ---"
    $PY scripts/funded_sim.py research/equity_b99/orb_r${TAG}.csv --haircut $H | tail -25
  done
  echo "================ ORB r=$R COMBINE (risk 1.25) ================"
  $PY scripts/run_monthly_combine.py --bars $BARS --config NO_SUCH_CONFIG.json \
      --set engine=orb --set orb_r_multiple=$R --partial-r 0 --risk-pct 1.25 | tail -15
done
echo "ALL DONE"
