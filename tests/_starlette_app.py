"""
tests/_starlette_app.py — a Starlette mirror of app/main.py, for LOCAL testing
only, in sandboxes where FastAPI/httpx aren't installed.

It wires the EXACT same app.handler.handle_triage() behind POST /triage, and
reproduces FastAPI's behaviour of returning a 422 for a body that fails Ticket
validation. This is not shipped; app/main.py (FastAPI) is the real entrypoint.
"""

from __future__ import annotations

import json
import os

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.handler import handle_triage
from app.schemas import Ticket
from app.storage import Storage

_storage = Storage(os.getenv("TRIAGE_DB_PATH", "triage.db"))


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "mode": os.getenv("TRIAGE_MODE", "fixture")})


async def triage(request: Request) -> JSONResponse:
    # Parse body
    try:
        raw = await request.body()
        data = json.loads(raw.decode() or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"detail": "malformed JSON body"}, status_code=422)

    # Validate against the Ticket schema. Mirrors FastAPI's automatic 422.
    try:
        ticket = Ticket(**data)
    except Exception as e:
        return JSONResponse(
            {"detail": "invalid ticket", "error": str(e)}, status_code=422
        )

    response = handle_triage(ticket, _storage)
    return JSONResponse(response.model_dump(mode="json"))


app = Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/triage", triage, methods=["POST"]),
    ]
)
