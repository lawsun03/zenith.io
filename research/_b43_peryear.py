"""B43 per-year equity export for proper pipeline comparison (excl 2022)."""
import subprocess
import sys
import threading

PYTHON = sys.executable
BARS_DIR = "bars/yearly"
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
    bars = f"{BARS_DIR}/bars_MNQ_dbv_{year}.csv"
    for wm, tag in [("60", "window60"), ("90", "window90")]:
        name = f"{tag}_{year}"
        out  = f"research/equity_b43/{tag}_r0p75_{year}.csv"
        cmd  = BASE_EQUITY + [
            "--bars", bars,
            "--set", f"orb_signal_window_mins={wm}",
            "--out", out,
        ]
        CMDS.append((name, cmd))

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
print("=== ALL PER-YEAR DONE ===", flush=True)
