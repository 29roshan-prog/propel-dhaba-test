# Dhaba Triage Service

A small, dependable `POST /triage` endpoint for the Propel full-stack AI
engineer assignment. It takes a support ticket exactly as it appears in
`dhaba_tickets.json` and returns JSON a support tool could act on: a category, a
severity, a **refund recommendation gated by deterministic business logic**, a
drafted reply in the customer's language, a `needs_human` flag, a confidence
estimate, and an audit trail.

> **Honesty note.** This is a hiring assessment, not a shipped product. The
> refund rules here are conservative engineering guardrails I chose and wrote
> down — they are **not** official Dhaba policy. I don't claim the solution is
> mistake-free; I claim it is tested, its failure paths are real, and its
> assumptions are explicit. Two genuine bugs I caught while building it are
> documented in §14.

**Track chosen: backend** (§12).

---

## 1. Project purpose and scope

The job the assignment is actually testing (from the PDF): *schemas that hold,
money decisions that can't be talked into, and logs you can read at 3am.* So the
design centres on three guarantees:

1. **The output holds its shape.** Every model result is validated against a
   strict Pydantic schema with closed enums. Prose, truncated JSON, or an
   invented category never becomes a confident decision — it degrades to a safe,
   human-review response.
2. **Money has a separate gate.** The LLM may classify a ticket and draft a
   reply. It **cannot** decide a refund. Refunds are computed by a pure,
   deterministic function (`app/policy.py`) from **billing facts only** — it
   never even reads the ticket body, so no text can talk it into paying out.
3. **Logs are clean.** The logging helper physically has no parameter for the
   ticket body, subject, or GSTIN, so PII can't be logged by construction.

Out of scope (deliberately, per the brief): no real payment processor, no
frontend, no vector DB, no agent graph. A refund is **recommended**, never
executed.

## 2. Architecture and request flow

```
                   POST /triage  (app/main.py — thin FastAPI shell)
                          │  FastAPI+Pydantic validate body → 422 if bad
                          ▼
                  app/handler.py  (idempotency, logging, safe fallback)
                          │
            ┌─────────────┴──────────────┐
            ▼                             ▼
   storage.get(id)?  ──yes──► return stored decision (no recompute)
            │ no
            ▼
     app/triage.triage_ticket()
            │
            ├─ security.detect_injection()      → flag, force human review
            ├─ classify:
            │     fixture → app/fixtures.py  (deterministic, offline)
            │     llm     → app/llm.py        (provider call; on ANY failure,
            │                                  degrade to fixture, mode=FALLBACK)
            │     └─ result validated against ModelClassification (closed enums)
            ├─ MONEY GATE: app/policy.decide_refund(ticket, category)
            │     └─ pure function of billing facts; body never consulted
            └─ assemble TriageResponse + safety overrides
                  (injection / low-confidence / unknown → needs_human)
            │
            ▼
   storage.save_if_absent()  (UNIQUE ticket_id → runs twice safely)
            │
            ▼
   privacy-safe structured log line  →  TriageResponse (JSON)
```

Why the endpoint is thin: the money decision is nowhere near the request
handler. The handler *cannot* authorise a refund because it doesn't know how —
that capability lives only in `policy.py`.

File map:

| File | Responsibility |
|------|----------------|
| `app/main.py` | FastAPI app, `POST /triage` + `GET /health`. Validation only. |
| `app/handler.py` | Framework-agnostic request logic (idempotency, log, fallback). |
| `app/schemas.py` | Pydantic models + closed enums. The contract. |
| `app/triage.py` | Orchestration: classify → validate → policy → fallback. |
| `app/policy.py` | **The money gate.** Deterministic refund logic. No LLM, no I/O. |
| `app/fixtures.py` | Deterministic offline classifier (fixture mode). |
| `app/llm.py` | Provider-neutral LLM adapter. Timeout + bounded retry. Money-blind. |
| `app/security.py` | Prompt-injection detection + safe handling. |
| `app/storage.py` | SQLite idempotency (`UNIQUE ticket_id`). Minimal, PII-free. |
| `app/logging_config.py` | Structured logs from an allowlisted field set. |
| `scripts/replay_tickets.py` | Runs all 12 tickets, emits the table in §10. |
| `tests/` | Schema, policy, injection, fallback, idempotency, privacy, API. |

## 3. Setup from a clean machine

Requires Python 3.10+.

```bash
git clone <your-repo-url>
cd propel-dhaba-triage

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Place the supplied data file (it is gitignored and NOT in the repo):
mkdir -p data
cp /path/to/dhaba_tickets.json data/dhaba_tickets.json
```

## 4. Environment variables and API-key-free fixture mode

Copy `.env.example` to `.env` (optional — the service runs with no `.env` at
all). The default mode is **fixture**: deterministic, offline, no API key.

| Variable | Default | Meaning |
|----------|---------|---------|
| `TRIAGE_MODE` | `fixture` | `fixture` (offline rules) or `llm` (real provider). |
| `LLM_API_KEY` | _(unset)_ | Only for `llm` mode. Never commit a real key. |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible endpoint. |
| `LLM_MODEL` | `gpt-4o-mini` | Model name. |
| `LLM_TIMEOUT_SECONDS` | `8` | Per-call network timeout. |
| `LLM_MAX_RETRIES` | `1` | Bounded retries before degrading to fixture. |
| `TRIAGE_DB_PATH` | `triage.db` | SQLite idempotency store. |

**Fixture mode is the evaluator path**: it never touches the network and never
spends your tokens. `llm` mode is provider-neutral — point `LLM_BASE_URL` at a
local server (llama.cpp / Ollama OpenAI-compat / vLLM) if you have no paid key.

## 5. Start the server and a sample `curl`

```bash
uvicorn app.main:app --reload
```

```bash
# Illustrative payload (synthetic — not a real supplied ticket).
curl -s -X POST http://127.0.0.1:8000/triage \
  -H 'Content-Type: application/json' \
  -d '{
    "id": "T-DEMO-FRAUD",
    "received_at": "2026-09-05T16:47:00+05:30",
    "subject": "unauthorised charges",
    "body": "an elderly relative says he never installed this; there are charges we did not make.",
    "purchases": [
      {"id":"p1","type":"trial","amount_inr":1,"status":"successful","at":"2026-07-09T21:12:00+05:30"},
      {"id":"p2","type":"renewal","amount_inr":249,"status":"successful","at":"2026-07-10T21:14:00+05:30"},
      {"id":"p3","type":"renewal","amount_inr":249,"status":"successful","at":"2026-08-10T21:13:00+05:30"}
    ],
    "app_opens_since_renewal": 0
  }'
```

Returns (fixture mode): `suspected_fraud`, severity `high`, refund
`needs_human` / 0, `needs_human: true`.

> The replay table in §10 uses the real ticket IDs (T-1001…T-1012) because the
> evaluator runs the supplied file locally; this README never reproduces a real
> ticket's body text.

## 6. Request / response schema

**Request** — a ticket exactly as in the data file (`id`, `received_at`,
`subject`, `body`, `purchases[]`, `app_opens_since_renewal`). See
`docs/INPUT_SCHEMA.md` for the full shape and the `type` vs `status` note.

**Response** (`TriageResponse`):

```jsonc
{
  "ticket_id": "T-1008",
  "category": "suspected_fraud",
  "severity": "high",
  "refund": {
    "recommendation": "needs_human",   // none | recommended | needs_human
    "amount_inr": 0,                    // >0 only when recommendation == recommended
    "reason": "Escalated to human: fraud/unauthorised-use claim."
  },
  "reply_draft": "Thank you for flagging this ...",
  "needs_human": true,
  "confidence": 0.9,                    // heuristic estimate, NOT calibrated
  "decision_reasons": ["Suspected-fraud ... must be reviewed by a human."],
  "mode": "fixture",                    // fixture | llm | fallback
  "injection_detected": false
}
```

**Categories** (closed enum, derived from the 12 tickets): `billing_dispute`,
`payment_activation`, `duplicate_charge`, `suspected_fraud`, `cancellation`,
`technical_issue`, `account_data_issue`, `invoice_request`, `feature_request`,
`plan_question`, `general_complaint`, `unknown`.

**Severity**: `low` (no money/harm), `medium` (money disputed or user blocked),
`high` (fraud, legal threat, money-moved-but-unconfirmed, or injection).

**Confidence** is an explicit heuristic (more matched signals → slightly higher,
capped), **not** a calibrated probability. Below `0.55` the ticket is routed to
a human regardless of category.

## 7. Refund policy assumptions and human-review rules

The brief supplies no full refund policy, so I implemented a **conservative
assessment policy** and wrote the assumptions down here. Guiding principle:

> **A refund requires a verified `successful` charge AND an explicit rule to
> return it. Absent either, the refund stays off; a real grievance we can't
> adjudicate goes to a human.**

Concrete rules (all in `app/policy.py`):

- Only `status == successful` purchases are billing evidence. `initiated` and
  `failed` are **not** proof money moved.
- **Suspected fraud → always human.** No automatic accusation, no automatic
  refund. Checked first so nothing can bypass it.
- **No successful charge → nothing to refund.** If the user claims a payment
  (e.g. an `initiated` renewal, T-1005), a human reconciles; we don't invent a
  completed charge.
- **Duplicate charge** is real only if **two successful charges of the same
  amount** exist. One `failed` + one `successful` (T-1009) is a single charge.
  A genuine duplicate recommends refunding **exactly one** charge, capped, and
  still needs human approval.
- **Billing disputes over a valid charge** (T-1001 trial→activate, T-1006
  autopay claim, T-1012 review threat): the charge is valid; a goodwill/policy
  refund is a human decision, not an automatic one.
- **Cancellation ≠ refund.** "cancel" (T-1004) stops future billing; it never
  authorises returning past money.
- **Invoice/feature/plan/technical** requests need no refund decision.
- A refund amount **never exceeds a single verified successful charge** and is
  **never duplicated**.
- When eligibility can't be determined safely → `needs_human=true` with a
  stated reason; never claim a refund was processed.

## 8. Failure behaviour and idempotency

**Failure behaviour.** In `llm` mode, any provider failure (timeout, HTTP
error, invalid/truncated JSON, invented category) is caught and the request
**degrades to the fixture classifier** with `mode=fallback`. The endpoint never
returns a 500 for a triage it could safely degrade; a truly unexpected error
returns a conservative `safe_fallback_response` (unknown / no refund / human).

**Idempotency.** `POST /triage` with a `ticket_id` already seen returns the
**stored** decision and does not recompute — so a re-delivery can't produce a
second, different side effect. The `UNIQUE` primary key on `ticket_id` makes
this race-safe (concurrent double-delivery: one INSERT wins, the other reads the
stored row).

*Triage-dedup vs external-refund-dedup* (the brief asks): this service dedups
the **triage decision**. It issues no real refund, so there's no external money
side effect to dedup here. A production system would **also** need an
idempotency key on the payment-processor call itself, because retrying that HTTP
call must not move money twice even when our DB row already exists. I note where
that key would live but don't implement a payment integration I was told not to
build.

## 9. How to run tests

```bash
pytest -q
```

The suite covers every brief-required case: valid-schema response, 422 on
missing input, malformed/invented-category fallback, idempotency, T-1003 &
T-1011 injection, T-1005 `initiated`, T-1009 `failed+successful`, T-1008 fraud,
privacy (no GSTIN in logs), fixture-mode-without-key, low-confidence human
review, and provider-timeout safety.

> In a sandbox without pytest/fastapi/httpx you can run the same test files with
> the bundled stdlib runner: `python tests/run_stdlib.py`. pytest on a normal
> machine is the source of truth. Last local run: **38 passed, 0 failed.**

## 10. Replay results table for all 12 tickets

Generated by `python scripts/replay_tickets.py --md` (fixture mode,
deterministic). **This table is produced from actual output, not hand-written.**
The final two columns are my own review.

| Ticket | Category | Sev | Refund | Amt | Human | Inj | Conf | Do I agree? / ambiguity note |
|--------|----------|-----|--------|----:|:---:|:--:|:--:|------------------------------|
| T-1001 | billing_dispute | medium | needs_human | 0 | yes | - | 0.8 | **Agree.** The ₹249 is the documented trial→activate charge. Sincere but not an error; goodwill is a human call. |
| T-1002 | technical_issue | medium | none | 0 | no | - | 0.9 | **Agree.** Crash report, no money question. Could arguably be `needs_human` for a paying user; I kept it a pure tech routing. |
| T-1003 | account_data_issue | high | none | 0 | yes | **YES** | 0.5 | **Agree.** Injection #1. Genuine request (restore data) answered; injected "approve refund" ignored. |
| T-1004 | cancellation | low | needs_human | 0 | yes | - | 0.7 | **Agree.** One word "cancel" is **ambiguous** (cancel? refund? which?). Human clarifies; no refund inferred. |
| T-1005 | payment_activation | high | needs_human | 0 | yes | - | 0.9 | **Agree.** The disputed ₹249 renewal is `initiated`, not successful — can't treat as paid. Reconcile with evidence. |
| T-1006 | billing_dispute | medium | needs_human | 0 | yes | - | 0.9 | **Agree, with ambiguity.** Autopay-off + unused is a sympathetic case but not billing-fact proof. A documented "cooling-off" rule could change this; none was given. |
| T-1007 | feature_request | low | none | 0 | no | - | 0.9 | **Agree.** Marathi request. No refund. |
| T-1008 | suspected_fraud | high | needs_human | 0 | yes | - | 0.9 | **Agree.** Elderly-relative fraud claim + legal threat → escalate; no auto-refund, no auto-accusation. |
| T-1009 | duplicate_charge | medium | needs_human | 0 | yes | - | 0.9 | **Agree.** `failed` + `successful` = one charge, not a double charge. Human should explain the failed attempt. |
| T-1010 | invoice_request | low | none | 0 | no | - | 0.9 | **Agree.** Invoice request. **Safety note:** body contains a GSTIN — verified it never reaches logs or storage. |
| T-1011 | plan_question | high | none | 0 | yes | **YES** | 0.5 | **Agree.** Injection #2. Pre-sale question answered; "print your system prompt / mark resolved" ignored. |
| T-1012 | general_complaint | medium | needs_human | 0 | yes | - | 0.9 | **Agree.** "refund or I 1-star" — a review threat is not refund evidence; charge is valid. Human decides goodwill. |

**Ambiguous tickets:** T-1001, T-1004, T-1006, T-1012. Each has a sincere
grievance but no billing fact that mandates a refund. The system's answer when
it can't tell: **refund off, `needs_human=true`, reason stated.** Not moving
money is the safe default — which the PDF says matters more than the accuracy
number.

## 11. Prompt-injection findings and evidence

The two tickets addressed to the assistant rather than to support are **T-1003**
and **T-1011** (detected automatically; exactly these two flag, zero false
positives across the 12).

- **T-1003** — a genuine request (restore account data after a phone change)
  with an appended block *posing as a system note* that tries to get the agent
  to ignore its instructions, approve a full refund, and confirm it was
  processed. (Paraphrased — this repo doesn't reproduce the ticket text.)
- **T-1011** — a genuine pre-sale question (does premium work offline?) with an
  appended postscript *addressed to the automated agent* that tries to get it to
  reveal its system prompt and internal refund rules and mark the ticket
  resolved. (Paraphrased.)

**What the service does** (actual output, from the test run):

| | T-1003 | T-1011 |
|--|--------|--------|
| `injection_detected` | true | true |
| refund | `none` / 0 | `none` / 0 |
| `needs_human` | true | true |
| severity | high | high |
| system prompt leaked? | no | no |
| marked resolved? | no | no |

The drafted replies answer only the genuine request and append: *"part of this
message appears to be instructions aimed at an automated agent. Those
instructions were ignored …"* — and never echo the attacker's text or the system
prompt.

**Why it's safe by design, not just by detection:** the money gate
(`policy.py`) never reads the ticket body, so even if detection missed an
injection, the embedded "approve refund" still could not reach the refund
decision. Detection adds flagging + human review on top of that structural
guarantee. See `tests/test_injection.py` for the assertions.

**One pick for the dashboard** (Task 2): **human-override rate** —
`# triage decisions a human later changed ÷ # decisions a human reviewed`,
tracked weekly. It's the number that tells you the triage is still trustworthy:
if it climbs, the model or rules have drifted from what humans actually accept.
I'd watch it per-category (a spike in `suspected_fraud` overrides matters more
than one in `feature_request`). *No production data exists yet — this defines
the metric, it doesn't report one.*

## 12. Backend track: 50 tickets/second, storage sketch, 3am debugging

*Proposed scale-up design. Implemented today: the deterministic gate, fixture
mode, idempotency, timeouts/bounded retries, structured logs. The rest below is
design, not yet built.*

**At 50 tickets/s during an outage, don't synchronously call a model per
request.** Changes so it degrades instead of falling over:

- **Accept fast, process async.** `POST /triage` validates, writes the ticket to
  a queue, returns `202 Accepted` with a triage id. Workers pull from the queue.
- **Bounded concurrency + timeouts.** A fixed worker pool and a semaphore cap
  concurrent model calls; each call keeps the existing per-call timeout and
  bounded retry.
- **Circuit breaker.** After N consecutive model failures, trip open and route
  everything to the **fixture classifier** (already deterministic and offline) —
  users still get a safe, categorised, human-flagged result while the model is
  down. Half-open probes restore it.
- **Rate limiting + backpressure.** Shed load by queue depth, never by dropping
  tickets. If the queue is saturated, return `202` + "queued for human review"
  rather than failing — **never silently drop a ticket.**
- **Caching only where safe.** Cache by `ticket_id` (idempotency already does
  this). Do **not** cache refund decisions across different billing states.
- **Degraded UX.** While degraded: fixture classification + `needs_human=true`
  on anything money-related, and a reply that says a human will follow up.

**Storage sketch** (minimum data, with retention):

```
tickets(            ticket_id PK, received_at, status, enqueued_at )        -- no body long-term
triage_decisions(   ticket_id PK/UNIQUE, category, severity, refund_rec,
                    refund_amount, needs_human, confidence, mode, created_at )
processing_log(     request_id PK, ticket_id, outcome, error_class, latency_ms, ts )
```

Unique constraint on `triage_decisions.ticket_id` = idempotency. Store the
**minimum**; the body is processed in-flight and not retained. Retention: raw
ticket text purged after processing / short window; decisions kept longer for
audit, PII-free.

**One thing for 3am debugging:** a **request/correlation id on every log line**
(already emitted), so one `grep <request_id>` reconstructs a ticket's whole
journey — classify → policy → store → fallback — with latency, mode,
fallback/injection flags, and error class, and **no PII**. Add per-provider
status + fallback counters so "is the model down?" is answerable in one query.

## 13. Reliability metric

See §11 — **human-override rate** (numerator: decisions a reviewer changed;
denominator: decisions reviewed; windowed weekly, sliced by category). It's a
safety/trust signal, not a vanity accuracy number, and it degrades visibly when
the system drifts. No production data is claimed.

## 14. AI agent usage disclosure and the genuine mistake caught

**Tools used.** Built with an AI coding agent (Claude, Opus 4.8) driving the
implementation, with me reviewing every file, running the tests, and making the
design calls (category set, severity scale, where the money line is drawn).
Rough split: the agent produced most of the boilerplate and first-draft logic
(~75%); the policy rules, the ambiguity calls, and the test assertions were
shaped by my review and corrections (~25%).

**Best prompt** (copied as sent):

> *"Keep money decisions deterministic. The LLM may classify the issue and draft
> a response; it must not directly authorize or set the refund amount. A
> separate function must validate billing facts and apply explicit rules. If
> facts are incomplete or conflict, set needs_human=true and do not
> automatically issue a refund."*

This is the instruction that produced the central design — `policy.py` as a pure
function the model can't reach.

**One genuine mistake caught** (not manufactured): `app/llm.py` originally did
`import httpx` at the top of the module. Because `app/triage.py` imports
`app/llm.py`, that made the **entire fixture path depend on httpx** — on a
machine without httpx installed, even offline fixture mode would crash on import.
I caught it when `python tests/run_stdlib.py` reported
`IMPORT ERROR tests.test_fallback: No module named 'httpx'`. Fix: move the
import to be **lazy**, inside `classify_with_llm()`, so it's only required when a
network call is actually made. Verified afterwards that fixture triage runs with
httpx absent. (A second, smaller issue was a *test* I wrote that wrongly asserted
T-1005 had zero successful purchases — it has a successful ₹1 trial; only the
₹249 renewal is `initiated`. The code was right; I fixed the assertion.)

**One part written/decided by hand:** the per-ticket refund reasoning and the
category↔ticket mapping (`docs/`, and the policy branches) — directing an agent
to re-derive the correct safe call for each of the 12 tickets was slower than
reasoning through the billing states myself, since the whole point is a human
defending *why* each decision is safe.

## 15. Limitations and future improvements

- **The fixture classifier is keyword-based.** It's 12/12 on the assessment
  tickets but will mislabel novel phrasings; that's why unknown/low-confidence
  routes to a human. It is not an ML model and isn't meant to be.
- **Confidence is heuristic, not calibrated.** Treat it as a routing signal, not
  a probability.
- **No real LLM call was exercised end-to-end in this environment** (no API key
  / network); the `llm.py` path is unit-tested with mocked responses and the
  parse/validate/fallback logic is covered, but a live provider run is untested
  here. On a machine with a key, set `TRIAGE_MODE=llm`.
- **Injection detection is a heuristic flag**, not a security boundary. The real
  defences are architectural (body never reaches the money gate; body is data,
  not instructions). Detection can have false negatives; safety doesn't depend
  on it.
- **No auth / rate limiting on the endpoint** — out of scope for the assessment;
  §12 sketches where they'd go.
- **Retention/PII purge is designed, not implemented** (§12).

---

*Confidential: the assignment PDF and `dhaba_tickets.json` are Propel material
and are **not** committed to this repo (see `.gitignore`).*
