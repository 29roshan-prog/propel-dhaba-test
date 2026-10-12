"""
Fallback / resilience tests (brief required tests #3, #11, #14).

#3:  malformed model JSON / invented category triggers fallback, never an
     unsafe refund.
#11: fixture mode works with API keys unset.
#14: a provider timeout/error returns safe output rather than crashing.
"""

from __future__ import annotations

import os

import pytest

from app.llm import LLMError, _parse_response
from app.schemas import RefundRecommendation, TriageMode
from app.triage import triage_ticket, safe_fallback_response
from app.schemas import Ticket


def _sample_ticket() -> Ticket:
    return Ticket(
        id="T-FALLBACK",
        received_at="t",
        subject="charged 249 without telling me",
        body="i only paid 1 rupee, now 249 gone. refund it.",
        purchases=[
            {"id": "p1", "type": "trial", "amount_inr": 1, "status": "successful", "at": "t"},
            {"id": "p2", "type": "renewal", "amount_inr": 249, "status": "successful", "at": "t"},
        ],
        app_opens_since_renewal=0,
    )


def test_parse_rejects_prose():
    with pytest.raises(LLMError):
        _parse_response("Sure! Here is my classification in words, not JSON.")


def test_parse_rejects_truncated_json():
    with pytest.raises(LLMError):
        _parse_response('{"category": "billing_dispute", "severity": "med')


def test_parse_rejects_invented_category():
    with pytest.raises(LLMError):
        _parse_response(
            '{"category": "make_me_rich", "severity": "low", '
            '"confidence": 0.9, "reply_draft": "x"}'
        )


def test_parse_accepts_valid_json_with_extra_prose():
    """Leading/trailing prose around a valid object is tolerated."""
    result = _parse_response(
        'Here you go:\n{"category": "billing_dispute", "severity": "medium", '
        '"confidence": 0.8, "reply_draft": "We will look into it."} Thanks!'
    )
    assert result.category.value == "billing_dispute"


def test_fixture_mode_works_without_api_key(monkeypatch):
    """#11: no key set, fixture mode still returns a valid response."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("TRIAGE_MODE", "fixture")
    r = triage_ticket(_sample_ticket())
    assert r.mode == TriageMode.FIXTURE
    assert r.reply_draft.strip() != ""


def test_llm_mode_without_key_degrades_to_fallback(monkeypatch):
    """#14-ish: llm mode with no key must not crash; it degrades to fixture."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("TRIAGE_MODE", "llm")
    r = triage_ticket(_sample_ticket(), mode=TriageMode.LLM)
    # The LLM call raises (no key) -> orchestrator degrades.
    assert r.mode == TriageMode.FALLBACK
    assert r.reply_draft.strip() != ""
    # And still no unsafe refund.
    assert r.refund.recommendation != RefundRecommendation.RECOMMENDED


def test_llm_timeout_degrades_safely(monkeypatch):
    """#14: a provider that times out yields a safe fallback, not a crash."""
    import app.triage as triage_mod

    def _boom(*a, **k):
        raise LLMError("simulated timeout")

    # Patch the llm call used inside the orchestrator.
    monkeypatch.setattr("app.llm.classify_with_llm", _boom, raising=True)
    r = triage_ticket(_sample_ticket(), mode=TriageMode.LLM)
    assert r.mode == TriageMode.FALLBACK
    assert r.refund.recommendation != RefundRecommendation.RECOMMENDED


def test_safe_fallback_response_is_conservative():
    r = safe_fallback_response("T-X", reason="unit")
    assert r.needs_human is True
    assert r.refund.amount_inr == 0
    assert r.category.value == "unknown"
