"""Scenario runner — wires everything together for one triage run.

Responsibilities the agent deliberately does NOT have:

* computing the failure signature,
* looking up whether an open incident already exists,
* persisting the terminal outcome.

Keeping those here means the agent is a pure loop that can be tested without a
store, and the dedup/persistence behaviour can be tested without a model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from triage_demo.agents.data_quality_agent import DataQualityAgent
from triage_demo.agents.triage_agent import EventHook, TriageAgent, TriageDeps
from triage_demo.models import BIRequest, Incident, TriageResult
from triage_demo.policy import TriagePolicy
from triage_demo.providers import get_provider
from triage_demo.signature import compute_signature
from triage_demo.store.incidents import IncidentStore, JsonFileIncidentStore
from triage_demo.tools.dataset import DatasetSource
from triage_demo.tools.flags import DataQualityFlagTable
from triage_demo.tools.inbox import GraphInbox, MockInbox
from triage_demo.tools.powerbi import LivePowerBIClient, MockPowerBIClient
from triage_demo.tools.teams import MockTeamsNotifier, WebhookTeamsNotifier

logger = logging.getLogger("triage.runner")


@dataclass
class Expectation:
    """What a scenario asserts. Drives both the tests and the run sheet."""

    outcome: str = ""
    remediation_applied: bool | None = None
    flags_written: int | None = None
    dq_has_issue: bool | None = None
    blocked_attempts: int | None = None


@dataclass
class Scenario:
    name: str
    title: str = ""
    description: str = ""
    email: str = ""
    datasets: list[dict[str, Any]] = field(default_factory=list)
    workspace_id: str = "00000000-0000-0000-0000-000000000000"
    dataset_id: str = "00000000-0000-0000-0000-000000000000"
    refresh_result: str = "Completed"
    refresh_history: list[dict[str, Any]] = field(default_factory=list)
    rogue_second_refresh: bool = False
    rogue_unknown_action: bool = False
    reset_flags: bool = True
    reset_incidents: bool = True
    repeat: int = 1
    expect: Expectation = field(default_factory=Expectation)
    narration: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> Scenario:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        expect = Expectation(**(raw.pop("expect", None) or {}))
        pbi = raw.pop("powerbi", None) or {}
        provider = raw.pop("provider", None) or {}
        return cls(
            expect=expect,
            workspace_id=pbi.get("workspace_id", cls.workspace_id),
            dataset_id=pbi.get("dataset_id", cls.dataset_id),
            refresh_result=pbi.get("refresh_result", "Completed"),
            refresh_history=list(pbi.get("refresh_history") or []),
            rogue_second_refresh=bool(provider.get("rogue_second_refresh", False)),
            rogue_unknown_action=bool(provider.get("rogue_unknown_action", False)),
            **raw,
        )


@dataclass
class RunArtifacts:
    """Everything a caller (CLI or test) needs after a run."""

    result: TriageResult
    incident: Incident | None
    request: BIRequest
    flag_rows_before: int
    flag_rows_after: int
    teams_messages: list[Any] = field(default_factory=list)
    powerbi_calls: list[Any] = field(default_factory=list)


class TriageRunner:
    def __init__(
        self,
        settings,
        *,
        base_dir: Path,
        store: IncidentStore | None = None,
        on_event: EventHook | None = None,
        flag_table_path: Path | None = None,
    ):
        self.settings = settings
        self.base_dir = Path(base_dir)
        self.on_event = on_event
        self.store: IncidentStore = store or JsonFileIncidentStore(
            self.base_dir / "runs" / "incidents.json"
        )
        self.flag_table = DataQualityFlagTable(
            flag_table_path or (self.base_dir / "runs" / "dq_flags.csv")
        )

    # --- inbox -------------------------------------------------------------

    def build_inbox(self):
        if self.settings.triage_tool_mode == "live" and self.settings.graph_tenant_id:
            return GraphInbox(
                tenant_id=self.settings.graph_tenant_id,
                client_id=self.settings.graph_client_id,
                client_secret=self.settings.graph_client_secret,
                mailbox=self.settings.graph_mailbox,
            )
        return MockInbox(directory=self.base_dir / "mock" / "emails")

    # --- clients -----------------------------------------------------------

    def build_powerbi(self, scenario: Scenario | None = None):
        if self.settings.triage_tool_mode == "live" and self.settings.powerbi_tenant_id:
            return LivePowerBIClient(
                tenant_id=self.settings.powerbi_tenant_id,
                client_id=self.settings.powerbi_client_id,
                client_secret=self.settings.powerbi_client_secret,
            )
        return MockPowerBIClient(
            refresh_result=(scenario.refresh_result if scenario else "Completed"),
            history=list(scenario.refresh_history) if scenario else [],
        )

    def build_teams(self):
        if self.settings.triage_tool_mode == "live" and self.settings.teams_webhook_url:
            return WebhookTeamsNotifier(self.settings.teams_webhook_url)
        return MockTeamsNotifier()

    # --- scenarios ---------------------------------------------------------

    def prepare(self, scenario: Scenario) -> None:
        if scenario.reset_flags:
            self.flag_table.reset()
        if scenario.reset_incidents:
            self.store.reset()

    async def run_scenario(self, scenario: Scenario) -> list[RunArtifacts]:
        self.prepare(scenario)
        request = MockInbox.load(self.base_dir / scenario.email)

        datasets = {
            entry["name"]: DatasetSource(
                name=entry["name"],
                path=(self.base_dir / entry["path"]).resolve(),
                key_columns=list(entry.get("key_columns") or []),
            )
            for entry in scenario.datasets
        }

        out: list[RunArtifacts] = []
        for attempt in range(max(1, scenario.repeat)):
            # A repeat must look like a genuinely new email, or the dedup beat
            # is indistinguishable from simple idempotency.
            run_request = request.model_copy(
                update={"request_id": f"{request.request_id}-r{attempt + 1}"}
                if attempt
                else {}
            )
            out.append(await self.run_request(run_request, scenario=scenario, datasets=datasets))
        return out

    async def run_request(
        self,
        request: BIRequest,
        *,
        scenario: Scenario | None = None,
        datasets: dict[str, DatasetSource] | None = None,
    ) -> RunArtifacts:
        signature, _payload = compute_signature(
            source="powerbi_refresh_failure",
            error=request.error_text(),
            artifact_kind="dataset",
            artifact_name=request.report_name or request.dataset_id or "",
        )
        known = self.store.find_open(signature)

        powerbi = self.build_powerbi(scenario)
        teams = self.build_teams()

        triage_provider = get_provider(
            "triage",
            self.settings,
            **(
                {
                    "rogue_second_refresh": scenario.rogue_second_refresh,
                    "rogue_unknown_action": scenario.rogue_unknown_action,
                }
                if scenario and self.settings.triage_provider_mode == "mock"
                else {}
            ),
        )
        dq_agent = DataQualityAgent(get_provider("data_quality", self.settings))

        agent = TriageAgent(
            triage_provider,
            policy=TriagePolicy.from_settings(self.settings),
            dq_agent=dq_agent,
            on_event=self.on_event,
        )

        deps = TriageDeps(
            powerbi=powerbi,
            teams=teams,
            flag_table=self.flag_table,
            datasets=datasets or {},
            workspace_id=(scenario.workspace_id if scenario else "") or "",
            dataset_id=(scenario.dataset_id if scenario else "") or "",
            signature=signature,
            known_incident=known,
        )

        flags_before = self.flag_table.row_count
        try:
            result = await agent.run(request, deps)
        finally:
            await agent.close()

        incident = self.store.record(
            result,
            report_name=request.report_name or "",
            original_error=request.error_text(),
            agent_name=agent.AGENT_NAME,
            prompt_version_hash=agent.prompt_hash,
            model_provider=agent.provider_name,
            model_name=agent.model_name,
            app_version=self.settings.app_version,
        )

        return RunArtifacts(
            result=result,
            incident=incident,
            request=request,
            flag_rows_before=flags_before,
            flag_rows_after=self.flag_table.row_count,
            teams_messages=list(getattr(teams, "messages", [])),
            powerbi_calls=list(getattr(powerbi, "calls", [])),
        )


def check_expectations(scenario: Scenario, artifacts: RunArtifacts) -> list[str]:
    """Return a list of human-readable failures. Empty means the run matched."""
    expect = scenario.expect
    failures: list[str] = []
    result = artifacts.result

    if expect.outcome and result.outcome != expect.outcome:
        failures.append(f"outcome: expected '{expect.outcome}', got '{result.outcome}'")

    if expect.remediation_applied is not None:
        applied = any(a.is_remediation and not a.blocked for a in result.actions)
        if applied != expect.remediation_applied:
            failures.append(
                f"remediation_applied: expected {expect.remediation_applied}, got {applied}"
            )

    if expect.flags_written is not None:
        written = artifacts.flag_rows_after - artifacts.flag_rows_before
        if written != expect.flags_written:
            failures.append(f"flags_written: expected {expect.flags_written}, got {written}")

    if expect.dq_has_issue is not None:
        has_issue = bool(result.dq_finding and result.dq_finding.has_issue)
        if has_issue != expect.dq_has_issue:
            failures.append(f"dq_has_issue: expected {expect.dq_has_issue}, got {has_issue}")

    if expect.blocked_attempts is not None:
        blocked = len(result.blocked_attempts)
        if blocked != expect.blocked_attempts:
            failures.append(
                f"blocked_attempts: expected {expect.blocked_attempts}, got {blocked}"
            )

    return failures


def discover_scenarios(directory: Path) -> list[Scenario]:
    return [Scenario.load(p) for p in sorted(Path(directory).glob("*.yaml"))]
