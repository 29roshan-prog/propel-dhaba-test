"""
app/fixtures.py — the deterministic, offline classifier.

Purpose (assignment requirement): "a replay or fixture mode, so your evaluator
isn't burning your tokens." This module classifies a ticket into a Category +
Severity + a drafted reply using ONLY rules and keyword signals — no network,
no API key, no randomness. Given the same ticket it always returns the same
classification, which is what makes the replay table reproducible.

This is intentionally simple keyword/há signal matching, not ML. For the 12
assessment tickets it is accurate; for arbitrary new tickets it is a safe
best-effort that degrades to UNKNOWN (which forces human review) rather than a
confident wrong guess.

It returns a `ModelClassification` — the exact same type the LLM path produces —
so triage.py doesn't care which brain answered. The refund is still decided
later by policy.py from billing facts; this module never touches money.
"""

from __future__ import annotations

import re

from app.schemas import Category, ModelClassification, Severity
from app.security import detect_injection

# Hinglish + English keyword signals. Lowercased matching.
_SIGNALS = {
    Category.SUSPECTED_FRAUD: [
        "fraud", "never installed", "did not install", "unauthori", "cyber cell",
        "cyber crime", "not know how to use", "stolen", "someone else",
    ],
    Category.DUPLICATE_CHARGE: [
        "double charge", "twice", "two times", "charged two", "do baar",
        "duplicate",
    ],
    Category.PAYMENT_ACTIVATION: [
        "paisa kat gaya", "paise kat", "money deducted", "deducted but",
        "not activate", "activate nahi", "still free", "free version",
        "paid but", "kat gaya",
    ],
    Category.CANCELLATION: [
        "cancel", "unsubscribe", "stop subscription", "band karo",
    ],
    Category.INVOICE_REQUEST: [
        "invoice", "gst", "gstin", "receipt", "bill chahiye", "reimburse",
    ],
    Category.FEATURE_REQUEST: [
        "feature request", "add ", "please add", "can you add", "support for",
        "language add",
    ],
    Category.TECHNICAL_ISSUE: [
        "crash", "crashes", "not working", "does not open", "won't open",
        "bug", "error", "closes immediately", "app closes",
    ],
    Category.ACCOUNT_DATA_ISSUE: [
        "order history", "restore", "lost data", "data gone", "history empty",
        "changed my phone", "account issue", "login",
    ],
    Category.BILLING_DISPUTE: [
        "without telling", "did not agree", "didn't agree", "wanted to stop",
        "turned off autopay", "autopay", "reverse", "i did not authorize",
        "charged me", "renewed",
    ],
    Category.PLAN_QUESTION: [
        "before i subscribe", "does premium", "offline", "how much",
        "which plan", "about the premium", "about the plan",
    ],
    Category.GENERAL_COMPLAINT: [
        "worst", "useless", "1 star", "one star", "bad review", "play store",
        "terrible",
    ],
}

# Reply templates per category, in a neutral support voice. Hinglish tickets get
# a Hinglish-friendly acknowledgement. These are DRAFTS a human can edit.
_REPLY_TEMPLATES = {
    Category.SUSPECTED_FRAUD: (
        "Thank you for flagging this. Because you've described charges that may "
        "be unauthorised, we're escalating your case to a specialist who will "
        "review the account and the charges in detail and contact you. We take "
        "this seriously and will not close the ticket until it's resolved."
    ),
    Category.DUPLICATE_CHARGE: (
        "Thanks for reaching out. We're checking the payment records for the "
        "charge you mentioned. A team member will confirm exactly what was "
        "charged and correct anything that shouldn't have gone through."
    ),
    Category.PAYMENT_ACTIVATION: (
        "Sorry for the trouble. We can see a payment was started on your "
        "account but not yet confirmed as completed on our side. Please share "
        "your payment reference/screenshot and we'll reconcile it and activate "
        "your plan or sort out the payment."
    ),
    Category.CANCELLATION: (
        "Thanks for letting us know. To make sure we do the right thing, could "
        "you confirm whether you'd like to (a) stop future renewals, (b) cancel "
        "and request a refund, or (c) both? We'll action it as soon as you "
        "confirm."
    ),
    Category.INVOICE_REQUEST: (
        "Happy to help with your invoice. We'll generate the GST invoice for "
        "your payment and send it to your registered email shortly."
    ),
    Category.FEATURE_REQUEST: (
        "Thanks for the suggestion — we've passed it to the product team. We "
        "appreciate you taking the time, and we're glad you're enjoying the app."
    ),
    Category.TECHNICAL_ISSUE: (
        "Sorry the app isn't working as expected. Our team is looking into the "
        "crash you reported. Could you confirm your app version so we can "
        "reproduce it? We'll follow up with a fix or workaround."
    ),
    Category.ACCOUNT_DATA_ISSUE: (
        "Thanks for reaching out. We'll look into restoring your account data "
        "and get back to you. For security, a team member will verify the "
        "account before making any changes."
    ),
    Category.BILLING_DISPUTE: (
        "Thanks for writing in. We've reviewed the charge on your account. "
        "We'll have a team member look at your specific situation and respond "
        "about the best resolution."
    ),
    Category.PLAN_QUESTION: (
        "Thanks for your interest. A team member will confirm the exact plan "
        "details for you so you can decide before subscribing."
    ),
    Category.GENERAL_COMPLAINT: (
        "We're sorry the app hasn't met your expectations. We'd like to "
        "understand what went wrong and make it right — a team member will "
        "review your account and follow up."
    ),
    Category.UNKNOWN: (
        "Thanks for contacting support. We've received your message and a team "
        "member will review it and get back to you shortly."
    ),
}

# Which categories are high severity by default (fast human attention).
_HIGH_SEVERITY = {Category.SUSPECTED_FRAUD, Category.PAYMENT_ACTIVATION}
# Low severity: no money at stake, no user blocked.
_LOW_SEVERITY = {
    Category.FEATURE_REQUEST,
    Category.INVOICE_REQUEST,
    Category.PLAN_QUESTION,
    Category.CANCELLATION,
}


def _severity_for(category: Category, is_injection: bool) -> Severity:
    """Injection attempts are always at least HIGH — someone's manipulating us."""
    if is_injection:
        return Severity.HIGH
    if category in _HIGH_SEVERITY:
        return Severity.HIGH
    if category in _LOW_SEVERITY:
        return Severity.LOW
    return Severity.MEDIUM


def classify(subject: str, body: str) -> ModelClassification:
    """
    Deterministically classify a ticket.

    Order matters: the most safety-critical signals (fraud) are scored with the
    highest weight so an ambiguous ticket tips toward the cautious category.
    """
    text = f"{subject}\n{body}".lower()
    is_injection, _ = detect_injection(subject, body)

    # Score each category by how many of its signals appear.
    scores = {cat: 0 for cat in _SIGNALS}
    for cat, signals in _SIGNALS.items():
        for s in signals:
            if s in text:
                scores[cat] += 1

    # Safety weighting: fraud and payment-activation signals are strong.
    scores[Category.SUSPECTED_FRAUD] *= 3
    scores[Category.PAYMENT_ACTIVATION] *= 2
    scores[Category.DUPLICATE_CHARGE] *= 2

    best_cat = max(scores, key=lambda c: scores[c])
    best_score = scores[best_cat]

    # No signal matched -> UNKNOWN, which forces human review downstream. We do
    # NOT guess a confident category from nothing.
    if best_score == 0:
        category = Category.UNKNOWN
        confidence = 0.30
    else:
        category = best_cat
        # Confidence is a transparent heuristic, NOT a calibrated probability.
        # More distinct signal hits -> a bit more confidence, capped.
        confidence = min(0.6 + 0.1 * best_score, 0.9)

    # An injection attempt makes us less confident in a clean classification and
    # guarantees downstream human review.
    if is_injection:
        confidence = min(confidence, 0.5)

    severity = _severity_for(category, is_injection)
    reply = _REPLY_TEMPLATES.get(category, _REPLY_TEMPLATES[Category.UNKNOWN])

    return ModelClassification(
        category=category,
        severity=severity,
        confidence=round(confidence, 2),
        reply_draft=reply,
    )
