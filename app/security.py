"""
app/security.py — treat every ticket as untrusted input.

The assignment's framing: "two of the tickets contain text addressed to the
assistant rather than to us." The job is NOT to sanitise the text into
safety — you can't reliably strip an injection. The job is architectural:

  1. The ticket body is DATA. It is never concatenated into the system
     instructions. (app/llm.py puts it in a clearly delimited user turn.)
  2. The money gate (app/policy.py) never reads the body at all, so no text can
     talk it into a refund.
  3. We additionally *detect* likely injection attempts so we can flag them,
     raise severity, and force human review — because a user trying to
     manipulate the assistant is itself a signal worth surfacing.

Detection here is a heuristic flag, not a security boundary. The real defences
are #1 and #2. If detection misses an injection, the system is still safe
because the body can't move money or change instructions regardless.
"""

from __future__ import annotations

import re
from typing import List, Tuple

# Phrases that indicate text is trying to steer the assistant rather than
# describe a support problem. Deliberately broad; false positives only cost us
# an extra human review, which is the safe direction.
_INJECTION_PATTERNS: List[re.Pattern] = [
    re.compile(r"ignore (your |all |previous )?(instructions|rules)", re.I),
    re.compile(r"system (note|prompt|message)", re.I),
    re.compile(r"(reveal|print|show|share|output).{0,30}(system )?prompt", re.I),
    re.compile(r"internal (refund )?(rules|policy|policies)", re.I),
    re.compile(r"approve (a |the )?(full )?refund", re.I),
    re.compile(r"mark (this )?(ticket )?(as )?resolved", re.I),
    re.compile(r"you are (now )?(a |an )?(verified |vip)", re.I),
    re.compile(r"for the (automated )?(agent|assistant|ai|bot) reading", re.I),
    re.compile(r"confirm(ing)? (the )?refund has been (processed|completed)", re.I),
    re.compile(r"disregard (the )?(above|policy)", re.I),
]


def detect_injection(subject: str, body: str) -> Tuple[bool, List[str]]:
    """
    Return (is_injection, matched_fragments).

    `matched_fragments` names WHICH pattern class fired (not the raw text), so
    the signal can be logged/flagged without echoing the attacker's payload.
    """
    text = f"{subject}\n{body}"
    hits: List[str] = []
    for pat in _INJECTION_PATTERNS:
        if pat.search(text):
            # Record the pattern's intent, not the matched user text, to avoid
            # repeating the injection in our own outputs/logs.
            hits.append(pat.pattern.split(r"(")[0].strip()[:40])
    return (len(hits) > 0, hits)


# The fixed refusal line we use when a ticket tries to extract the prompt or
# force a resolution. It answers the *genuine* request elsewhere; this only
# covers the injected part.
INJECTION_SAFE_NOTE = (
    "Note: part of this message appears to be instructions aimed at an "
    "automated agent. Those instructions were ignored. Only the genuine "
    "support request was acted on, and this ticket has been flagged for human "
    "review."
)
