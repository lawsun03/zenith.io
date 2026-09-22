"""Instrument specs for the research corpus.

Root (full-size) symbols only — NQ, ES, GC. Micro sizing (MNQ/MES/MGC) is a
sizing-layer concern (CLAUDE.md rule 4) and has no place here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

Root = Literal["NQ", "ES", "GC"]

ROOTS: tuple[Root, ...] = ("NQ", "ES", "GC")


@dataclass(frozen=True)
class InstrumentSpec:
    root: Root
    dataset: str  # Databento dataset code
    tick_size: Decimal
    # CME month codes this root lists, in calendar order (F=Jan .. Z=Dec).
    contract_months: tuple[str, ...]


# GC lists the standard bi-monthly COMEX cycle: Feb, Apr, Jun, Aug, Oct, Dec.
# NQ/ES list the quarterly financial cycle: Mar, Jun, Sep, Dec.
INSTRUMENTS: dict[Root, InstrumentSpec] = {
    "NQ": InstrumentSpec("NQ", "GLBX.MDP3", Decimal("0.25"), ("H", "M", "U", "Z")),
    "ES": InstrumentSpec("ES", "GLBX.MDP3", Decimal("0.25"), ("H", "M", "U", "Z")),
    "GC": InstrumentSpec("GC", "GLBX.MDP3", Decimal("0.10"), ("G", "J", "M", "Q", "V", "Z")),
}

MONTH_CODE_TO_NUM: dict[str, int] = {
    "F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
    "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12,
}
