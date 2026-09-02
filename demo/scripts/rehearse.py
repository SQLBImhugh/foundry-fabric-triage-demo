"""Two consecutive full rehearsals, in run-sheet order.

A rehearsal is not "do the scenarios pass". It is: does the whole sequence hold
together twice in a row, in the order it will be presented, with the reset
procedure in between. Scenarios that pass individually can still fail as a set
-- suppression needs an open incident from an earlier run, and a reset that
half-works leaves the second rehearsal looking fine on stage and wrong in the
incident store.

Run order matters and mirrors the run sheet: scenario 2b suppresses an incident
created by an earlier run, and scenarios 3 onward keep incidents so the ledger
and the incident store accumulate the way they will on the day.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXE = REPO / ".venv" / "Scripts" / "triage-demo.exe"
ANSI = re.compile(r"\x1b\[[0-9;]*m")

# (scenario, keep_incidents) -- mirrors docs/run-sheet.md.
ORDER: list[tuple[str, bool]] = [
    ("scenario1-transient", False),
    ("scenario2-data-quality", True),
    ("scenario2b-known-issue", False),
    ("scenario3-policy-block", True),
    ("scenario4-unknown-action", True),
    ("scenario5-approval-granted", True),
    ("scenario6-approval-denied", True),
]


def _run(args: list[str], env: dict[str, str]) -> tuple[int, str]:
    proc = subprocess.run(
        [str(EXE), *args], capture_output=True, text=True, env=env, cwd=str(REPO),
        encoding="utf-8", errors="replace",
    )
    return proc.returncode, ANSI.sub("", (proc.stdout or "") + (proc.stderr or ""))


def rehearse(number: int, env: dict[str, str]) -> tuple[bool, float, int]:
    print(f"\n=== Rehearsal {number} ===", flush=True)

    code, _ = _run(["reset"], env)
    if code != 0:
        print("  reset FAILED -- a rehearsal that starts dirty proves nothing")
        return False, 0.0, 0

    ok = True
    started = time.time()
    for scenario, keep in ORDER:
        args = ["run", scenario] + (["--keep-incidents"] if keep else [])
        code, text = _run(args, env)
        outcome = "?"
        match = re.search(r"\|\s*outcome\s+(\w+)", text) or re.search(r"outcome=(\w+)", text)
        if match:
            outcome = match.group(1)
        status = "PASS" if code == 0 else "FAIL"
        if code != 0:
            ok = False
        print(f"  {status}  {scenario:<30} {outcome}", flush=True)

    elapsed = time.time() - started

    # The incident store is the thing most likely to be quietly wrong: every
    # terminal outcome must be there, including the refusals and the denial.
    code, text = _run(["incidents"], env)
    rows = len(re.findall(r"^\│\s+(u?sig:)", text, re.M))
    print(f"  incidents recorded: {rows}   elapsed: {elapsed:.0f}s")
    if rows == 0:
        print("  incident store EMPTY after a full pass -- that is a failure")
        ok = False

    return ok, elapsed, rows


def main() -> int:
    env = dict(os.environ)
    env.update({
        "TRIAGE_PROVIDER_MODE": os.environ.get("TRIAGE_PROVIDER_MODE", "foundry"),
        "TRIAGE_TOOL_MODE": os.environ.get("TRIAGE_TOOL_MODE", "mock"),
        # Rehearse against the local store so a rehearsal never mutates the
        # durable incident table the deployed agent is using.
        "INCIDENT_TABLE_ENDPOINT": "",
    })
    print(f"provider={env['TRIAGE_PROVIDER_MODE']}  tools={env['TRIAGE_TOOL_MODE']}")

    results = [rehearse(n, env) for n in (1, 2)]

    print("\n" + "=" * 60)
    for n, (ok, elapsed, rows) in enumerate(results, start=1):
        print(f"Rehearsal {n}: {'CLEAN' if ok else 'FAILED'}   {elapsed:.0f}s   {rows} incident(s)")

    # Two rehearsals of identical scenarios should leave identical incident
    # state. A difference means something is non-deterministic in a place the
    # scenario assertions do not cover -- worth knowing before a customer asks
    # why the queue looks different the second time.
    counts = {rows for _, _, rows in results}
    if len(counts) > 1:
        print(f"\nWARNING: incident counts differ between rehearsals {sorted(counts)}.")
        print("Scenario expectations passed, so outcomes were correct, but the")
        print("incident store did not end up in the same state twice. Investigate")
        print("before relying on the incident queue as a demo beat.")

    both = all(ok for ok, _, _ in results)
    print("\nExit criteria (two consecutive clean rehearsals):",
          "MET" if both else "NOT MET")
    return 0 if both else 1


if __name__ == "__main__":
    sys.exit(main())
