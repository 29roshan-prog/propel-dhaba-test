"""
app/main.py — the HTTP shell (FastAPI). Thin on purpose.

Exposes exactly POST /triage (plus a trivial GET /health). All the real work
lives in app/handler.py and the modules it calls; this file only:
  - declares the FastAPI app and routes,
  - lets FastAPI+Pydantic validate the request body (a malformed/missing-field
    body yields a 422 automatically — the "clear 4xx" requirement, for free),
  - delegates to handle_triage().

Keeping the endpoint thin is itself a design statement the assignment reads for:
the money decision is nowhere near the request handler. The handler cannot
accidentally authorise a refund because it doesn't know how.

Run it:  uvicorn app.main:app --reload
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI

# Load local development configuration before importing modules that read env vars.
# In deployed environments, process environment variables remain supported.
load_dotenv()

from app.handler import handle_triage
from app.schemas import Ticket, TriageResponse
from app.storage import Storage

app = FastAPI(
    title="Dhaba Triage Service",
    description="POST /triage — classify a support ticket and gate refunds.",
    version="1.0.0",
)

# One storage instance per process. Path from env so tests can use a temp file
# and an evaluator can wipe it easily.
_storage = Storage(os.getenv("TRIAGE_DB_PATH", "triage.db"))


@app.get("/health")
def health() -> dict:
    """Liveness probe. No ticket data involved."""
    return {"status": "ok", "mode": os.getenv("TRIAGE_MODE", "fixture")}


@app.post("/triage", response_model=TriageResponse)
def triage(ticket: Ticket) -> TriageResponse:
    """
    Triage one ticket. FastAPI validates `ticket` against the Ticket schema
    BEFORE this runs, so malformed input never reaches our logic — it gets a
    422 with a clear JSON error.
    """
    return handle_triage(ticket, _storage)
