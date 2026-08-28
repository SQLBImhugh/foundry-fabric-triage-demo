"""Head-to-head comparison of demo models across every scenario.

The scenarios carry `expect` blocks, so "did the model behave correctly" is a
deterministic check rather than a judgement call. Tools stay mocked so the only
variable is the model; the provider is real Foundry so the reasoning is real.

Reports pass/fail, the outcome each model reached, and wall-clock latency --
latency matters for a live demo, where a model that is right but slow is a
worse choice than one that is right and quick.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXE = REPO / ".venv" / "Scripts" / "triage-demo.exe"

MODELS = {
    "gpt-5.6-luna": ("bi-triage", "bi-data-quality"),
    "gpt-5.4": ("bi-triage-54", "bi-data-quality-54"),
}

OUTCOME = re.compile(r"outcome\s+(\w+)", re.I)
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def scenarios() -> list[str]:
    return sorted(p.stem for p in (REPO / "scenarios").glob("*.yaml"))


def run(scenario: str, triage: str, dq: str) -> tuple[bool, str, float]:
    env = dict(os.environ)
    env.update(
        {
            "TRIAGE_PROVIDER_MODE": "foundry",
            "TRIAGE_TOOL_MODE": "mock",
            "FOUNDRY_TRIAGE_AGENT_NAME": triage,
            "FOUNDRY_DQ_AGENT_NAME": dq,
            # Keep every run independent so dedup from a previous model's run
            # cannot make the next model look like it suppressed correctly.
            "INCIDENT_TABLE_ENDPOINT": "",
        }
    )
    started = time.time()
    proc = subprocess.run(
        [str(EXE), "run", scenario],
        capture_output=True, text=True, env=env, cwd=str(REPO),
        # The CLI forces UTF-8 output; without this the parent decodes as
        # cp1252 on Windows and dies on the first box-drawing character.
        encoding="utf-8", errors="replace",
    )
    elapsed = time.time() - started
    text = ANSI.sub("", (proc.stdout or "") + (proc.stderr or ""))
    match = OUTCOME.search(text)
    return proc.returncode == 0, (match.group(1) if match else "?"), elapsed


def main() -> int:
    names = scenarios()
    results: dict[str, dict[str, tuple[bool, str, float]]] = {m: {} for m in MODELS}

    for model, (triage, dq) in MODELS.items():
        print(f"\n=== {model} ===", flush=True)
        for name in names:
            subprocess.run([str(EXE), "reset"], capture_output=True, cwd=str(REPO))
            ok, outcome, secs = run(name, triage, dq)
            results[model][name] = (ok, outcome, secs)
            print(f"  {'PASS' if ok else 'FAIL'}  {name:<34} {outcome:<22} {secs:5.1f}s", flush=True)

    print("\n\n" + "=" * 78)
    print(f"{'scenario':<34} {'gpt-5.6-luna':<24} {'gpt-5.4':<24}")
    print("-" * 78)
    for name in names:
        cells = []
        for model in MODELS:
            ok, outcome, secs = results[model][name]
            cells.append(f"{'ok ' if ok else 'FAIL'} {outcome[:14]:<14} {secs:4.0f}s")
        print(f"{name:<34} {cells[0]:<24} {cells[1]:<24}")

    print("-" * 78)
    for model in MODELS:
        passed = sum(1 for r in results[model].values() if r[0])
        total_time = sum(r[2] for r in results[model].values())
        print(f"{model:<20} {passed}/{len(names)} passed   total {total_time:5.0f}s   "
              f"mean {total_time / max(1, len(names)):4.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
