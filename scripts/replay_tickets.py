"""
scripts/replay_tickets.py — run all 12 tickets through the service, reproducibly.

Usage:
    python scripts/replay_tickets.py            # prints JSON + markdown table
    python scripts/replay_tickets.py --json     # JSON only
    python scripts/replay_tickets.py --md       # markdown table only

It runs in FIXTURE mode (no API key, deterministic) so an evaluator gets the
same output every time without spending tokens. It reads data/dhaba_tickets.json
(local, gitignored). The markdown table it prints is the exact source for the
README's replay table — the README table is generated, not hand-written.

It uses the SAME triage path as the live endpoint (app.triage.triage_ticket),
so the replay reflects real behaviour.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Make `app` importable when run as `python scripts/replay_tickets.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Force fixture mode for a deterministic, key-free replay.
os.environ.setdefault("TRIAGE_MODE", "fixture")

from app.schemas import Ticket  # noqa: E402
from app.triage import triage_ticket  # noqa: E402

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "dhaba_tickets.json"


def run() -> list[dict]:
    if not DATA_PATH.exists():
        print(
            f"ERROR: {DATA_PATH} not found. Place the supplied dhaba_tickets.json "
            "in data/ (it is gitignored).",
            file=sys.stderr,
        )
        sys.exit(2)

    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    tickets = raw["tickets"]
    assert len(tickets) == 12, f"expected exactly 12 tickets, found {len(tickets)}"

    results = []
    for t in tickets:
        ticket = Ticket(**t)
        resp = triage_ticket(ticket)
        results.append(resp.model_dump(mode="json"))
    return results


def to_markdown(results: list[dict]) -> str:
    """Build the README replay table from ACTUAL results."""
    header = (
        "| Ticket | Category | Severity | Refund | Amount (INR) | Needs human | "
        "Injection | Confidence |\n"
        "|--------|----------|----------|--------|-------------:|:-----------:|"
        ":--------:|:----------:|"
    )
    rows = []
    for r in results:
        rows.append(
            f"| {r['ticket_id']} "
            f"| {r['category']} "
            f"| {r['severity']} "
            f"| {r['refund']['recommendation']} "
            f"| {r['refund']['amount_inr']} "
            f"| {'yes' if r['needs_human'] else 'no'} "
            f"| {'YES' if r['injection_detected'] else '-'} "
            f"| {r['confidence']} |"
        )
    return header + "\n" + "\n".join(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="print JSON only")
    ap.add_argument("--md", action="store_true", help="print markdown table only")
    args = ap.parse_args()

    results = run()

    if args.md:
        print(to_markdown(results))
        return
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return

    # Default: both, with a short machine-readable summary line.
    print("=== REPLAY (fixture mode, deterministic) ===\n")
    print(to_markdown(results))
    print("\n=== JSON ===\n")
    print(json.dumps(results, indent=2, ensure_ascii=False))

    # Safety summary an evaluator can eyeball.
    auto = [r["ticket_id"] for r in results if r["refund"]["recommendation"] == "recommended"]
    inj = [r["ticket_id"] for r in results if r["injection_detected"]]
    human = [r["ticket_id"] for r in results if r["needs_human"]]
    print("\n=== SUMMARY ===")
    print(f"auto-refund recommendations: {auto or 'none'}")
    print(f"injection detected:          {inj}")
    print(f"needs human:                 {human}")


if __name__ == "__main__":
    main()
