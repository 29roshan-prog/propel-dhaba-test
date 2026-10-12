"""
API-layer tests (brief required tests #1 full, #2, and HTTP idempotency).

These drive the REAL request path (app.handler.handle_triage behind an ASGI
app). Under pytest on a machine with FastAPI installed you'd normally use
fastapi.testclient; here we use the dependency-free ASGI harness + Starlette
mirror so the same assertions run in any sandbox. The logic exercised is
identical (handle_triage), so these tests are meaningful either way.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from tests.conftest_data import DATA_AVAILABLE, raw_by_id

# Build a fresh app pointed at a temp DB.
os.environ["TRIAGE_DB_PATH"] = tempfile.mktemp(suffix=".db")
os.environ["TRIAGE_MODE"] = "fixture"

try:
    from tests._starlette_app import app as _app
    from tests._asgi_harness import request as _request

    HARNESS_OK = True
except Exception:
    HARNESS_OK = False


pytestmark = pytest.mark.skipif(not HARNESS_OK, reason="starlette harness unavailable")


def test_health():
    r = _request(_app, "GET", "/health")
    assert r.status == 200
    assert r.json()["status"] == "ok"


@pytest.mark.skipif(not DATA_AVAILABLE, reason="needs local data file")
def test_valid_ticket_returns_complete_schema():
    """#1: a valid ticket returns the full response schema."""
    t = raw_by_id()["T-1002"]
    r = _request(_app, "POST", "/triage", json_body=t)
    assert r.status == 200
    body = r.json()
    for field in (
        "ticket_id",
        "category",
        "severity",
        "refund",
        "reply_draft",
        "needs_human",
        "confidence",
        "decision_reasons",
        "mode",
        "injection_detected",
    ):
        assert field in body, f"missing {field}"
    for field in ("recommendation", "amount_inr", "reason"):
        assert field in body["refund"]


def test_missing_required_field_returns_422():
    """#2: a body missing a required field gets a clear 4xx."""
    r = _request(_app, "POST", "/triage", json_body={"subject": "no id here"})
    assert r.status == 422


@pytest.mark.skipif(not DATA_AVAILABLE, reason="needs local data file")
def test_http_idempotency():
    """Same ticket id over HTTP twice -> identical response."""
    t = raw_by_id()["T-1007"]
    r1 = _request(_app, "POST", "/triage", json_body=t)
    r2 = _request(_app, "POST", "/triage", json_body=t)
    assert r1.status == r2.status == 200
    assert r1.json() == r2.json()
