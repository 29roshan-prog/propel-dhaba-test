"""
tests/conftest_data.py — shared ticket loading for tests.

Loads the REAL dhaba_tickets.json from data/ (gitignored). Tests assert against
real ticket content, which is why the data file must be present locally to run
the full suite. A couple of tests that don't need real data build synthetic
tickets inline.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.schemas import Ticket

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "dhaba_tickets.json"


def load_raw_tickets() -> dict:
    with open(_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def tickets_by_id() -> dict[str, Ticket]:
    raw = load_raw_tickets()
    return {t["id"]: Ticket(**t) for t in raw["tickets"]}


def raw_by_id() -> dict[str, dict]:
    raw = load_raw_tickets()
    return {t["id"]: t for t in raw["tickets"]}


DATA_AVAILABLE = _DATA_PATH.exists()
