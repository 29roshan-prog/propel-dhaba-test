"""
Privacy + end-to-end triage tests (brief required tests #10, #13, and the full
replay sanity #12 is covered by the replay script test).

#10: ticket body and GSTIN are not logged.
#13: low-confidence / ambiguous cases require human review.
"""

from __future__ import annotations

import inspect
import io
import logging

import pytest

from app import logging_config
from app.logging_config import log_event
from app.schemas import Severity
from app.triage import triage_ticket, CONFIDENCE_HUMAN_THRESHOLD
from app.schemas import Ticket
from tests.conftest_data import DATA_AVAILABLE, tickets_by_id


def test_log_event_cannot_accept_body_fields():
    """#10 (structural): there is no parameter to pass body/subject/gstin."""
    params = set(inspect.signature(log_event).parameters)
    for forbidden in ("body", "subject", "gstin", "email", "phone", "reply", "purchases"):
        assert forbidden not in params


def test_log_line_has_no_pii():
    """#10 (behavioural): a logged event for a GSTIN ticket leaks nothing."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s"))
    original = logging_config._logger.handlers
    logging_config._logger.handlers = [handler]
    try:
        log_event(
            request_id="r1",
            event="triage_processed",
            ticket_id="T-1010",
            category="invoice_request",
            severity="low",
            refund_rec="none",
            needs_human=False,
            mode="fixture",
        )
    finally:
        logging_config._logger.handlers = original
    out = buf.getvalue().lower()
    # None of these free-text markers may appear in a log line. The log helper
    # takes no body/subject parameter, so a logged event can only contain the
    # allowlisted structured fields — never a company name, a GST number, or any
    # body text. (Generic tokens here; we don't repeat real ticket content.)
    for leak in ("gstin", "invoice for", "company", "subject", "body"):
        assert leak not in out


@pytest.mark.skipif(not DATA_AVAILABLE, reason="needs local data file")
def test_injection_tickets_low_confidence_force_human():
    """#13: ambiguous/low-confidence (injection) tickets require human review."""
    for tid in ("T-1003", "T-1011"):
        r = triage_ticket(tickets_by_id()[tid])
        assert r.needs_human is True


def test_unknown_category_forces_human():
    """A ticket with no recognisable signal -> UNKNOWN -> human review."""
    t = Ticket(
        id="T-GIBBERISH",
        received_at="t",
        subject="asdfghjkl",
        body="qwerty zxcvbnm",
        purchases=[],
        app_opens_since_renewal=0,
    )
    r = triage_ticket(t)
    assert r.category.value == "unknown"
    assert r.needs_human is True
    assert r.confidence < CONFIDENCE_HUMAN_THRESHOLD


@pytest.mark.skipif(not DATA_AVAILABLE, reason="needs local data file")
def test_no_reply_claims_processed_refund():
    """No ticket's drafted reply ever claims a refund was completed."""
    for tid, t in tickets_by_id().items():
        r = triage_ticket(t)
        low = r.reply_draft.lower()
        assert "refund has been processed" not in low
        assert "refund has been completed" not in low
        assert "we have refunded" not in low
