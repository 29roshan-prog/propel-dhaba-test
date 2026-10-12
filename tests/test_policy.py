"""
Policy (money gate) tests — the most important tests in the suite.

Covers brief required tests #7 (T-1005 initiated), #8 (T-1009 failed+successful),
#9 (T-1008 fraud escalated), plus the core invariant: no automatic payout, and
refunds never exceed a single verified successful charge.
"""

from __future__ import annotations

import pytest

from app.policy import decide_refund, max_successful_charge, successful_purchases
from app.schemas import Category, RefundRecommendation, Ticket
from tests.conftest_data import DATA_AVAILABLE, tickets_by_id

# The category each ticket SHOULD be, so we test the money gate independently of
# the classifier.
EXPECTED_CATEGORY = {
    "T-1001": Category.BILLING_DISPUTE,
    "T-1002": Category.TECHNICAL_ISSUE,
    "T-1003": Category.ACCOUNT_DATA_ISSUE,
    "T-1004": Category.CANCELLATION,
    "T-1005": Category.PAYMENT_ACTIVATION,
    "T-1006": Category.BILLING_DISPUTE,
    "T-1007": Category.FEATURE_REQUEST,
    "T-1008": Category.SUSPECTED_FRAUD,
    "T-1009": Category.DUPLICATE_CHARGE,
    "T-1010": Category.INVOICE_REQUEST,
    "T-1011": Category.PLAN_QUESTION,
    "T-1012": Category.GENERAL_COMPLAINT,
}


@pytest.mark.skipif(not DATA_AVAILABLE, reason="needs local data file")
def test_no_automatic_payout_on_any_real_ticket():
    """#core: not one real ticket produces an auto-approved refund with money."""
    tickets = tickets_by_id()
    for tid, ticket in tickets.items():
        refund, _human, _reasons = decide_refund(ticket, EXPECTED_CATEGORY[tid])
        assert refund.recommendation != RefundRecommendation.RECOMMENDED, (
            f"{tid} unexpectedly recommended an auto-refund"
        )
        assert refund.amount_inr == 0, f"{tid} attached a nonzero refund amount"


@pytest.mark.skipif(not DATA_AVAILABLE, reason="needs local data file")
def test_t1005_initiated_not_treated_as_paid():
    """#7: the DISPUTED 249 renewal is 'initiated', not a confirmed charge.

    Note: T-1005 does have a successful 1-rupee *trial*; what it lacks is a
    successful *renewal*. The user's complaint ("249 kat gaya") is about the
    249, which is only 'initiated'. So there is no successful 249 to refund, and
    the max successful charge is the 1-rupee trial, not 249.
    """
    from app.policy import max_successful_charge
    from app.schemas import PurchaseStatus

    t = tickets_by_id()["T-1005"]
    # The renewal the user is disputing is NOT successful:
    renewals = [p for p in t.purchases if p.type.value == "renewal"]
    assert all(p.status != PurchaseStatus.SUCCESSFUL for p in renewals)
    # The only successful charge is the 1-rupee trial.
    assert max_successful_charge(t) == 1

    refund, human, reasons = decide_refund(t, Category.PAYMENT_ACTIVATION)
    assert refund.recommendation == RefundRecommendation.NEEDS_HUMAN
    assert refund.amount_inr == 0
    assert human is True


@pytest.mark.skipif(not DATA_AVAILABLE, reason="needs local data file")
def test_t1009_failed_plus_successful_is_not_double_charge():
    """#8: one failed + one successful = a single charge, not a duplicate."""
    t = tickets_by_id()["T-1009"]
    assert len(successful_purchases(t)) == 1  # only one went through
    refund, human, reasons = decide_refund(t, Category.DUPLICATE_CHARGE)
    assert refund.recommendation != RefundRecommendation.RECOMMENDED
    assert refund.amount_inr == 0
    assert human is True


@pytest.mark.skipif(not DATA_AVAILABLE, reason="needs local data file")
def test_t1008_fraud_escalated():
    """#9: suspected fraud always escalates; no auto-refund, no auto-accusation."""
    t = tickets_by_id()["T-1008"]
    refund, human, reasons = decide_refund(t, Category.SUSPECTED_FRAUD)
    assert human is True
    assert refund.recommendation == RefundRecommendation.NEEDS_HUMAN
    assert refund.amount_inr == 0


def test_genuine_duplicate_refunds_exactly_one_capped():
    """Positive case: two successful identical charges -> recommend ONE, capped."""
    t = Ticket(
        id="T-DUP",
        received_at="2026-09-06T08:19:00+05:30",
        subject="double charge",
        body="charged twice",
        purchases=[
            {"id": "p1", "type": "renewal", "amount_inr": 249, "status": "successful", "at": "t"},
            {"id": "p2", "type": "renewal", "amount_inr": 249, "status": "successful", "at": "t"},
        ],
        app_opens_since_renewal=0,
    )
    refund, human, _ = decide_refund(t, Category.DUPLICATE_CHARGE)
    assert refund.recommendation == RefundRecommendation.RECOMMENDED
    assert refund.amount_inr == 249  # exactly one charge, not 498
    assert human is True  # still needs human approval to actually pay out


def test_refund_never_exceeds_single_charge():
    """Even with three successful charges, a refund is capped at one."""
    t = Ticket(
        id="T-TRIPLE",
        received_at="t",
        subject="s",
        body="b",
        purchases=[
            {"id": f"p{i}", "type": "renewal", "amount_inr": 249, "status": "successful", "at": "t"}
            for i in range(3)
        ],
        app_opens_since_renewal=0,
    )
    assert max_successful_charge(t) == 249
    refund, _, _ = decide_refund(t, Category.DUPLICATE_CHARGE)
    assert refund.amount_inr == 249


def test_cancellation_does_not_infer_refund():
    """A 'cancel' request never authorises returning past money."""
    t = Ticket(
        id="T-CANCEL",
        received_at="t",
        subject="cancel",
        body="cancel",
        purchases=[
            {"id": "p1", "type": "trial", "amount_inr": 1, "status": "successful", "at": "t"}
        ],
        app_opens_since_renewal=1,
    )
    refund, human, _ = decide_refund(t, Category.CANCELLATION)
    assert refund.recommendation == RefundRecommendation.NEEDS_HUMAN
    assert refund.amount_inr == 0
    assert human is True
