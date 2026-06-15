"""B43 parallel benchmark runner — combine w60/w90 + equity export w60/w90."""
import subprocess
import sys
import threading

PYTHON = sys.executable
BARS = "bars/bars_MNQ_dbv_2021_2026.csv"

BASE_COMBINE = [
    PYTHON, "scripts/run_monthly_combine.py",
    "--bars", BARS,
    "--instrument", "MNQ",
    "--timeframe", "5min",
    "--partial-r", "0",
    "--set", "swing_stop_lookback=0",
    "--set", "engine=orb",
    "--set", "orb_r_multiple=2.5",
    "--set", "orb_reentry_after_stop=True",
]

BASE_EQUITY = [
    PYTHON, "scripts/equity_export.py",
    "--bars", BARS,
    "--instrument", "MNQ",
    "--timeframe", "5",
    "--risk-pct", "0.75",
    "--partial-r", "0",
    "--set", "swing_stop_lookback=0",
    "--set", "engine=orb",
    "--set", "orb_r_multiple=2.5",
    "--set", "orb_reentry_after_stop=True",
]

CMDS = [
    ("combine_w60",  BASE_COMBINE + ["--set", "orb_signal_window_mins=60"]),
    ("combine_w90",  BASE_COMBINE + ["--set", "orb_signal_window_mins=90"]),
    ("equity_w60",   BASE_EQUITY  + ["--set", "orb_signal_window_mins=60",
                                     "--out", "research/equity_b43/window60_r0p75_5y.csv"]),
    ("equity_w90",   BASE_EQUITY  + ["--set", "orb_signal_window_mins=90",
                                     "--out", "research/equity_b43/window90_r0p75_5y.csv"]),
]

lock = threading.Lock()


def run(name, cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    with lock:
        print(f"\n=== DONE: {name} (rc={r.returncode}) ===", flush=True)
        if r.returncode == 0:
            print(r.stdout[-4000:], flush=True)
        else:
            print("STDOUT:", r.stdout[-2000:], flush=True)
            print("STDERR:", r.stderr[-2000:], flush=True)


threads = [threading.Thread(target=run, args=(n, c)) for n, c in CMDS]
for t in threads:
    t.start()
for t in threads:
    t.join()
print("\n=== ALL B43 BENCHMARKS DONE ===", flush=True)
