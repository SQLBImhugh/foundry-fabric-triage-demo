"""Measure how often a scenario fails its expectations against the live model.

Run once, a scenario "works". Run it five times and you find out whether it
works reliably enough to put in front of a customer. A demo beat that fails one
time in five will fail in the room eventually, and the room is the worst place
to discover it.

Usage:
    python scripts\\flake_check.py scenario2b-known-issue scenario4-unknown-action
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
RUNS = int(os.environ.get("FLAKE_RUNS", "5"))


def main(scenarios: list[str]) -> int:
    env = dict(os.environ)
    env.update({
        "TRIAGE_PROVIDER_MODE": os.environ.get("TRIAGE_PROVIDER_MODE", "foundry"),
        "TRIAGE_TOOL_MODE": "mock",
        "INCIDENT_TABLE_ENDPOINT": "",
    })
    print(f"provider={env['TRIAGE_PROVIDER_MODE']}  runs={RUNS} each\n")

    worst = 0
    for scenario in scenarios:
        failures: list[str] = []
        durations: list[float] = []
        for i in range(RUNS):
            subprocess.run([str(EXE), "reset"], capture_output=True, cwd=str(REPO))
            started = time.time()
            proc = subprocess.run(
                [str(EXE), "run", scenario], capture_output=True, text=True,
                env=env, cwd=str(REPO), encoding="utf-8", errors="replace",
            )
            durations.append(time.time() - started)
            if proc.returncode != 0:
                text = ANSI.sub("", (proc.stdout or "") + (proc.stderr or ""))
                # The expectation report lists each unmet assertion after the
                # "Expectations FAILED" banner.
                reasons = re.findall(r"^\s*[-│|]\s*(.*(?:expected|got|missing).*)$",
                                     text, re.M | re.I)
                failures.append(reasons[0].strip() if reasons else "unknown reason")
            print(f"  {scenario:<30} run {i + 1}/{RUNS}  "
                  f"{'PASS' if proc.returncode == 0 else 'FAIL'}", flush=True)

        rate = len(failures)
        worst = max(worst, rate)
        mean = sum(durations) / len(durations)
        print(f"\n  {scenario}: {RUNS - rate}/{RUNS} passed, mean {mean:.0f}s")
        for reason in dict.fromkeys(failures):
            # Strip box-drawing characters: this runs on Windows consoles that
            # default to cp1252, and a crash while reporting a failure is worse
            # than a slightly plainer message.
            clean = reason.encode("ascii", "replace").decode("ascii")
            print(f"    reason: {clean[:150]}")
        print()

    print("=" * 60)
    if worst == 0:
        print("No flakes observed. Safe to present as-is.")
    else:
        print(f"Worst case {worst}/{RUNS} failures. Fix or make the controller decide,")
        print("rather than hoping the model behaves on the day.")
    return 0 if worst == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["scenario2b-known-issue", "scenario4-unknown-action"]))
