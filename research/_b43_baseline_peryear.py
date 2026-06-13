"""Generate per-year no-cutoff baseline equity with same parity flags as B43."""
import subprocess
import sys
import threading

PYTHON = sys.executable
YEARS = ["2021", "2023", "2024", "2025", "2026"]

BASE_EQUITY = [
    PYTHON, "scripts/equity_export.py",
    "--instrument", "MNQ", "--timeframe", "5",
    "--risk-pct", "0.75", "--partial-r", "0",
    "--set", "swing_stop_lookback=0", "--set", "engine=orb",
    "--set", "orb_r_multiple=2.5", "--set", "orb_reentry_after_stop=True",
]

CMDS = []
for year in YEARS:
    bars = f"bars/yearly/bars_MNQ_dbv_{year}.csv"
    out  = f"research/equity_b43/window00_r0p75_{year}.csv"
    cmd  = BASE_EQUITY + ["--bars", bars, "--out", out]
    CMDS.append((f"w00_{year}", cmd))

lock = threading.Lock()


def run(name, cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    with lock:
        print(f"DONE: {name} (rc={r.returncode})", flush=True)
        if r.returncode != 0:
            print(r.stderr[-500:], flush=True)


threads = [threading.Thread(target=run, args=(n, c)) for n, c in CMDS]
for t in threads:
    t.start()
for t in threads:
    t.join()
print("=== BASELINE PER-YEAR DONE ===", flush=True)
