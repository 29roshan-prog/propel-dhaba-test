"""
app/logging_config.py — structured, privacy-safe logging. "Logs are clean."

The assignment: "nothing in these tickets that identifies a person or a company
should end up in a log line."

Strategy — safe by construction, not by filtering:
- We expose ONE helper, `log_event`, that takes a fixed, allowlisted set of
  fields: request_id, ticket_id, category, severity, refund_rec, needs_human,
  confidence, mode, injection, latency_ms, outcome, error_class.
- There is NO parameter for the ticket body, subject, purchases, reply text, or
  any free text. You physically cannot pass the GSTIN or the body through this
  function, because there's nowhere to put it.
- ticket_id is logged because it is an opaque internal reference (T-1010), not a
  person/company identifier. If even that were disallowed, flip LOG_TICKET_ID to
  False and it is replaced with a salted hash.

Output is a single JSON line per event (easy to grep/aggregate at 3am).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
from typing import Optional

# If your assignment considers the ticket id itself too identifying, set this to
# False: ids are then logged as a short salted hash so you can still correlate
# retries without storing the raw id.
LOG_TICKET_ID = True
_ID_SALT = "dhaba-triage-local-salt"  # not a secret; only to de-identify ids

_logger = logging.getLogger("triage")
if not _logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(message)s"))  # we emit raw JSON
    _logger.addHandler(_handler)
    _logger.setLevel(logging.INFO)


def _safe_ticket_id(ticket_id: str) -> str:
    if LOG_TICKET_ID:
        return ticket_id
    digest = hashlib.sha256((_ID_SALT + ticket_id).encode()).hexdigest()
    return f"tkt_{digest[:12]}"


def log_event(
    *,
    request_id: str,
    event: str,
    ticket_id: Optional[str] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    refund_rec: Optional[str] = None,
    needs_human: Optional[bool] = None,
    confidence: Optional[float] = None,
    mode: Optional[str] = None,
    injection: Optional[bool] = None,
    latency_ms: Optional[float] = None,
    outcome: Optional[str] = None,
    error_class: Optional[str] = None,
) -> None:
    """
    Emit one structured JSON log line from an allowlisted field set.

    Note what is ABSENT from the signature: body, subject, purchases, reply,
    email, phone, gstin, amounts tied to a person. There is deliberately no way
    to log them through this function.
    """
    record = {
        "ts": round(time.time(), 3),
        "event": event,
        "request_id": request_id,
    }
    if ticket_id is not None:
        record["ticket_id"] = _safe_ticket_id(ticket_id)
    for k, v in (
        ("category", category),
        ("severity", severity),
        ("refund_rec", refund_rec),
        ("needs_human", needs_human),
        ("confidence", confidence),
        ("mode", mode),
        ("injection", injection),
        ("latency_ms", latency_ms),
        ("outcome", outcome),
        ("error_class", error_class),
    ):
        if v is not None:
            record[k] = v
    _logger.info(json.dumps(record, ensure_ascii=False))
