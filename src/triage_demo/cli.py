"""Command-line surface. This is what the audience actually looks at.

    triage-demo list
    triage-demo run scenario1-transient
    triage-demo run scenario2-data-quality --show-data
    triage-demo flags
    triage-demo incidents
    triage-demo preflight
    triage-demo reset
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table

from triage_demo.observability import configure_telemetry
from triage_demo.runner import (
    RunArtifacts,
    Scenario,
    TriageRunner,
    check_expectations,
    discover_scenarios,
)
from triage_demo.settings import settings
from triage_demo.tools.dataset import render_table

console = Console()

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = REPO_ROOT / "scenarios"

_TOOL_STYLE = {
    "consult_data_quality_agent": "bold magenta",
    "refresh_powerbi_dataset": "bold yellow",
    "rebind_dataset_gateway": "bold red",
    "write_data_quality_flag": "bold cyan",
    "notify_teams": "bold blue",
    "report_resolution": "bold green",
    "get_known_incidents": "bold white",
}

_OUTCOME_STYLE = {
    "resolved": "bold green",
    "flagged_data_quality": "bold cyan",
    "duplicate_suppressed": "bold yellow",
    "approval_denied": "bold yellow",
    "needs_human": "bold yellow",
    "declared_failed": "bold red",
    "agent_crashed": "bold red",
    "policy_blocked": "bold red",
    "timed_out": "bold red",
    "budget_exceeded": "bold red",
    "max_turns_exceeded": "bold red",
}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _make_event_hook(verbose: bool):
    def hook(event: str, payload: dict[str, Any]) -> None:
        if event == "triage_started":
            console.print(
                Panel(
                    f"[dim]request[/dim] {payload.get('request_id')}\n"
                    f"[dim]signature[/dim] {payload.get('signature')}\n"
                    f"[dim]model[/dim] {payload.get('model')}\n"
                    f"[dim]known incident[/dim] "
                    f"{'YES' if payload.get('known_incident') else 'no'}",
                    title="Triage started",
                    border_style="blue",
                )
            )
        elif event == "thinking" and verbose:
            console.print(f"  [dim italic]{payload.get('text','')}[/dim italic]")
        elif event == "tool_started":
            name = payload.get("tool", "")
            style = _TOOL_STYLE.get(name, "white")
            marker = "->" if name != "consult_data_quality_agent" else "=>"
            console.print(f"  {marker} [{style}]{name}[/{style}]")
        elif event == "tool_completed":
            status = payload.get("status", "ok")
            if status == "blocked_by_policy":
                console.print(
                    Panel(
                        f"[bold red]{payload.get('tool')}[/bold red] was not executed.\n\n"
                        f"{payload.get('reason', '')}\n\n"
                        "[dim]The refusal is returned to the agent as data, so it can "
                        "still escalate to a human.[/dim]",
                        title="REFUSED BY CONTROLLER",
                        border_style="red",
                    )
                )
            else:
                style = "red" if status in ("error", "refused") else "dim"
                console.print(f"     [{style}]{status}[/{style}]")
        elif event == "playbooks_matched":
            names = payload.get("names") or []
            warn = payload.get("retry_discouraged")
            console.print(
                Panel(
                    "\n".join(f"  - {n}" for n in names)
                    + (
                        "\n\n[bold yellow]All matched playbooks say a retry will not "
                        "help.[/bold yellow]"
                        if warn
                        else ""
                    ),
                    title=f"Knowledge base — {len(names)} playbook(s) matched",
                    border_style="cyan",
                )
            )
        elif event == "policy_violation":
            console.print(
                Panel(
                    str(payload.get("message", "")),
                    title=f"POLICY: {payload.get('kind')}",
                    border_style="red",
                )
            )
        elif event == "outcome_downgraded":
            console.print(
                Panel(
                    f"Controller downgraded '{payload.get('from')}' -> "
                    f"'{payload.get('to')}' because the evidence did not support the claim.",
                    title="Outcome downgraded",
                    border_style="yellow",
                )
            )
        elif event == "agent_crashed":
            console.print(f"  [bold red]CRASH[/bold red] {payload.get('exception')}")

    return hook


def _render_result(artifacts: RunArtifacts) -> None:
    result = artifacts.result
    style = _OUTCOME_STYLE.get(result.outcome, "white")

    console.print()
    console.print(
        Panel(
            f"[{style}]{result.outcome.upper()}[/{style}]\n\n"
            f"{result.summary}\n\n"
            f"[dim]root cause[/dim] {result.root_cause or 'n/a'}\n"
            f"[dim]action taken[/dim] {result.action_taken or 'none'}",
            title="Outcome",
            border_style=style.split()[-1],
        )
    )

    budget = Table(title="Policy ledger", show_header=True, header_style="bold")
    budget.add_column("Metric")
    budget.add_column("Used", justify="right")
    budget.add_row("LLM turns", str(result.llm_turns))
    budget.add_row("Tool calls dispatched", str(result.tool_calls))
    if result.attempted_actions != result.tool_calls:
        budget.add_row(
            "[red]Actions attempted[/red]", f"[red]{result.attempted_actions}[/red]"
        )
    budget.add_row("Remediation writes", str(result.write_actions))
    budget.add_row("Tokens", f"{result.tokens_used:,}")
    budget.add_row("Wall clock", f"{result.wall_clock_ms} ms")
    if result.blocked_attempts:
        budget.add_row(
            "[red]Blocked attempts[/red]", f"[red]{', '.join(result.blocked_attempts)}[/red]"
        )
    if result.denied_actions:
        budget.add_row(
            "[yellow]Not approved[/yellow]", f"[yellow]{', '.join(result.denied_actions)}[/yellow]"
        )
    console.print(budget)

    for ap in result.approvals:
        style = "green" if ap.granted else "yellow"
        verdict = "APPROVED" if ap.granted else f"NOT APPROVED ({ap.outcome})"
        console.print(
            Panel(
                f"[bold {style}]{verdict}[/bold {style}]\n\n"
                f"[dim]action[/dim]      {ap.action}\n"
                f"[dim]fingerprint[/dim] {ap.fingerprint}\n"
                f"[dim]impact[/dim]      {ap.impact}\n"
                f"[dim]decided by[/dim]  {ap.decided_by or '(nobody)'}\n"
                f"[dim]reason[/dim]      {ap.reason}\n"
                f"[dim]waited[/dim]      {ap.waited_ms} ms",
                title="Human approval",
                border_style=style,
            )
        )

    trail = Table(title="Audit trail", show_header=True, header_style="bold")
    trail.add_column("#", justify="right", width=3)
    trail.add_column("Tool")
    trail.add_column("Result")
    trail.add_column("ms", justify="right")
    for idx, action in enumerate(result.actions, start=1):
        name = f"[red]{action.tool_name}[/red]" if action.blocked else action.tool_name
        trail.add_row(str(idx), name, action.result_summary[:70], str(action.duration_ms))
    console.print(trail)

    if result.dq_finding and result.dq_finding.evidence:
        ev = result.dq_finding.evidence
        has_issue = result.dq_finding.has_issue
        body = ev.headline()
        if ev.sample_keys:
            body += "\n\n[dim]sample keys[/dim]\n" + "\n".join(f"  {k}" for k in ev.sample_keys)
        console.print(
            Panel(
                body,
                title=(
                    f"Data Quality evidence ({result.dq_finding.agent_name})"
                    if has_issue
                    else f"Data Quality check passed ({result.dq_finding.agent_name})"
                ),
                border_style="magenta" if has_issue else "dim",
            )
        )

    for message in artifacts.teams_messages:
        console.print(
            Panel(message.to_markdown(), title="Teams message", border_style="blue")
        )

    if artifacts.incident is not None:
        inc = artifacts.incident
        console.print(
            f"[dim]incident[/dim] {inc.id}  "
            f"[dim]status[/dim] {inc.status}  "
            f"[dim]occurrences[/dim] {inc.occurrence_count}  "
            f"[dim]investigate[/dim] {inc.requires_investigation}"
        )

    delta = artifacts.flag_rows_after - artifacts.flag_rows_before
    if delta:
        console.print(f"[cyan]Data quality flag table: +{delta} row(s)[/cyan]")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    scenarios = discover_scenarios(SCENARIO_DIR)
    table = Table(title="Scenarios", header_style="bold")
    table.add_column("Name")
    table.add_column("Title")
    table.add_column("Expects")
    for sc in scenarios:
        table.add_row(sc.name, sc.title, sc.expect.outcome or "-")
    console.print(table)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    path = _resolve_scenario(args.scenario)
    if path is None:
        console.print(f"[red]No scenario named '{args.scenario}'[/red]")
        return 2

    scenario = Scenario.load(path)
    console.print(Rule(f"[bold]{scenario.title or scenario.name}[/bold]"))
    if scenario.description:
        console.print(f"[dim]{scenario.description}[/dim]\n")

    runner = TriageRunner(
        settings, base_dir=REPO_ROOT, on_event=_make_event_hook(args.verbose)
    )

    if args.show_data and scenario.datasets:
        for entry in scenario.datasets:
            data_path = REPO_ROOT / entry["path"]
            console.print(
                Panel(
                    render_table(data_path),
                    title=f"Source table: {entry['name']} (before)",
                    border_style="dim",
                )
            )

    if args.show_data:
        console.print(
            Panel(
                _render_flags(runner),
                title="Data quality flag table (before)",
                border_style="dim",
            )
        )

    all_artifacts = asyncio.run(runner.run_scenario(scenario, keep_incidents=args.keep_incidents))

    for idx, artifacts in enumerate(all_artifacts, start=1):
        if len(all_artifacts) > 1:
            console.print(Rule(f"Run {idx} of {len(all_artifacts)}"))
        _render_result(artifacts)

    if args.show_data:
        console.print(
            Panel(
                _render_flags(runner),
                title="Data quality flag table (after)",
                border_style="cyan",
            )
        )

    failures = check_expectations(scenario, all_artifacts[-1])
    console.print()
    if failures:
        console.print(
            Panel("\n".join(f"- {f}" for f in failures), title="Expectations FAILED", border_style="red")
        )
        return 1

    console.print("[bold green]Expectations met.[/bold green]")
    return 0


def cmd_flags(args: argparse.Namespace) -> int:
    runner = TriageRunner(settings, base_dir=REPO_ROOT)
    console.print(Panel(_render_flags(runner), title="Data quality flag table"))
    return 0


def cmd_incidents(args: argparse.Namespace) -> int:
    runner = TriageRunner(settings, base_dir=REPO_ROOT)
    incidents = runner.store.list_all()
    if not incidents:
        console.print("[dim]No incidents recorded.[/dim]")
        return 0

    table = Table(title="Incident store", header_style="bold")
    table.add_column("ID")
    table.add_column("Outcome")
    table.add_column("Status")
    table.add_column("Count", justify="right")
    table.add_column("Investigate")
    table.add_column("Root cause")
    for inc in sorted(incidents, key=lambda i: i.last_seen_at, reverse=True):
        table.add_row(
            inc.id,
            inc.outcome,
            inc.status,
            str(inc.occurrence_count),
            "[red]yes[/red]" if inc.requires_investigation else "no",
            (inc.diagnosed_root_cause or "")[:50],
        )
    console.print(table)
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    from triage_demo.observability import otel_available

    table = Table(title="Preflight", header_style="bold")
    table.add_column("Check")
    table.add_column("Value")
    table.add_column("Status")

    def row(name: str, value: str, ok: bool, optional: bool = False) -> None:
        mark = "[green]ok[/green]" if ok else ("[yellow]optional[/yellow]" if optional else "[red]missing[/red]")
        table.add_row(name, value or "(unset)", mark)

    row("Provider mode", settings.triage_provider_mode, True)
    row("Tool mode", settings.triage_tool_mode, True)
    row("Scenarios", str(len(discover_scenarios(SCENARIO_DIR))), True)
    row(
        "Foundry endpoint",
        settings.foundry_project_endpoint,
        bool(settings.foundry_project_endpoint),
        optional=settings.triage_provider_mode != "foundry",
    )
    row(
        "Azure OpenAI endpoint",
        settings.azure_openai_endpoint,
        bool(settings.azure_openai_endpoint),
        optional=settings.triage_provider_mode != "direct",
    )
    live = settings.triage_tool_mode == "live"
    row("Graph tenant", settings.graph_tenant_id, bool(settings.graph_tenant_id), optional=not live)
    row("Power BI workspace", settings.powerbi_workspace_id, bool(settings.powerbi_workspace_id), optional=not live)
    row("Teams webhook", "set" if settings.teams_webhook_url else "", bool(settings.teams_webhook_url), optional=not live)
    row(
        "App Insights",
        "set" if settings.applicationinsights_connection_string else "",
        bool(settings.applicationinsights_connection_string),
        optional=True,
    )
    row("OpenTelemetry SDK", "installed" if otel_available() else "not installed", otel_available(), optional=True)

    console.print(table)
    console.print(
        "\n[dim]Everything marked optional is only needed for the mode you intend to run.[/dim]"
    )
    return 0


async def _watch_loop(
    runner: TriageRunner,
    inbox,
    *,
    once: bool,
    limit: int,
    interval: int,
    live: bool,
) -> int:
    """Poll a mailbox and triage each new message.

    Deliberately does **not** mark messages as read. A demo that mutates the
    customer's mailbox is a demo you can only give once, and read state is the
    operator's signal, not ours. Dedup is by Graph message id, held by the inbox.
    """
    triaged = 0
    while True:
        try:
            requests = await inbox.fetch(limit=limit)
        except Exception as exc:
            console.print(f"[red]Fetch failed:[/red] {type(exc).__name__}: {exc}")
            if once:
                return 1
            await asyncio.sleep(interval)
            continue

        if not requests:
            if once:
                console.print("[dim]No new mail.[/dim]")
                return 0
            await asyncio.sleep(interval)
            continue

        for request in requests:
            console.print(
                Rule(f"[bold]{request.subject or '(no subject)'}[/bold] "
                     f"[dim]from {request.sender}[/dim]")
            )
            artifacts = await runner.run_request(request)
            _render_result(artifacts)
            triaged += 1

        if once:
            console.print(f"\n[bold green]Triaged {triaged} message(s).[/bold green]")
            return 0
        if live:
            console.print(f"[dim]Waiting {interval}s for new mail...[/dim]")
        await asyncio.sleep(interval)


def cmd_watch(args: argparse.Namespace) -> int:
    runner = TriageRunner(
        settings, base_dir=REPO_ROOT, on_event=_make_event_hook(args.verbose)
    )
    inbox = runner.build_inbox()
    live = type(inbox).__name__ == "GraphInbox"

    if live:
        checks = asyncio.run(_verify_inbox(inbox))
        auth, scope = checks["auth"], checks["scope"]

        if not auth.get("ok"):
            console.print("[red]Graph authentication failed.[/red] Run `triage-demo preflight`.")
            return 2
        if auth.get("has_upn"):
            console.print(
                "[red]Refusing to run:[/red] the token carries a 'upn' claim, so this is a "
                "delegated user token, not the app-only identity this agent is designed for."
            )
            return 2

        console.print(
            f"[green]Graph app-only auth OK[/green] "
            f"[dim]roles={', '.join(auth.get('roles') or []) or 'none'}[/dim]"
        )

        # Fail closed. App-only Mail.Read is tenant-wide unless an Exchange
        # ApplicationAccessPolicy scopes it, and we proved that the hard way:
        # a demo app read the global administrator's mailbox. If the scope
        # cannot be demonstrated, do not ingest.
        if scope.get("checked") and scope.get("scoped"):
            console.print(
                f"[green]Mailbox scope enforced[/green] "
                f"[dim]control read of {scope['canary']} denied (403)[/dim]"
            )
        elif scope.get("checked") and not scope.get("scoped"):
            console.print(
                Panel(
                    str(scope.get("reason", "")),
                    title="[red]Refusing to run: app is not scoped[/red]",
                    border_style="red",
                )
            )
            return 2
        elif args.require_scope_check:
            console.print(
                f"[red]Refusing to run:[/red] mailbox scope could not be proven "
                f"({scope.get('reason', 'no canary mailbox configured')}). "
                "Set GRAPH_CANARY_MAILBOX, or pass --no-require-scope-check to override."
            )
            return 2
        else:
            console.print(
                f"[yellow]Mailbox scope unverified[/yellow] "
                f"[dim]({scope.get('reason', 'no canary mailbox configured')})[/dim]"
            )

        console.print(f"[dim]Watching {settings.graph_mailbox}[/dim]\n")
    else:
        console.print("[yellow]Mock inbox[/yellow] [dim](set TRIAGE_TOOL_MODE=live for Graph)[/dim]\n")

    return asyncio.run(
        _watch_loop(
            runner,
            inbox,
            once=args.once,
            limit=args.limit,
            interval=args.interval,
            live=live,
        )
    )


async def _verify_inbox(inbox) -> dict[str, Any]:
    auth = await inbox.verify()
    scope: dict[str, Any] = {"checked": False, "reason": "no canary mailbox configured"}
    if auth.get("ok") and settings.graph_canary_mailbox:
        scope = await inbox.verify_scope(settings.graph_canary_mailbox)
    return {"auth": auth, "scope": scope}


def _operator_graph_token() -> str:
    """Get a directory-read token for the *operator* running the CLI.

    DefaultAzureCredential is the right choice here and the wrong choice for
    the agent. This command is a human inspecting the directory, so falling
    back to their `az login` is exactly what should happen. The agent's own
    Graph access deliberately does not use this chain -- see tools/inbox.py.
    """
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential().get_token("https://graph.microsoft.com/.default").token


def _identity_panel(report) -> Panel:
    from rich.table import Table as _Table

    t = _Table(show_header=False, box=None, padding=(0, 1))
    t.add_column(style="dim", no_wrap=True)
    t.add_column()

    t.add_row("identity", report.display_name)
    t.add_row("object id", report.object_id)

    # Agent identities are the only service principals where these are equal.
    # It is a cheap, verifiable tell that this is not an ordinary app.
    if report.identity_matches_app:
        t.add_row("app id", f"{report.app_id} [green](same as object id)[/green]")
    else:
        t.add_row("app id", report.app_id)

    t.add_row("enabled", "yes" if report.enabled else "[red]no[/red]")

    if report.blueprint_name:
        t.add_row("blueprint", report.blueprint_name)

    secret_state = (
        f"[bold green]none[/bold green] "
        f"[dim](keys={report.key_credentials}, passwords={report.password_credentials})[/dim]"
        if report.is_secretless
        else f"[red]keys={report.key_credentials}, passwords={report.password_credentials}[/red]"
    )
    t.add_row("stored secrets", secret_state)

    if report.secret_expires_at:
        # The whole argument for agent identities, in one row.
        t.add_row("secret expires", f"[yellow]{report.secret_expires_at}[/yellow]")

    if report.federated_credentials:
        t.add_row("authenticates via", ", ".join(report.federated_credentials) + " [dim](federated)[/dim]")

    t.add_row(
        "sponsor",
        ", ".join(report.sponsors) if report.sponsors else "[yellow]none recorded[/yellow]",
    )
    t.add_row(
        "permissions",
        "\n".join(report.graph_app_roles) if report.graph_app_roles else "[dim]none[/dim]",
    )

    proof = report.scope_proof
    if proof is not None:
        lines = [f"[green]granted[/green]  {m}" for m in proof.granted]
        lines += [f"[red]denied[/red]   {m}" for m in proof.denied]
        t.add_row("mailbox scope", "\n".join(lines) if lines else "[dim]not checked[/dim]")

    border = "green" if report.is_secretless else "yellow"
    label = "Agent identity" if report.is_agent_identity else "App registration"
    return Panel(t, title=f"{label}: [bold]{report.short_name}[/bold]", border_style=border)


def cmd_identity(args: argparse.Namespace) -> int:
    from triage_demo.identity import (
        HttpGraphReader,
        MailboxScopeProof,
        load_agent_identity,
        load_app_registration,
        project_name_prefix,
    )

    try:
        token = _operator_graph_token()
    except Exception as exc:
        console.print(
            f"[red]Could not authenticate to Microsoft Graph[/red] ({type(exc).__name__}). "
            "Run `az login` first."
        )
        return 2

    reader = HttpGraphReader(token)
    prefix = project_name_prefix(settings.foundry_project_endpoint)

    proof: MailboxScopeProof | None = None
    if args.check_scope and settings.graph_mailbox and settings.graph_canary_mailbox:
        proof = MailboxScopeProof(
            granted=[settings.graph_mailbox],
            denied=[settings.graph_canary_mailbox],
        )

    console.print(
        Rule("[bold]Agent identities[/bold] [dim]-- who these agents are, and who is accountable[/dim]")
    )
    if prefix:
        console.print(f"[dim]project: {prefix}[/dim]\n")

    found = 0
    for needle in args.agents:
        try:
            report = load_agent_identity(
                reader,
                display_name_contains=needle,
                name_prefix=prefix,
                scope_proof=proof,
            )
        except Exception as exc:
            console.print(f"[red]Lookup failed for '{needle}':[/red] {type(exc).__name__}: {exc}")
            continue
        if report is None:
            console.print(f"[yellow]No agent identity matching '{needle}'.[/yellow]")
            continue
        console.print(_identity_panel(report))
        found += 1

    if not found:
        console.print(
            "\n[dim]No agent identities found. Foundry creates one per agent; if this "
            "tenant has none, the agents may predate the feature.[/dim]"
        )
        return 1

    # The contrast panel. Mailbox ingestion cannot use an agent identity --
    # Exchange rejects them for app-only mail access -- so one conventional app
    # registration remains. Showing it beside the others makes the difference
    # concrete: it is the only thing here holding a secret, and the only thing
    # with an expiry date.
    if settings.graph_client_id:
        try:
            app_report = load_app_registration(
                reader, app_id=settings.graph_client_id, scope_proof=proof
            )
        except Exception as exc:
            console.print(f"[dim]Could not read the app registration ({type(exc).__name__}).[/dim]")
            app_report = None
        if app_report is not None:
            console.print(_identity_panel(app_report))
            console.print(
                "[dim]Mailbox ingestion still needs this one. Verified from inside the "
                "container: Graph's directory endpoint accepted the agent identity (200) "
                "while the Exchange-backed mail endpoint refused the same token (401).[/dim]"
            )

    console.print(
        "\n[dim]Each agent authenticates as itself, with its own permissions and its own "
        "audit trail. Every identity above except the app registration has no secret to "
        "steal, rotate, or let expire.[/dim]"
    )
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    runner = TriageRunner(settings, base_dir=REPO_ROOT)
    runner.flag_table.reset()
    runner.store.reset()
    console.print("[green]Flag table and incident store cleared.[/green]")
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    """Print the tool schemas — useful when the audience asks what the agent can do."""
    import json

    from triage_demo.tools.registry import DQ_TOOLS, TRIAGE_TOOLS

    payload = {"triage_agent": TRIAGE_TOOLS, "data_quality_agent": DQ_TOOLS}
    console.print(Syntax(json.dumps(payload, indent=2), "json", theme="ansi_dark"))
    return 0


# ---------------------------------------------------------------------------


def _render_flags(runner: TriageRunner) -> str:
    rows = runner.flag_table.read_all()
    if not rows:
        return "(empty - 0 rows)"
    return render_table(runner.flag_table.path)


def _resolve_scenario(name: str) -> Path | None:
    candidates = [
        SCENARIO_DIR / name,
        SCENARIO_DIR / f"{name}.yaml",
        SCENARIO_DIR / f"{name.replace('_', '-')}.yaml",
        Path(name),
    ]
    for path in candidates:
        if path.exists() and path.is_file():
            return path
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="triage-demo", description=__doc__)
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List available scenarios").set_defaults(func=cmd_list)

    run = sub.add_parser("run", help="Run a scenario end to end")
    run.add_argument("scenario")
    run.add_argument("--verbose", "-v", action="store_true", help="Show agent reasoning")
    run.add_argument("--show-data", action="store_true", help="Show source + flag tables")
    run.add_argument(
        "--keep-incidents",
        action="store_true",
        help="Do not clear the incident store first (keeps prior runs' evidence visible)",
    )
    run.set_defaults(func=cmd_run)

    watch = sub.add_parser("watch", help="Poll a mailbox and triage each new message")
    watch.add_argument("--once", action="store_true", help="Drain once and exit")
    watch.add_argument("--limit", type=int, default=10, help="Max messages per poll")
    watch.add_argument("--interval", type=int, default=30, help="Seconds between polls")
    watch.add_argument("--verbose", "-v", action="store_true", help="Show agent reasoning")
    watch.add_argument(
        "--no-require-scope-check",
        dest="require_scope_check",
        action="store_false",
        help="Allow live ingestion without proving the app is mailbox-scoped (not recommended)",
    )
    watch.set_defaults(func=cmd_watch, require_scope_check=True)

    identity = sub.add_parser(
        "identity", help="Show the Entra agent identity behind each agent"
    )
    identity.add_argument(
        "agents",
        nargs="*",
        default=["bi-triage-controller", "bi-triage", "bi-data-quality"],
        help="Substrings matching agent identity display names",
    )
    identity.add_argument(
        "--check-scope",
        action="store_true",
        help="Include the mailbox scope the agent is confined to",
    )
    identity.set_defaults(func=cmd_identity)

    sub.add_parser("flags", help="Show the data quality flag table").set_defaults(func=cmd_flags)
    sub.add_parser("incidents", help="Show the incident store").set_defaults(func=cmd_incidents)
    sub.add_parser("preflight", help="Verify configuration").set_defaults(func=cmd_preflight)
    sub.add_parser("reset", help="Clear flags and incidents").set_defaults(func=cmd_reset)
    sub.add_parser("tools", help="Print agent tool schemas").set_defaults(func=cmd_tools)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252, which mangles any non-ASCII character
    # in a Teams message or an error string. A demo is a bad place to discover
    # that, so force UTF-8 on the way out.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.ERROR,
        format="%(levelname)s %(name)s %(message)s",
    )

    # Without this call the connection string is read and never used, so
    # "tracing is wired up" would be true of the code and false of the process.
    if settings.applicationinsights_connection_string:
        configure_telemetry(settings.applicationinsights_connection_string)

    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
