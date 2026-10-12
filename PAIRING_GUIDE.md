# Pairing Guide — defend and change this code live

The live session changes the requirements while you're in it. This guide gets
you ready to (a) explain any file in your own words and (b) make a change and
predict its effect. Work through it with the code open.

---

## The one-sentence mental model

**The LLM classifies and drafts; a deterministic function decides money; the
ticket body is data, never instructions; the same ticket twice is safe.**

If you can say that and point to the file for each clause, you can defend the
whole project:
- classifies/drafts → `app/fixtures.py` or `app/llm.py`
- decides money → `app/policy.py`
- body is data → `app/security.py` + the fact that `policy.py` never reads the body
- same ticket twice → `app/storage.py`

## Request flow in 8 steps (say this out loud)

1. `POST /triage` hits `app/main.py`. FastAPI validates the body against
   `Ticket`. Bad body → automatic **422**.
2. `app/handler.py` checks storage: seen this `ticket_id`? → return the stored
   decision, don't recompute.
3. New ticket → `app/triage.triage_ticket()`.
4. `detect_injection()` scans subject+body (flag only).
5. Classify: fixture rules, or LLM. LLM failure → degrade to fixture, mark
   `mode=fallback`. Result validated against `ModelClassification` (closed enums).
6. **Money gate:** `policy.decide_refund(ticket, category)` — pure function of
   billing facts.
7. Safety overrides: injection / low-confidence / unknown → `needs_human=true`.
8. `storage.save_if_absent()` (UNIQUE id), one privacy-safe log line, return JSON.

---

## Change this → observe this

Each item: what an interviewer might ask you to change, the exact line, what
happens, and which test proves it. **Run the test after any change** — don't
claim a change is safe without it.

### 1. Confidence threshold — escalation rate
- **Where:** `app/triage.py`, `CONFIDENCE_HUMAN_THRESHOLD = 0.55`.
- **Raise to 0.85:** more tickets fall below it → more `needs_human=true`. In
  the current replay, T-1001 (0.8), T-1004 (0.7) and the injection tickets (0.5)
  are already ≤0.85, so escalation rises. Safer, more human load.
- **Lower to 0.3:** fewer escalations; only the 0.5 injection tickets stay
  below. Riskier — borderline classifications go out without review.
- **Proof:** `tests/test_privacy_and_triage.py::test_unknown_category_forces_human`
  asserts UNKNOWN (conf 0.30) escalates; change the constant and that boundary
  moves.
- **Check question:** *if you set it to 1.0, what happens to every ticket?*
  (Answer: every ticket escalates — confidence is always ≤1.0, and `< 1.0`
  catches all but a perfect-1.0 case.)

### 2. Add a category to the enum — the blast radius
- **Where:** `app/schemas.py`, `class Category`.
- **What else must change:** (a) `app/fixtures.py` `_SIGNALS` + a reply template
  + severity bucket; (b) the LLM allowed-list is auto-generated from the enum, so
  the prompt updates itself — but you should add an example; (c) a `policy.py`
  branch if the new category touches money, else it falls into the "no money"
  set or the unreachable safe-default; (d) README table; (e) a test.
- **If you forget `policy.py`:** the new category hits the final safe-default
  branch → `needs_human`, no refund. Safe, but you've added a category that
  always escalates. That's the design working, not failing.
- **Check question:** *which existing test would start failing if you add a
  category but no signal for it?* (Answer: none fail, but the fixture classifier
  will never emit it — it'd be dead unless a signal is added.)

### 3. A severity threshold — which tickets get fast attention
- **Where:** `app/fixtures.py`, `_HIGH_SEVERITY` / `_LOW_SEVERITY` sets, and
  `_severity_for()`.
- **Move `billing_dispute` into `_HIGH_SEVERITY`:** T-1001, T-1006 jump to
  `high`. Nothing about money changes (severity ≠ refund), only triage urgency.
- **Proof:** no money test changes; a severity assertion would.
- **Check question:** *does raising a ticket's severity change its refund?*
  (Answer: no — severity and refund are independent; severity is urgency,
  refund is `policy.py`.)

### 4. A timeout — latency vs reliability
- **Where:** `app/llm.py`, `LLM_TIMEOUT_SECONDS` (env, default 8).
- **Lower to 1s:** faster failure under load, but more legitimate slow responses
  get cut off → more `mode=fallback`. Users still get a safe fixture result.
- **Raise to 30s:** fewer fallbacks, but a slow provider can tie up workers —
  dangerous at 50 req/s (see README §12: that's why you cap concurrency).
- **Proof:** `tests/test_fallback.py::test_llm_timeout_degrades_safely` (a
  simulated timeout yields `mode=fallback`, no unsafe refund).

### 5. The fallback — safety vs experience
- **Where:** `app/triage.py` `_classify()` and `safe_fallback_response()`.
- **If you made fallback return `category=unknown` always** (instead of the
  fixture classification): safer labelling but worse UX — every LLM hiccup
  produces an unclassified ticket. Current design keeps the useful fixture
  category and just marks `mode=fallback`.
- **Check question:** *on LLM failure, why don't we return a 500?* (Answer:
  because a safe degraded answer is better than no answer; the fixture path is
  valid and offline.)

### 6. The refund cap / rule — which test must change
- **Where:** `app/policy.py`, the duplicate-charge branch (`amt = max(...)`).
- **If you changed it to refund the SUM of duplicates:**
  `tests/test_policy.py::test_refund_never_exceeds_single_charge` **fails** (it
  asserts 249, you'd produce 498). That test exists precisely to stop this
  mistake. Don't "fix" the test to match — the cap is a safety rule.
- **Check question:** *why cap at one charge even when three succeeded?*
  (Answer: refunding more than a verified single charge invents money; the
  assignment forbids a refund exceeding a verified eligible charge.)

### 7. Remove the SQLite UNIQUE constraint — duplicate processing
- **Where:** `app/storage.py`, `ticket_id TEXT PRIMARY KEY`.
- **If removed:** `save_if_absent` could insert two rows for the same id, and
  concurrent double-delivery would both "win" → duplicate decisions / duplicate
  side effects in a system that did issue refunds.
  `tests/test_idempotency.py::test_same_ticket_twice_one_row` **fails** (it
  asserts exactly 1 row).
- **Check question:** *what's the difference between deduping the triage result
  and deduping an actual refund?* (Answer: README §8 — we dedupe the decision;
  a real system also needs an idempotency key on the payment-processor call.)

### 8. Model temperature — what changes, what doesn't
- **Where:** `app/llm.py`, `"temperature": 0` in the payload.
- **Raise to 1.0:** replies/classifications become less repeatable. What does
  **not** change: the refund decision (it's in `policy.py`, not the model) and
  schema validity (invalid output still fails validation → fallback). So higher
  temperature can't make money less safe — only classification noisier.
- **Check question:** *can temperature ever cause a wrong refund?* (Answer: no —
  the model never decides refunds.)

### 9. Injection patterns — false positives vs misses
- **Where:** `app/security.py`, `_INJECTION_PATTERNS`.
- **Remove a pattern:** an injection phrasing may stop being *flagged* — but the
  ticket is still **safe**, because the body never reaches the money gate and is
  never treated as instructions. You'd lose the flag + forced review, not the
  core protection.
- **Proof:** `tests/test_injection.py::test_exactly_two_injection_tickets_detected`
  asserts exactly T-1003 & T-1011 flag; editing patterns can break that.
- **Check question:** *if detection misses an injection, can it get a refund?*
  (Answer: no — `policy.py` doesn't read the body.)

### 10. Logging fields — the privacy guarantee
- **Where:** `app/logging_config.py`, `log_event()` signature.
- **If you added a `body=` parameter:** you'd reintroduce the exact risk the
  design prevents. `tests/test_privacy_and_triage.py::test_log_event_cannot_accept_body_fields`
  **fails** — it asserts there is no `body`/`subject`/`gstin` parameter.
- **Check question:** *why is "allowlist of fields" safer than "scrub PII before
  logging"?* (Answer: scrubbing can miss a pattern; an allowlist has nowhere to
  put the PII in the first place.)

---

## Likely "change the requirements" moves in the live session, and your answer

- *"Now actually issue refunds under ₹X automatically."* → Add a branch in
  `policy.py` that, for a verified single successful charge ≤ threshold and a
  non-fraud category, returns `RECOMMENDED` with the amount. Add the external
  idempotency key discussion (README §8). Add a test for the cap and for
  fraud still escalating.
- *"Support a new language in replies."* → `fixtures.py` templates are the draft
  source; the LLM path already drafts in the ticket's language. No money change.
- *"The model keeps returning an extra field."* → `ModelClassification` uses
  `extra="ignore"`, so extra keys are tolerated; closed enums still guard the
  important ones. Show them `_parse_response`.
- *"Make it handle a batch of tickets."* → Loop `triage_ticket` (it's pure in
  fixture mode); point at the replay script as the existing batch path; discuss
  the queue design in §12 for scale.

## Fast file-by-file "why it exists"

- **schemas.py** — the contract; closed enums are the safety net.
- **policy.py** — the money gate; pure function; never reads the body.
- **triage.py** — orchestration + safety overrides; where fallback happens.
- **fixtures.py** — deterministic offline brain; makes replay reproducible.
- **llm.py** — provider adapter; money-blind; lazy httpx; timeout+retry.
- **security.py** — injection flag; defence-in-depth, not the main defence.
- **storage.py** — idempotency via UNIQUE id; stores the minimum.
- **logging_config.py** — PII-safe by construction (allowlist).
- **handler.py / main.py** — thin shell; validation → 422; delegates.

If you can answer each "check question" above without looking, you're ready.
