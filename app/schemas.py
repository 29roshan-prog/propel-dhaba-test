"""
app/schemas.py — the contract.

Everything that crosses the API boundary is defined here as a strict Pydantic
model or enum. This is the "schemas that hold" requirement from the assignment:
the model will eventually return prose, truncated JSON, or an invented category,
and the only thing standing between that and a bad decision is strict validation
against these types.

Why one file for all schemas:
- There is exactly one source of truth for the shape of a request and a
  response. A reviewer (or you, in pairing) can read the whole contract in one
  place.
- The enums are closed sets. If the LLM returns a category that is not in
  `Category`, Pydantic raises — it cannot be coerced into a confident decision.

Nothing in this file calls an LLM, touches the network, or decides a refund.
It only describes shapes.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Input enums — these mirror the REAL dhaba_tickets.json values.
# (Verified by inspecting the file, not guessed.)
# --------------------------------------------------------------------------- #
class PurchaseType(str, Enum):
    """`purchases[].type` in the data. Only two values appear."""

    TRIAL = "trial"
    RENEWAL = "renewal"


class PurchaseStatus(str, Enum):
    """
    `purchases[].status` in the data.

    IMPORTANT: only `SUCCESSFUL` is evidence that money actually moved.
    `INITIATED` means a charge was started but not confirmed (T-1005).
    `FAILED` means it did not go through (T-1009's first line).
    The PDF prose also names "renewal" as a state, but in the real JSON
    "renewal" is a *type*, not a *status*. We model the data as it is.
    """

    INITIATED = "initiated"
    FAILED = "failed"
    SUCCESSFUL = "successful"


# --------------------------------------------------------------------------- #
# Output enums — these are OURS. We define the small closed set the LLM must
# pick from. "Do not invent categories at inference time" (assignment).
# --------------------------------------------------------------------------- #
class Category(str, Enum):
    """
    The closed category set, derived from the actual 12 tickets.

    If you add a value here you must also: update the fixture classifier,
    the LLM prompt's allowed list, any policy branch that cares, and the
    README table. That blast radius is intentional — categories are a
    contract, not a convenience.
    """

    BILLING_DISPUTE = "billing_dispute"          # T-1001, T-1006
    PAYMENT_ACTIVATION = "payment_activation"     # T-1005
    DUPLICATE_CHARGE = "duplicate_charge"         # T-1009
    SUSPECTED_FRAUD = "suspected_fraud"           # T-1008
    CANCELLATION = "cancellation"                 # T-1004
    TECHNICAL_ISSUE = "technical_issue"           # T-1002
    ACCOUNT_DATA_ISSUE = "account_data_issue"     # T-1003 (genuine part)
    INVOICE_REQUEST = "invoice_request"           # T-1010
    FEATURE_REQUEST = "feature_request"           # T-1007
    PLAN_QUESTION = "plan_question"               # T-1011 (genuine part)
    GENERAL_COMPLAINT = "general_complaint"       # T-1012
    UNKNOWN = "unknown"                           # safe fallback bucket


class Severity(str, Enum):
    """
    Severity = how fast a human should care. Documented meaning:

    - LOW:    no money at risk, no user harm. Feature/invoice/info.
    - MEDIUM: money disputed or user blocked, but no safety/legal signal.
    - HIGH:   possible fraud, legal/regulatory threat, money-moved-but-
              unconfirmed, or a prompt-injection attempt. A human should look soon.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TriageMode(str, Enum):
    """Which classifier produced this result. Keeps fixture honest-ly labelled."""

    FIXTURE = "fixture"   # deterministic rules, no network, no key
    LLM = "llm"           # a real provider answered
    FALLBACK = "fallback" # LLM failed/invalid -> safe degraded result


class RefundRecommendation(str, Enum):
    """
    What the deterministic policy decided about money.

    Note the vocabulary: these are *recommendations*, never executed refunds.
    A triage service recommends; it does not move money.
    """

    NONE = "none"                  # no refund warranted by the evidence
    RECOMMENDED = "recommended"    # evidence supports a refund of `amount_inr`
    NEEDS_HUMAN = "needs_human"    # cannot decide safely -> escalate


# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #
class Purchase(BaseModel):
    """One billing record as billing knew it when the ticket landed."""

    id: str
    type: PurchaseType
    amount_inr: int = Field(ge=0)
    status: PurchaseStatus
    at: str  # kept as string; we don't do date math in the money gate

    model_config = {"extra": "forbid"}  # reject unexpected fields loudly


class Ticket(BaseModel):
    """
    A ticket exactly as it appears in dhaba_tickets.json.

    `extra="forbid"` means if the input shape drifts (a renamed field), we fail
    fast with a 422 instead of silently triaging a half-understood ticket.
    """

    id: str = Field(min_length=1)
    received_at: str
    subject: str
    body: str
    purchases: List[Purchase] = Field(default_factory=list)
    app_opens_since_renewal: int = Field(default=0, ge=0)

    model_config = {"extra": "forbid"}

    @field_validator("id")
    @classmethod
    def id_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("ticket id must not be blank")
        return v


# --------------------------------------------------------------------------- #
# Output models
# --------------------------------------------------------------------------- #
class Refund(BaseModel):
    """
    The money part of the response. Built ONLY by app/policy.py, never by the
    model. `amount_inr` is 0 unless `recommendation == RECOMMENDED`.
    """

    recommendation: RefundRecommendation
    amount_inr: int = Field(default=0, ge=0)
    reason: str

    @field_validator("amount_inr")
    @classmethod
    def amount_only_when_recommended(cls, v: int, info) -> int:
        # Defence in depth: a non-zero amount with a non-RECOMMENDED status is a
        # bug. We refuse to serialize it rather than emit a misleading response.
        rec = info.data.get("recommendation")
        if rec is not None and rec != RefundRecommendation.RECOMMENDED and v != 0:
            raise ValueError("amount_inr must be 0 unless refund is RECOMMENDED")
        return v


class TriageResponse(BaseModel):
    """
    The full triage output a support tool could act on. Required by the brief:
    ticket_id, category, severity, refund{}, reply_draft, needs_human,
    confidence, decision_reasons, and a mode indicator (fixture/llm/fallback).
    """

    ticket_id: str
    category: Category
    severity: Severity
    refund: Refund
    reply_draft: str
    needs_human: bool
    confidence: float = Field(ge=0.0, le=1.0)
    decision_reasons: List[str]
    mode: TriageMode
    injection_detected: bool = False

    model_config = {"extra": "forbid"}


# --------------------------------------------------------------------------- #
# The narrow object the LLM is allowed to return. Deliberately does NOT contain
# any refund field: the model classifies and drafts; it never touches money.
# --------------------------------------------------------------------------- #
class ModelClassification(BaseModel):
    """
    What we parse out of a model response. If the model returns anything that
    does not fit this (prose, truncated JSON, invented category), construction
    raises and triage.py falls back safely.
    """

    category: Category
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    reply_draft: str

    model_config = {"extra": "ignore"}  # tolerate extra keys the model adds
