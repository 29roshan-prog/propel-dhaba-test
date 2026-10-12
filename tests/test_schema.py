"""Schema/validation tests (brief required tests #1, #2, #3 partial)."""

from __future__ import annotations

import pytest

from app.schemas import (
    Category,
    ModelClassification,
    Refund,
    RefundRecommendation,
    Ticket,
    TriageResponse,
)
from tests.conftest_data import DATA_AVAILABLE, tickets_by_id


@pytest.mark.skipif(not DATA_AVAILABLE, reason="needs local data/dhaba_tickets.json")
def test_all_real_tickets_parse():
    """#1 (input side): every real ticket validates against the Ticket model."""
    t = tickets_by_id()
    assert len(t) == 12
    assert set(t) == {f"T-{1000+i}" for i in range(1, 13)}


def test_ticket_rejects_extra_field():
    """Schema drift (an unexpected field) is rejected, not silently accepted."""
    with pytest.raises(Exception):
        Ticket(
            id="T-X",
            received_at="2026-01-01T00:00:00+05:30",
            subject="s",
            body="b",
            purchases=[],
            app_opens_since_renewal=0,
            unexpected="leak",
        )


def test_ticket_rejects_blank_id():
    with pytest.raises(Exception):
        Ticket(
            id="   ",
            received_at="t",
            subject="s",
            body="b",
            purchases=[],
            app_opens_since_renewal=0,
        )


def test_invented_category_rejected():
    """#3 (core): a category outside the enum cannot be constructed."""
    with pytest.raises(Exception):
        ModelClassification(
            category="totally_made_up",
            severity="low",
            confidence=0.9,
            reply_draft="hi",
        )


def test_confidence_bounds_enforced():
    with pytest.raises(Exception):
        ModelClassification(
            category="unknown", severity="low", confidence=1.5, reply_draft="x"
        )


def test_refund_amount_requires_recommended():
    """A nonzero refund amount with a non-RECOMMENDED status is refused."""
    with pytest.raises(Exception):
        Refund(
            recommendation=RefundRecommendation.NONE,
            amount_inr=249,
            reason="should not be allowed",
        )
    # The valid shape is fine:
    ok = Refund(
        recommendation=RefundRecommendation.RECOMMENDED,
        amount_inr=249,
        reason="ok",
    )
    assert ok.amount_inr == 249


def test_triage_response_requires_all_fields():
    """A response missing a required field cannot be constructed."""
    with pytest.raises(Exception):
        TriageResponse(ticket_id="T-X")  # missing everything else
