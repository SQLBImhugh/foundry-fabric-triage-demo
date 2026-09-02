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
from rich.markup import escape

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
    ("scenario7-schedule-reenable", "run-8-schedule", True),
    ("scenario8-capacity-backoff", "run-9-backoff", True),
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

    # Never overwrite a good asset with a failed capture. This one reads live
    # directory data, so it fails whenever the operator's token has expired --
    # and silently replacing a working panel with a blank one is exactly the
    # kind of thing nobody notices until the document is in front of a customer.
    if code != 0:
        return f"exit {code} (kept the existing capture)"

    OUT.mkdir(parents=True, exist_ok=True)
    console.save_svg(str(OUT / f"{stem}.svg"), title="triage-demo identity --check-scope")
    return "OK"


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


def capture_teams_sync(stem: str = "shot-teams-card") -> str:
    """Capture the Teams card a run produces.

    Rendered from the payload the agent actually generates, so it cannot drift
    from what would really be posted. It is explicitly *not* a screenshot of
    Teams: delivery needs a Power Automate Workflows webhook, which is a manual
    setup step, and pretending otherwise would be the one dishonest asset in an
    otherwise verified document.

    Synchronous because the CLI command runs its own event loop.
    """
    import argparse

    console = Console(record=True, width=104, force_terminal=True)
    cli.console = console

    args = argparse.Namespace(scenario="scenario1-transient", json=False)
    try:
        code = cli.cmd_teams_preview(args)
    except Exception as exc:  # pragma: no cover - capture-time only
        console.print(f"[red]teams capture failed: {type(exc).__name__}: {exc}[/red]")
        code = 1

    if code != 0:
        return f"exit {code} (kept the existing capture)"

    OUT.mkdir(parents=True, exist_ok=True)
    console.save_svg(str(OUT / f"{stem}.svg"), title="triage-demo teams-preview")
    return "OK"


def capture_detector_sync(stem: str = "shot-silent-sweep") -> str:
    """Capture the silent-failure detector's full lifecycle.

    Scripted against the mock health client rather than a live model, for the
    same reason the scenario runs are: the walkthrough has to be reproducible
    on a laptop with no tenant. The sequence shown is the one that was verified
    live against a real Fabric semantic model on 2026-09-01 -- healthy, a
    column removed, suspect, confirmed, and quiet again once restored.

    Each sweep is a separate call against one shared store, because the point
    being demonstrated is that the detector remembers between runs. Collapsing
    them into a single call would show the output without the property.
    """
    import asyncio as _asyncio

    from triage_demo.detectors.silent_failures import HealthProbe, SilentFailureScanner
    from triage_demo.store.semantic_health import InMemorySemanticHealthStore
    from triage_demo.tools.semantic_health import MockSemanticHealthClient

    console = Console(record=True, width=104, force_terminal=True)
    cli.console = console

    # The shape of the model this was proved against: a star schema whose fact
    # table carries a date key, so the watermark is read through the
    # relationship rather than off the fact.
    schema = (
        "column:dim_date[date]",
        "column:dim_date[date_key]",
        "column:fact_sales_invoice[invoice_date_key]",
        "column:fact_sales_invoice[invoice_number]",
        "column:fact_sales_invoice[net_amount]",
        "column:fact_sales_invoice[source_system]",
    )
    dropped = tuple(e for e in schema if "source_system" not in e)

    store = InMemorySemanticHealthStore()
    probe = HealthProbe(
        name="businesscentral-invoices",
        workspace_id="ws",
        dataset_id="ds",
        table="fact_sales_invoice",
        date_table="dim_date",
        date_column="date",
        report_name="businesscentral_invoices_import",
        watch_schema=True,
        expected_lag_hours=100_000,
    )

    async def sweep(client: MockSemanticHealthClient) -> object:
        scanner = SilentFailureScanner(client, store)
        return (await scanner.sweep([probe]))[0]

    def show(caption: str, finding) -> None:
        colour = {
            "healthy": "green", "suspect": "yellow",
            "confirmed": "red", "detector_fault": "magenta", "parked": "magenta",
        }.get(finding.status, "white")
        console.print(f"\n[bold]{caption}[/bold]")
        console.print(f"  status  [{colour}]{finding.status.upper()}[/{colour}]")
        if finding.kind:
            console.print(f"  kind    {finding.kind}")
        if finding.detail:
            # The detail names the object that vanished, as DAX --
            # fact_sales_invoice[source_system]. Rich reads the brackets as a
            # style tag and drops the column, leaving a capture that says
            # something is missing without saying what.
            console.print(f"  [dim]{escape(finding.detail)}[/dim]")
        if finding.status == "healthy":
            console.print("  [dim]Nothing announced. A detector that reports "
                          "'still fine' every sweep is one people filter.[/dim]")

    console.rule("[bold]Failures that never send an alert[/bold]")
    console.print(
        "The refresh reported success every time. Nothing below arrives by email --\n"
        "the detector goes and measures the model instead."
    )

    healthy = MockSemanticHealthClient(max_date="2024-12-23", row_count=400, schema=schema)
    show("Sweep 1 - baseline recorded", _asyncio.run(sweep(healthy)))

    broken = MockSemanticHealthClient(max_date="2024-12-23", row_count=400, schema=dropped)
    show("Sweep 2 - a column has gone", _asyncio.run(sweep(broken)))
    show("Sweep 3 - seen twice, now it counts", _asyncio.run(sweep(broken)))

    restored = MockSemanticHealthClient(max_date="2024-12-23", row_count=400, schema=schema)
    show("Sweep 4 - column restored", _asyncio.run(sweep(restored)))

    console.print(
        "\n[dim]Verified against a real Fabric semantic model on 2026-09-01: the same\n"
        "four steps, and the incident recorded two occurrences against one\n"
        "notification.[/dim]"
    )

    OUT.mkdir(parents=True, exist_ok=True)
    console.save_svg(str(OUT / f"{stem}.svg"), title="triage-demo health")
    return "OK"


def capture_probes_sync(stem: str = "shot-probe-preflight") -> str:
    """Capture what is watched, and the check that it can watch anything.

    ``--preflight`` earns its place in the document because the failure it
    catches is invisible: a probe with no ids, or a duplicate name on one
    model, produces configuration that looks like monitoring and reports
    nothing at all, indefinitely.
    """
    import argparse
    import json

    from triage_demo.settings import Settings

    console = Console(record=True, width=104, force_terminal=True)
    cli.console = console
    original = cli.settings

    good = [{
        "name": "businesscentral-invoices",
        "workspace_id": "800e3585-...", "dataset_id": "179fb3b4-...",
        "table": "fact_sales_invoice", "date_table": "dim_date", "date_column": "date",
        "report_name": "businesscentral_invoices_import",
        "watch_schema": True, "load_weekdays": [1, 2, 3, 4, 5],
    }]
    broken = good + [{"name": "unfinished", "workspace_id": "", "dataset_id": "", "table": ""}]

    try:
        console.rule("[bold]What is being watched[/bold]")
        cli.settings = Settings(silent_health_probes=json.dumps(good), triage_tool_mode="mock")
        cli.cmd_health(argparse.Namespace(
            baselines=False, probes=True, accept=None, preflight=False))

        console.rule("[bold]Checking it can watch anything at all[/bold]")
        cli.settings = Settings(silent_health_probes=json.dumps(broken), triage_tool_mode="mock")
        code = cli.cmd_health(argparse.Namespace(
            baselines=False, probes=False, accept=None, preflight=True))
        console.print(
            f"\n[bold]exit {code}[/bold] [dim]-- a configuration that cannot detect "
            "anything fails the check rather than running silently.[/dim]"
        )
    except Exception as exc:  # pragma: no cover - capture-time only
        console.print(f"[red]probe capture failed: {type(exc).__name__}: {exc}[/red]")
        return "failed (kept the existing capture)"
    finally:
        cli.settings = original

    OUT.mkdir(parents=True, exist_ok=True)
    console.save_svg(str(OUT / f"{stem}.svg"), title="triage-demo health --probes / --preflight")
    return "OK"


async def main() -> int:
    print(f"provider={settings.triage_provider_mode}  tools={settings.triage_tool_mode}")
    for scenario_name, stem, keep in RUNS:
        status = await capture(scenario_name, stem, keep)
        print(f"  {stem:22s} {status}")

    print(f"  {'shot-silent-sweep':22s} {await asyncio.to_thread(capture_detector_sync)}")
    print(f"  {'shot-probe-preflight':22s} {await asyncio.to_thread(capture_probes_sync)}")
    print(f"  {'shot-identity':22s} {await capture_identity()}")
    print(f"  {'shot-teams-card':22s} {await asyncio.to_thread(capture_teams_sync)}")
    print(f"  {'shot-hosted-invoke':22s} {await capture_hosted()}")
    print(f"\nSVGs written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
