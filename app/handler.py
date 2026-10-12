"""
app/handler.py — framework-agnostic request handling.

The actual triage-request logic (idempotency lookup, compute, store, log, safe
fallback) lives here as plain functions that take a validated `Ticket` and a
`Storage`, and return a `TriageResponse`. Neither FastAPI nor Starlette is
imported here.

Why split this out from main.py:
- It lets the exact production logic be unit-tested with zero web dependencies
  (important in sandboxes without FastAPI/httpx).
- main.py becomes a tiny adapter: parse/validate -> call handle_triage -> return.
  Both the FastAPI app and the test harness call the SAME function, so the thing
  we test is the thing that runs.
"""

from __future__ import annotations

import time
import uuid

from app.logging_config import log_event
from app.schemas import Ticket, TriageResponse
from app.storage import Storage
from app.triage import safe_fallback_response, triage_ticket


def handle_triage(ticket: Ticket, storage: Storage) -> TriageResponse:
    """
    Idempotent triage of one already-validated ticket.

    Input validation (missing fields -> 4xx) happens at the framework boundary
    before this is called; by the time we're here, `ticket` is a valid Ticket.
    """
    request_id = str(uuid.uuid4())
    start = time.perf_counter()

    try:
        existing = storage.get(ticket.id)
        if existing is not None:
            log_event(
                request_id=request_id,
                event="triage_idempotent_hit",
                ticket_id=ticket.id,
                category=existing.category.value,
                refund_rec=existing.refund.recommendation.value,
                needs_human=existing.needs_human,
                mode=existing.mode.value,
                outcome="returned_stored",
                latency_ms=round((time.perf_counter() - start) * 1000, 2),
            )
            return existing

        response = triage_ticket(ticket)
        stored, was_new = storage.save_if_absent(response)

        log_event(
            request_id=request_id,
            event="triage_processed",
            ticket_id=ticket.id,
            category=stored.category.value,
            severity=stored.severity.value,
            refund_rec=stored.refund.recommendation.value,
            needs_human=stored.needs_human,
            confidence=stored.confidence,
            mode=stored.mode.value,
            injection=stored.injection_detected,
            outcome="new" if was_new else "raced_to_stored",
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
        )
        return stored

    except Exception as e:
        log_event(
            request_id=request_id,
            event="triage_error",
            ticket_id=ticket.id,
            outcome="safe_fallback",
            error_class=type(e).__name__,
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
        )
        return safe_fallback_response(ticket.id, reason=type(e).__name__)
