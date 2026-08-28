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
    ("scenario5-approval-granted", "run-6-approved", True),
    ("scenario6-approval-denied", "run-7-denied", True),
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


async def capture_identity(stem: str = "shot-identity") -> str:
    """Capture the agent identity panels from the live directory.

    Worth capturing as a terminal export rather than a portal screenshot: it
    shows the same facts the portal shows, but read back through the API the
    customer would use, which is harder to stage and easier to believe.
    """
    import argparse

    console = Console(record=True, width=104, force_terminal=True)
    cli.console = console

    args = argparse.Namespace(
        agents=["bi-triage-controller", "bi-triage", "bi-data-quality"], check_scope=True
    )
    try:
        code = cli.cmd_identity(args)
    except Exception as exc:  # pragma: no cover - capture-time only
        console.print(f"[red]identity capture failed: {type(exc).__name__}: {exc}[/red]")
        code = 1

    OUT.mkdir(parents=True, exist_ok=True)
    console.save_svg(str(OUT / f"{stem}.svg"), title="triage-demo identity --check-scope")
    return "OK" if code == 0 else f"exit {code}"


async def capture_hosted(stem: str = "shot-hosted-invoke") -> str:
    """Capture the deployed agent answering, to prove this runs in Azure.

    Shelling out to `azd ai agent invoke` rather than calling the runner
    in-process is the point: the output is the hosted container's, not this
    machine's.
    """
    import re
    import subprocess

    console = Console(record=True, width=104, force_terminal=True)
    alert = (
        "Power BI: Refresh failed for 'Completions Daily Rollup'. "
        "Error code: ModelRefreshFailed_CredentialsNotSpecified. Workspace: BI Triage Demo."
    )
    console.rule("[bold]The deployed agent, invoked in Azure[/bold]")
    console.print(f"[dim]$ azd ai agent invoke bi-triage-controller \"{alert[:60]}...\"[/dim]\n")

    proc = subprocess.run(
        "azd ai agent invoke bi-triage-controller " + subprocess.list2cmdline([alert]),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT), shell=True,
        # azd occasionally waits on something interactive; a capture script must
        # never be the reason a rehearsal stalls.
        timeout=180,
    )
    text = re.sub(r"\x1b\[[0-9;]*m", "", (proc.stdout or "") + (proc.stderr or ""))
    for line in text.splitlines():
        if not line.strip() or "Update available" in line or "winget" in line:
            continue
        style = "bold green" if line.strip().startswith("[bi-triage-controller]") else ""
        console.print(line, style=style, highlight=False)

    OUT.mkdir(parents=True, exist_ok=True)
    console.save_svg(str(OUT / f"{stem}.svg"), title="azd ai agent invoke bi-triage-controller")
    return "OK" if proc.returncode == 0 else f"exit {proc.returncode}"


async def main() -> int:
    print(f"provider={settings.triage_provider_mode}  tools={settings.triage_tool_mode}")
    for scenario_name, stem, keep in RUNS:
        status = await capture(scenario_name, stem, keep)
        print(f"  {stem:22s} {status}")

    print(f"  {'shot-identity':22s} {await capture_identity()}")
    print(f"  {'shot-hosted-invoke':22s} {await capture_hosted()}")
    print(f"\nSVGs written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
