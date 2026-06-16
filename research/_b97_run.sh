#!/usr/bin/env bash
# B97 instrument-transfer: MGC + MES, 3 engines each. Deployed-style config,
# per-instrument %-of-price-rescaled overrides. 2022 excluded. risk 0.75 funded.
set -u
cd /c/Users/Lawrence/Documents/topstep-trader-bot/topstep-bot
PY=.venv/Scripts/python.exe
OUT=research/equity_b97
mkdir -p "$OUT"

run_instr () {
  local INST="$1" BARS="$2" SB="$3" MAB="$4"
  for V in combined orb ifvg; do
    local SETS="--set stop_buffer=$SB --set min_absolute_body=$MAB --set swing_stop_lookback=30"
    if [ "$V" = "orb" ]; then
      SETS="$SETS --set engine=orb --set orb_r_multiple=2.5"
    elif [ "$V" = "ifvg" ]; then
      SETS="$SETS --set engine=ifvg --set ifvg_entry_mode=close"
    else
      SETS="$SETS --set engine=combined --set ifvg_entry_mode=close"
    fi
    local CSV="$OUT/${INST}_${V}.csv"
    local TR="$OUT/${INST}_${V}_trades.csv"
    echo "### $INST $V — equity_export"
    $PY scripts/equity_export.py --bars "$BARS" --instrument "$INST" \
        --timeframe 5min --risk-pct 0.75 --partial-r 1.5 --killzones all \
        $SETS --out "$CSV" --trade-csv "$TR" --exclude-years 2022 2>/dev/null \
        | grep -E "wrote .* equity points"
    for H in 0 200 400; do
      echo "--- $INST $V funded h$H"
      $PY scripts/funded_sim.py "$CSV" --haircut $H --instrument "$INST" 2>/dev/null \
        | grep -iE "net pay|payout|bust|pass|account|funded|days|combine" | head -20
    done
  done
}

run_instr MGC bars/bars_MGC_GCv_2021_2026.csv 0.40 0.60 > "$OUT/log_MGC.txt" 2>&1 &
PID1=$!
run_instr MES bars/bars_MES_ESv_2024_2026.csv 1.00 1.75 > "$OUT/log_MES.txt" 2>&1 &
PID2=$!
wait $PID1 $PID2
echo "ALL DONE"
