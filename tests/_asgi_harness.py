"""
tests/_asgi_harness.py — a dependency-free ASGI driver.

Why this exists: the grading machine runs the real FastAPI app on uvicorn. But
in some sandboxes FastAPI/httpx aren't installed, so Starlette's TestClient
(which needs httpx) can't run. This harness drives an ASGI app directly with a
hand-built scope/receive/send, using only the Python stdlib. It lets us exercise
the REAL request/response cycle (routing, JSON body, status codes) without any
extra dependency.

It is a TEST helper, not part of the service. The production entrypoint is
app/main.py (FastAPI).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional


class Response:
    def __init__(self, status: int, headers: list, body: bytes):
        self.status = status
        self.headers = {k.decode().lower(): v.decode() for k, v in headers}
        self._body = body

    def json(self) -> Any:
        return json.loads(self._body.decode() or "null")

    @property
    def text(self) -> str:
        return self._body.decode()


def request(app, method: str, path: str, json_body: Optional[dict] = None) -> Response:
    """Send one request through an ASGI `app` and return the Response."""
    body_bytes = b"" if json_body is None else json.dumps(json_body).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body_bytes)).encode()),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }

    sent = {"status": 500, "headers": [], "body": b""}
    body_consumed = {"done": False}

    async def receive():
        if body_consumed["done"]:
            return {"type": "http.disconnect"}
        body_consumed["done"] = True
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
            sent["headers"] = message.get("headers", [])
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    async def _run():
        await app(scope, receive, send)

    asyncio.run(_run())
    return Response(sent["status"], sent["headers"], sent["body"])
