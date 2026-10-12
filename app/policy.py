"""
app/policy.py — the money gate. The most important file in the repo.

The assignment is explicit: "a refund decision should not be a field the model
can freely write ... where you draw that line is most of our read on you."

So the rule is absolute: the LLM may classify a ticket and draft a reply, but
the refund decision is computed HERE, by deterministic Python, from billing
facts only — never from the model's opinion and never from what the ticket body
*claims*.

Design shape:
- `decide_refund(ticket, category)` is a pure function: same inputs -> same
  output, no I/O, no randomness, no clock. That is what makes it testable and
  what makes money decisions reproducible.
- It returns a `Refund` plus a list of human-readable `decision_reasons` and a
  `needs_human` flag. The reasons are the audit trail.

The guiding principle, stated once: **a refund requires a VERIFIED SUCCESSFUL
charge AND an explicit rule that says to return it. Absent either, the refund
stays off.** When there's a real grievance we can't adjudicate, we escalate to a
human rather than guess with money.

These are conservative engineering guardrails for this assessment, NOT official
Dhaba policy. They are written down (here and in the README) rather than
invented silently.
"""

from __future__ import annotations

from typing import List, Tuple

from app.schemas import (
    Category,
    Purchase,
    PurchaseStatus,
    Refund,
    RefundRecommendation,
    Ticket,
)


# --------------------------------------------------------------------------- #
# Billing-fact helpers. These read ONLY structured purchase records, never the
# free-text body. The body can lie or be manipulated; the ledger is the truth.
# --------------------------------------------------------------------------- #
def successful_purchases(ticket: Ticket) -> List[Purchase]:
    """Purchases that actually went through. The only refundable evidence."""
    return [p for p in ticket.purchases if p.status == PurchaseStatus.SUCCESSFUL]


def has_status(ticket: Ticket, status: PurchaseStatus) -> bool:
    return any(p.status == status for p in ticket.purchases)


def max_successful_charge(ticket: Ticket) -> int:
    """
    The ceiling for any refund: we never recommend more than a single verified
    successful charge. Returns 0 if there is no successful charge at all.
    """
    amounts = [p.amount_inr for p in successful_purchases(ticket)]
    return max(amounts) if amounts else 0


# --------------------------------------------------------------------------- #
# The gate.
# --------------------------------------------------------------------------- #
def decide_refund(ticket: Ticket, category: Category) -> Tuple[Refund, bool, List[str]]:
    """
    Decide the money outcome for a ticket.

    Returns: (Refund, needs_human, decision_reasons)

    The category comes from the classifier (LLM or fixture), but note: category
    only *routes* the decision to the right rule. The decision itself is made
    from billing facts. A mis-classification cannot, by itself, cause a refund —
    because every branch that could pay out still re-checks the ledger.
    """
    reasons: List[str] = []
    succ = successful_purchases(ticket)
    ceiling = max_successful_charge(ticket)

    # ------------------------------------------------------------------ #
    # Rule 0 — Suspected fraud always goes to a human. No automatic refund,
    # no automatic accusation. (T-1008)
    # This is checked FIRST so no other branch can short-circuit it.
    # ------------------------------------------------------------------ #
    if category == Category.SUSPECTED_FRAUD:
        reasons.append(
            "Suspected-fraud/unauthorised-use report: must be reviewed by a "
            "human. No automatic refund or accusation."
        )
        return (
            Refund(
                recommendation=RefundRecommendation.NEEDS_HUMAN,
                amount_inr=0,
                reason="Escalated to human: fraud/unauthorised-use claim.",
            ),
            True,
            reasons,
        )

    # ------------------------------------------------------------------ #
    # Rule 1 — No successful charge => nothing to refund.
    # Covers the trap cases:
    #   * T-1005: renewal is `initiated`, not successful -> cannot prove money
    #     left the bank. We do NOT refund an unconfirmed charge.
    #   * T-1011: empty purchases entirely.
    # If the user nonetheless claims money moved, that's a reconciliation job
    # for a human, not an invented refund.
    # ------------------------------------------------------------------ #
    if not succ:
        if has_status(ticket, PurchaseStatus.INITIATED):
            reasons.append(
                "A payment is 'initiated' but not 'successful': the charge is "
                "unconfirmed. Cannot treat as a completed payment (T-1005 shape)."
            )
        else:
            reasons.append(
                "No successful charge on record: there is no verified payment "
                "to refund."
            )
        # If the user is claiming a charge, a human should reconcile.
        needs_human = category in {
            Category.PAYMENT_ACTIVATION,
            Category.BILLING_DISPUTE,
            Category.DUPLICATE_CHARGE,
        }
        if needs_human:
            reasons.append(
                "User reports a payment problem but billing shows no successful "
                "charge: route to human to reconcile (request evidence)."
            )
        return (
            Refund(
                recommendation=(
                    RefundRecommendation.NEEDS_HUMAN
                    if needs_human
                    else RefundRecommendation.NONE
                ),
                amount_inr=0,
                reason="No verified successful charge to refund.",
            ),
            needs_human,
            reasons,
        )

    # ------------------------------------------------------------------ #
    # Rule 2 — Duplicate charge: only real if TWO successful charges of the
    # same amount exist. One failed + one successful is NOT a double charge.
    # (T-1009: failed + successful -> exactly one real charge.)
    # ------------------------------------------------------------------ #
    if category == Category.DUPLICATE_CHARGE:
        # Count successful charges grouped by amount.
        from collections import Counter

        amount_counts = Counter(p.amount_inr for p in succ)
        duplicated_amounts = [amt for amt, n in amount_counts.items() if n >= 2]
        if duplicated_amounts:
            # A genuine duplicate: refund exactly ONE of the duplicated charges.
            # Even here we escalate, because auto-refunding is a money side
            # effect; the assignment prefers no automatic payout. We surface a
            # RECOMMENDED amount for the human to approve.
            amt = max(duplicated_amounts)
            reasons.append(
                f"Two or more successful charges of INR {amt} found: a genuine "
                f"duplicate. Recommend refunding one (INR {amt}); human approves."
            )
            return (
                Refund(
                    recommendation=RefundRecommendation.RECOMMENDED,
                    amount_inr=amt,
                    reason=f"Duplicate successful charge of INR {amt} detected; "
                    f"refund one instance.",
                ),
                True,  # still needs a human to approve the payout
                reasons,
            )
        else:
            reasons.append(
                "Claim of double charge, but the ledger shows only one "
                "successful charge (other attempts failed/initiated). No "
                "duplicate to refund (T-1009 shape)."
            )
            return (
                Refund(
                    recommendation=RefundRecommendation.NEEDS_HUMAN,
                    amount_inr=0,
                    reason="No duplicate successful charge found; human should "
                    "explain the failed attempt to the user.",
                ),
                True,
                reasons,
            )

    # ------------------------------------------------------------------ #
    # Rule 3 — Billing disputes over a VALID charge (T-1001, T-1006, T-1012).
    # A successful charge exists, but the user disputes it ("didn't agree",
    # "wanted to stop", "refund or I'll 1-star"). None of these is, by itself,
    # evidence the charge was wrong. The trial->activate model is documented;
    # a bad-review threat is not a billing fact; an autopay claim needs
    # verification we don't have. => do not auto-refund; route to human for a
    # goodwill/policy decision.
    # ------------------------------------------------------------------ #
    if category in {Category.BILLING_DISPUTE, Category.GENERAL_COMPLAINT}:
        reasons.append(
            f"A successful charge of up to INR {ceiling} exists and appears "
            "valid under the documented pricing model. The dispute is not "
            "backed by a billing fact that mandates a refund."
        )
        reasons.append(
            "Routing to human for a goodwill/policy decision rather than "
            "auto-refunding or auto-denying."
        )
        return (
            Refund(
                recommendation=RefundRecommendation.NEEDS_HUMAN,
                amount_inr=0,
                reason="Charge appears valid; goodwill/policy call left to human.",
            ),
            True,
            reasons,
        )

    # ------------------------------------------------------------------ #
    # Rule 4 — Payment/activation issues with a successful charge (money
    # confirmed but service not delivered). A refund MIGHT be warranted, but
    # the safe default is: fix activation first, human decides refund.
    # ------------------------------------------------------------------ #
    if category == Category.PAYMENT_ACTIVATION:
        reasons.append(
            f"Successful charge of INR {ceiling} but user reports service not "
            "activated. Priority is to deliver the service; a refund is a human "
            "decision if activation cannot be fixed."
        )
        return (
            Refund(
                recommendation=RefundRecommendation.NEEDS_HUMAN,
                amount_inr=0,
                reason="Confirmed payment, activation problem: human to resolve "
                "delivery or refund.",
            ),
            True,
            reasons,
        )

    # ------------------------------------------------------------------ #
    # Rule 5 — Cancellation (T-1004). Cancelling is not refunding. A one-word
    # "cancel" expresses intent to stop future billing; it does not authorize
    # returning past money. Route to the cancellation workflow; human confirms
    # scope.
    # ------------------------------------------------------------------ #
    if category == Category.CANCELLATION:
        # A cancellation label can hide a dispute about a completed renewal
        # (T-1006). Do not inspect free text here: the classifier owns intent,
        # while this gate reports only what the structured ledger proves.
        successful_renewals = [
            p for p in succ if p.type.value == "renewal"
        ]
        if successful_renewals:
            latest_renewal_amount = successful_renewals[-1].amount_inr
            reasons.append(
                f"Cancellation intent detected and the ledger contains a "
                f"successful renewal of INR {latest_renewal_amount}. The ledger "
                "does not establish whether cancellation was requested or "
                "effective before renewal; a human must verify the timeline "
                "and applicable policy."
            )
            refund_reason = (
                "Successful renewal recorded; verify cancellation timing and "
                "refund eligibility with a human. No refund inferred."
            )
        else:
            reasons.append(
                "Cancellation intent detected. Cancelling future billing is not "
                "the same as refunding a past charge; no refund is authorised by "
                "the request alone."
            )
            refund_reason = (
                "Cancellation request; confirm scope with user. No refund inferred."
            )
        return (
            Refund(
                recommendation=RefundRecommendation.NEEDS_HUMAN,
                amount_inr=0,
                reason=refund_reason,
            ),
            True,
            reasons,
        )

    # ------------------------------------------------------------------ #
    # Rule 6 — Everything else (technical issue, account/data, invoice,
    # feature, plan question, unknown, account): no refund decision needed.
    # Some still want a human (account/data restore), but no money moves.
    # ------------------------------------------------------------------ #
    no_money_categories = {
        Category.TECHNICAL_ISSUE,
        Category.ACCOUNT_DATA_ISSUE,
        Category.INVOICE_REQUEST,
        Category.FEATURE_REQUEST,
        Category.PLAN_QUESTION,
        Category.UNKNOWN,
    }
    if category in no_money_categories:
        # account/data restore and unknown get a human; pure info requests don't.
        needs_human = category in {Category.ACCOUNT_DATA_ISSUE, Category.UNKNOWN}
        reasons.append(
            f"Category '{category.value}' does not involve a refund decision."
        )
        if needs_human:
            reasons.append("Routed to human for handling (account/data or unknown).")
        return (
            Refund(
                recommendation=RefundRecommendation.NONE,
                amount_inr=0,
                reason="No refund applicable to this category.",
            ),
            needs_human,
            reasons,
        )

    # ------------------------------------------------------------------ #
    # Fell through every branch: the safest possible outcome. This should be
    # unreachable because Category is a closed enum, but if a new category is
    # added without a rule, we must NOT silently emit a confident no-refund.
    # ------------------------------------------------------------------ #
    reasons.append(
        f"No policy rule matched category '{category.value}'. Escalating to "
        "human as the safe default."
    )
    return (
        Refund(
            recommendation=RefundRecommendation.NEEDS_HUMAN,
            amount_inr=0,
            reason="Unhandled category; escalated.",
        ),
        True,
        reasons,
    )
