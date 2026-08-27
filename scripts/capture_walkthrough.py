"""Capture demo runs as SVG for the walkthrough document.

Rich can export a console session to SVG, which beats a screenshot of a
terminal: it stays sharp at any zoom, the text is selectable, and it does not
carry whatever happened to be on the rest of the screen.

Usage:
    .\\.venv\\Scripts\\python.exe scripts\\capture_walkthrough.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from rich.console import Console

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import triage_demo.cli as cli  # noqa: E402
from triage_demo.runner import Scenario, TriageRunner, check_expectations  # noqa: E402
from triage_demo.settings import settings  # noqa: E402

OUT = REPO_ROOT / "walkthrough" / "shots"

RUNS: list[tuple[str, str, bool]] = [
    # (scenario, output stem, keep_incidents)
    ("scenario1-transient", "run-1-transient", False),
    ("scenario2-data-quality", "run-2-data-quality", True),
    ("scenario2b-known-issue", "run-3-suppressed", False),
    ("scenario3-policy-block", "run-4-refused", True),
    ("scenario4-unknown-action", "run-5-allowlist", True),
]


async def capture(scenario_name: str, stem: str, keep: bool) -> str:
    scenario = Scenario.load(REPO_ROOT / "scenarios" / f"{scenario_name}.yaml")

    # record=True makes the console buffer everything for export.
    console = Console(record=True, width=104, force_terminal=True)
    cli.console = console  # the renderers read the module-level console

    runner = TriageRunner(
        settings, base_dir=REPO_ROOT, on_event=cli._make_event_hook(False)
    )
    console.rule(f"[bold]{scenario.title}[/bold]")

    artifacts = await runner.run_scenario(scenario, keep_incidents=keep)
    for idx, art in enumerate(artifacts, start=1):
        if len(artifacts) > 1:
            console.rule(f"Run {idx} of {len(artifacts)}")
        cli._render_result(art)

    failures = check_expectations(scenario, artifacts[-1])
    status = "OK" if not failures else f"FAILED: {failures}"
    console.print(f"\n[bold]{'Expectations met.' if not failures else status}[/bold]")

    OUT.mkdir(parents=True, exist_ok=True)
    console.save_svg(str(OUT / f"{stem}.svg"), title=f"triage-demo run {scenario_name}")
    return status


async def main() -> int:
    print(f"provider={settings.triage_provider_mode}  tools={settings.triage_tool_mode}")
    for scenario_name, stem, keep in RUNS:
        status = await capture(scenario_name, stem, keep)
        print(f"  {stem:22s} {status}")
    print(f"\nSVGs written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
