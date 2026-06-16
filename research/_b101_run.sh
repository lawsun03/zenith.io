#!/usr/bin/env bash
# B101 Fib-extension TARGET sweep. equity_export (risk 0.75, partial_r 0, 2022 excluded)
# + per-trade CSV + funded_sim h0/200/400. iFVG = novel; ORB = cross-check vs B99.
set -e
cd "$(dirname "$0")/.."
PY=.venv/Scripts/python.exe
BARS=bars/bars_MNQ_dbv_2021_2026.csv
OUT=research/equity_b101
mkdir -p "$OUT"

EXTS="1.272 1.414 1.618 2.0 2.618"

run() {  # engine, fibparam, tag, extra_set
  local eng="$1" fibp="$2" tag="$3" ext="$4"
  local sets="--set engine=$eng"
  [ -n "$ext" ] && sets="$sets --set ${fibp}=${ext}"
  $PY scripts/equity_export.py --bars "$BARS" --instrument MNQ --timeframe 5min \
    --risk-pct 0.75 --partial-r 0 --exclude-years 2022 $sets \
    --out "$OUT/eq_${tag}.csv" --trade-csv "$OUT/trades_${tag}.csv" \
    > "$OUT/_eq_${tag}.log" 2>&1
  for h in 0 200 400; do
    $PY scripts/funded_sim.py "$OUT/eq_${tag}.csv" --haircut $h \
      > "$OUT/_funded_${tag}_h${h}.log" 2>&1
  done
  echo "done $tag"
}

# iFVG baseline (fixed r2.5, deployed entry_mode=close from bot_config.json)
run ifvg "" ifvg_base ""
for e in $EXTS; do
  t=$(echo "$e" | tr '.' 'p')
  run ifvg ifvg_fib_target_ext ifvg_$t "$e"
done

# ORB cross-check (baselines r2.5/r1.5 already in B99 journal)
for e in $EXTS; do
  t=$(echo "$e" | tr '.' 'p')
  run orb orb_fib_target_ext orb_$t "$e"
done

echo "ALL B101 RUNS COMPLETE"
