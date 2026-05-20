from __future__ import annotations

import csv
import json
import re
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent  # project root


def load_all_trades() -> list[dict]:
    """Merge all trades*.csv files from project root."""
    rows: list[dict] = []
    for path in sorted(_ROOT.glob("trades*.csv")):
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append({
                    "ts": row.get("ts", ""),
                    "instrument": row.get("instrument", ""),
                    "side": row.get("side", ""),
                    "type": row.get("type", ""),
                    "fill_price": _float(row.get("fill_price")),
                    "size": _int(row.get("size")),
                    "realized_pnl": _float(row.get("realized_pnl")),
                    "broker_order_id": row.get("broker_order_id", ""),
                    "stop": _float(row.get("stop")),
                    "target": _float(row.get("target")),
                    "killzone": row.get("killzone") or None,
                    "rationale": row.get("rationale") or None,
                })
    return rows


def load_backtest_summaries() -> list[dict]:
    """Parse all summary_run*.txt from backtest_results/."""
    results = []
    for path in sorted((_ROOT / "backtest_results").glob("summary_run*.txt")):
        parsed = _parse_summary_file(path)
        if parsed:
            results.append(parsed)
    return results


def load_log_events(days: int = 2) -> list[dict]:
    """Read last N daily log files. Files are UTF-16 (Windows PowerShell redirect)."""
    events: list[dict] = []
    logs_dir = _ROOT / "logs"
    if not logs_dir.exists():
        return events
    for path in sorted(logs_dir.glob("*.log"))[-days:]:
        try:
            text = path.read_text(encoding="utf-16", errors="replace")
        except Exception:
            continue
        for line in text.splitlines():
            parsed = _parse_log_line(line.strip())
            if parsed:
                events.append(parsed)
    return events


def load_config() -> dict:
    p = _ROOT / "bot_config.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _float(v: str | None) -> float | None:
    if not v:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _int(v: str | None) -> int | None:
    if not v:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _parse_summary_file(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Header: "BACKTEST SUMMARY: key=val  key=val ..."
    params: dict[str, str] = {}
    for line in lines[:3]:
        for m in re.finditer(r'(\w+)=([\d.]+)', line):
            params[m.group(1)] = m.group(2)

    def extract(label: str) -> str | None:
        for line in lines:
            if label in line and ":" in line:
                return line.split(":")[-1].strip()
        return None

    def extract_float(label: str) -> float | None:
        v = extract(label)
        if not v:
            return None
        v = re.sub(r'[+$,]', '', v).split()[0]
        try:
            return float(v)
        except ValueError:
            return None

    total_trades: int | None = None
    win_rate: float | None = None
    for line in lines:
        if "Total trades:" in line:
            m = re.search(r'(\d+)', line.split(":")[-1])
            if m:
                total_trades = int(m.group(1))
        if "Winners:" in line:
            m = re.search(r'\(([\d.]+)%\)', line)
            if m:
                win_rate = float(m.group(1)) / 100

    # Killzone section
    killzones: dict[str, dict] = {}
    in_kz = False
    for line in lines:
        if "By killzone:" in line:
            in_kz = True
            continue
        if in_kz:
            if "===" in line:
                break
            m = re.match(
                r'\s*([\w/ ]+?)\s+trades=\s*(\d+)\s+win_rate=\s*([\d.]+)%\s+net=\s*([+\-$\d,. ]+)',
                line,
            )
            if m:
                kz = m.group(1).strip().lower().replace(" ", "_").replace("/", "_")
                net = re.sub(r'[+$,\s]', '', m.group(4))
                killzones[kz] = {
                    "trades": int(m.group(2)),
                    "win_rate": float(m.group(3)) / 100,
                    "net_pnl": float(net) if net else 0.0,
                }

    net_pnl = extract_float("Net P&L")
    if net_pnl is None:
        return None

    return {
        "params": params,
        "verdict": extract("Verdict"),
        "net_pnl": net_pnl,
        "gross_profit": extract_float("Gross profit"),
        "gross_loss": extract_float("Gross loss"),
        "total_trades": total_trades,
        "win_rate": win_rate,
        "profit_factor": extract_float("Profit factor"),
        "expectancy": extract_float("Expectancy"),
        "max_drawdown": extract_float("Max drawdown"),
        "killzones": killzones,
    }


def _parse_log_line(line: str) -> dict | None:
    # Format: "HH:MM:SS LEVEL   logger | message"
    m = re.match(
        r'(\d{2}:\d{2}:\d{2})\s+(INFO|WARNING|ERROR|DEBUG|CRITICAL)\s+(\S+)\s+\|\s+(.*)',
        line,
    )
    if not m:
        return None
    return {
        "time": m.group(1),
        "level": m.group(2),
        "logger": m.group(3),
        "message": m.group(4).strip(),
    }
