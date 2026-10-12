"""Replay the supplied ticket set through the configured live LLM path.

Requires python-dotenv and a valid .env. Output contains ticket-derived triage
results, so the JSON artifact is gitignored and must not be committed.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=True)
os.environ["TRIAGE_MODE"] = "llm"

from app.schemas import Ticket  # noqa: E402
from app.triage import triage_ticket  # noqa: E402

DATA_PATH = ROOT / "data" / "dhaba_tickets.json"
OUTPUT_PATH = ROOT / "llm_replay_results.json"


def main() -> int:
    if not DATA_PATH.exists():
        print("Missing data/dhaba_tickets.json. Copy the supplied ticket file there.", file=sys.stderr)
        return 2
    if not os.getenv("LLM_API_KEY"):
        print("LLM_API_KEY is not set. Check your local .env; do not paste the key into chat or commit it.", file=sys.stderr)
        return 2

    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    results = []
    for item in payload["tickets"]:
        ticket = Ticket.model_validate(item)
        result = triage_ticket(ticket).model_dump(mode="json")
        results.append(result)
        print(
            f"{result['ticket_id']}: category={result['category']}, "
            f"mode={result['mode']}, human={result['needs_human']}, "
            f"injection={result['injection_detected']}"
        )

    fallback_count = sum(r["mode"] != "llm" for r in results)
    injections = [r["ticket_id"] for r in results if r["injection_detected"]]
    print(f"\nSUMMARY\nTickets processed: {len(results)}")
    print(f"LLM-mode results: {len(results) - fallback_count}")
    print(f"Fallback results: {fallback_count}")
    print(f"Injection detected: {injections}")
    OUTPUT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Saved {OUTPUT_PATH.name} (do not commit this ticket-derived artifact).")
    return 0 if fallback_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
