"""Measure inbox-to-first-action latency.

The customer asked for this number specifically, so it is measured rather than
estimated. "It depends" is a worse answer than a number with its assumptions
stated.

What is being measured
----------------------
From ``receivedDateTime`` on the message in Exchange -- the moment the alert
actually landed, not the moment we noticed it -- to the agent's first tool call.
That is the honest boundary: an operator's stopwatch starts when the mail
arrives, not when a poll happens to fire.

The number therefore includes polling delay, which is the largest and least
interesting component. Both are reported separately so the polling interval can
be argued about without confusing it with agent latency.
"""

from __future__ import annotations

import asyncio
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from triage_demo.runner import TriageRunner  # noqa: E402
from triage_demo.settings import settings  # noqa: E402


def _parse(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


async def main() -> int:
    runner = TriageRunner(settings, base_dir=REPO_ROOT)
    inbox = runner.build_inbox()
    live = type(inbox).__name__ == "GraphInbox"

    print(f"inbox: {type(inbox).__name__}  mailbox: {settings.graph_mailbox or '(mock)'}")
    if not live:
        print("Not in live mode; set TRIAGE_TOOL_MODE=live and GRAPH_* to measure for real.")
        return 1

    requests = await inbox.fetch(limit=10)
    if not requests:
        print("No new alerts in the mailbox. Send one, then re-run.")
        return 1

    first_action: list[float] = []
    end_to_end: list[float] = []

    for request in requests:
        received = _parse(request.received_at)
        noticed = datetime.now(UTC)

        started = asyncio.get_running_loop().time()
        artifacts = await runner.run_request(request)
        elapsed = asyncio.get_running_loop().time() - started

        first_action.append(elapsed)
        if received is not None:
            end_to_end.append((noticed - received).total_seconds() + elapsed)

        outcome = getattr(getattr(artifacts, "result", None), "outcome", "?")
        print(f"  {request.subject[:52]:<52} {outcome:<22} {elapsed:5.1f}s")

    print()
    print(f"agent time (fetch -> outcome):      {statistics.mean(first_action):5.1f}s mean")
    if end_to_end:
        print(f"arrival -> outcome, incl. polling:  {statistics.mean(end_to_end):5.1f}s mean")
    print(f"poll interval configured:           {settings.graph_poll_seconds}s")
    print()
    print("Worst case adds one poll interval: an alert arriving just after a")
    print("sweep waits the full interval before anything happens.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
