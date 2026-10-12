"""
app/triage.py — orchestration. The one place the whole flow is visible.

Flow for a single ticket:
  1. Detect injection (flag + force human review later).
  2. Classify:
       - fixture mode: deterministic rules (app/fixtures.py)
       - llm mode: call provider (app/llm.py); on ANY failure, fall back to the
         fixture classifier and mark mode=FALLBACK. We never crash and never
         emit an unvalidated model result.
  3. Gate money: app/policy.decide_refund() using billing facts only.
  4. Assemble the TriageResponse, applying safety overrides:
       - injection  -> needs_human=True, severity>=HIGH, append safe note
       - low confidence -> needs_human=True
       - UNKNOWN category -> needs_human=True

This function is deterministic in fixture mode: same ticket -> same response,
which is what makes the replay table reproducible and the tests stable.
"""

from __future__ import annotations

import os
from typing import List

from app.fixtures import classify as fixture_classify
from app.policy import decide_refund
from app.schemas import (
    Category,
    ModelClassification,
    Refund,
    RefundRecommendation,
    Severity,
    Ticket,
    TriageMode,
    TriageResponse,
)
from app.security import INJECTION_SAFE_NOTE, detect_injection

# Below this confidence we always ask a human to look, regardless of category.
# Interviewers will tweak this number; see PAIRING_GUIDE.md for the effect.
CONFIDENCE_HUMAN_THRESHOLD = 0.55

_SEVERITY_ORDER = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2}


def _at_least_high(sev: Severity) -> Severity:
    return sev if _SEVERITY_ORDER[sev] >= _SEVERITY_ORDER[Severity.HIGH] else Severity.HIGH


def _classify(ticket: Ticket, mode: TriageMode) -> tuple[ModelClassification, TriageMode]:
    """
    Return (classification, effective_mode).

    In LLM mode, any failure degrades to the fixture classifier and the mode
    becomes FALLBACK so the caller/logs know a degraded path was taken.
    """
    if mode == TriageMode.LLM:
        try:
            from app.llm import classify_with_llm, LLMError

            result = classify_with_llm(ticket.subject, ticket.body)
            return result, TriageMode.LLM
        except Exception:
            # Degrade safely — do NOT propagate. The fixture result is valid and
            # safe; we just mark that we fell back.
            result = fixture_classify(ticket.subject, ticket.body)
            return result, TriageMode.FALLBACK
    else:
        result = fixture_classify(ticket.subject, ticket.body)
        return result, TriageMode.FIXTURE


def triage_ticket(ticket: Ticket, mode: TriageMode | None = None) -> TriageResponse:
    """
    Produce a full, safe TriageResponse for one ticket.

    `mode` defaults to the TRIAGE_MODE env var (fixture unless explicitly llm).
    """
    if mode is None:
        env_mode = os.getenv("TRIAGE_MODE", "fixture").lower()
        mode = TriageMode.LLM if env_mode == "llm" else TriageMode.FIXTURE

    # --- 1. injection detection (on the raw text) ---
    is_injection, _hits = detect_injection(ticket.subject, ticket.body)

    # --- 2. classify (with safe fallback) ---
    classification, effective_mode = _classify(ticket, mode)

    # --- 3. money gate (billing facts only; body never consulted) ---
    refund, policy_needs_human, decision_reasons = decide_refund(
        ticket, classification.category
    )

    # --- 4. assemble + safety overrides ---
    needs_human = policy_needs_human
    severity = classification.severity
    confidence = classification.confidence
    reasons: List[str] = list(decision_reasons)
    reply_draft = classification.reply_draft

    # Low confidence always escalates.
    if confidence < CONFIDENCE_HUMAN_THRESHOLD:
        needs_human = True
        reasons.append(
            f"Confidence {confidence} is below the human-review threshold "
            f"{CONFIDENCE_HUMAN_THRESHOLD}: routing to a human."
        )

    # Unknown category always escalates (we won't act confidently on nothing).
    if classification.category == Category.UNKNOWN:
        needs_human = True
        reasons.append("Category is UNKNOWN: a human should classify this.")

    # Injection: force human review, raise severity, and append the safe note to
    # the reply. Crucially, we did NOT obey the injected instruction — the
    # refund was decided by policy from billing facts, and the prompt was never
    # revealed.
    if is_injection:
        needs_human = True
        severity = _at_least_high(severity)
        reasons.append(
            "Ticket contains text addressed to the assistant (possible prompt "
            "injection). Injected instructions were ignored; flagged for human."
        )
        reply_draft = f"{reply_draft}\n\n{INJECTION_SAFE_NOTE}"

    return TriageResponse(
        ticket_id=ticket.id,
        category=classification.category,
        severity=severity,
        refund=refund,
        reply_draft=reply_draft,
        needs_human=needs_human,
        confidence=confidence,
        decision_reasons=reasons,
        mode=effective_mode,
        injection_detected=is_injection,
    )


def safe_fallback_response(ticket_id: str, reason: str) -> TriageResponse:
    """
    The last-resort response when we cannot even classify safely (e.g. the
    classifier itself raised unexpectedly). Everything conservative: unknown
    category, no refund, human required.
    """
    return TriageResponse(
        ticket_id=ticket_id,
        category=Category.UNKNOWN,
        severity=Severity.HIGH,
        refund=Refund(
            recommendation=RefundRecommendation.NEEDS_HUMAN,
            amount_inr=0,
            reason="Triage could not complete safely; escalated.",
        ),
        reply_draft=(
            "Thanks for contacting support. We've received your message and a "
            "team member will review it shortly."
        ),
        needs_human=True,
        confidence=0.0,
        decision_reasons=[f"Safe fallback: {reason}"],
        mode=TriageMode.FALLBACK,
        injection_detected=False,
    )
