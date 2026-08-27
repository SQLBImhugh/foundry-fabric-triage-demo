"""Tests for the human-approval gate.

A gate that looks like control but is not is worse than no gate, because it
gets trusted. Each test here pins one property that has to hold for an approval
to mean anything.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from test_agents import CannedProvider, _tool_response

from triage_demo.agents.data_quality_agent import DataQualityAgent
from triage_demo.agents.triage_agent import TriageAgent, TriageDeps
from triage_demo.approvals import (
    ApprovalRequest,
    AutoApproveGate,
    AutoDenyGate,
    TeamsCardApprovalGate,
    TimeoutGate,
    action_fingerprint,
)
from triage_demo.policy import (
    APPROVAL_REQUIRED_ACTIONS,
    REMEDIATION_ACTIONS,
    PolicyLedger,
    TriagePolicy,
    requires_approval,
)
from triage_demo.providers.mock import ScriptedDataQualityProvider
from triage_demo.tools.dataset import DatasetSource
from triage_demo.tools.flags import DataQualityFlagTable
from triage_demo.tools.powerbi import MockPowerBIClient
from triage_demo.tools.teams import MockTeamsNotifier

REBIND = "rebind_dataset_gateway"


def make_request(**overrides) -> ApprovalRequest:
    base = {
        "action": REBIND,
        "arguments": {"target_gateway": "gw-onprem-02", "justification": "repeating failure"},
        "justification": "repeating failure",
        "request_id": "req-1",
        "report_name": "Completions Daily Rollup",
    }
    base.update(overrides)
    return ApprovalRequest(**base)


# ---------------------------------------------------------------------------
# 1. An approval is bound to exactly what was approved
# ---------------------------------------------------------------------------


def test_fingerprint_covers_arguments_not_just_the_action() -> None:
    a = action_fingerprint(REBIND, {"target_gateway": "gw-01"})
    b = action_fingerprint(REBIND, {"target_gateway": "gw-02"})
    assert a != b, "approving a rebind to one gateway would authorise any gateway"


def test_fingerprint_is_stable_across_key_order() -> None:
    a = action_fingerprint(REBIND, {"target_gateway": "gw-01", "justification": "x"})
    b = action_fingerprint(REBIND, {"justification": "x", "target_gateway": "gw-01"})
    assert a == b


async def test_approval_for_one_action_does_not_authorise_another() -> None:
    """The attack this prevents: approve something small, execute something big."""
    approved = make_request(arguments={"target_gateway": "gw-harmless"})
    decision = await AutoApproveGate().request_approval(approved)

    substituted = make_request(arguments={"target_gateway": "gw-production-core"})
    valid, why = decision.is_valid_for(substituted)

    assert valid is False
    assert "does not match" in why


# ---------------------------------------------------------------------------
# 2. Approvals expire
# ---------------------------------------------------------------------------


async def test_expired_approval_is_refused() -> None:
    request = make_request(timeout_seconds=60)
    decision = await AutoApproveGate().request_approval(request)

    later = request.requested_at + timedelta(seconds=61)
    valid, why = decision.is_valid_for(request, now=later)

    assert valid is False
    assert "expired" in why


async def test_unexpired_approval_is_accepted() -> None:
    request = make_request(timeout_seconds=600)
    decision = await AutoApproveGate().request_approval(request)
    valid, why = decision.is_valid_for(request)
    assert valid is True, why


# ---------------------------------------------------------------------------
# 3. One approval, one execution
# ---------------------------------------------------------------------------


async def test_an_approval_cannot_be_replayed() -> None:
    gate = AutoApproveGate()
    request = make_request()
    decision = await gate.request_approval(request)

    assert gate.consume(decision) is True
    assert gate.consume(decision) is False, "a retry rode the first human decision"


# ---------------------------------------------------------------------------
# 4. Everything that is not a clear yes is a no
# ---------------------------------------------------------------------------


async def test_silence_is_not_consent() -> None:
    decision = await TimeoutGate().request_approval(make_request())
    assert decision.granted is False
    assert decision.outcome == "timed_out"


async def test_explicit_denial_is_recorded_as_denial_not_timeout() -> None:
    """A person saying no and nobody answering are different facts."""
    decision = await AutoDenyGate(reason="not during close week").request_approval(make_request())
    assert decision.granted is False
    assert decision.outcome == "denied"
    assert "close week" in decision.reason


async def test_a_card_that_cannot_be_delivered_is_not_approved() -> None:
    class BrokenNotifier:
        async def post(self, summary):
            raise ConnectionError("teams unreachable")

    gate = TeamsCardApprovalGate(notifier=BrokenNotifier())
    decision = await gate.request_approval(make_request())

    assert decision.granted is False
    assert decision.outcome == "error"


async def test_no_decision_channel_is_not_approved() -> None:
    gate = TeamsCardApprovalGate(notifier=MockTeamsNotifier(), decision_source=None)
    decision = await gate.request_approval(make_request())
    assert decision.granted is False
    assert decision.outcome == "error"


async def test_an_unrecognised_response_is_not_approved() -> None:
    class OddSource:
        async def poll(self, request_id):
            return {"decision": "maybe", "responder": "someone"}

    gate = TeamsCardApprovalGate(
        notifier=MockTeamsNotifier(), decision_source=OddSource(), poll_seconds=0.01
    )
    decision = await gate.request_approval(make_request(timeout_seconds=2))
    assert decision.granted is False
    assert decision.outcome == "error"


async def test_a_response_with_the_wrong_fingerprint_is_not_approved() -> None:
    class ForgedSource:
        async def poll(self, request_id):
            return {"decision": "approve", "fingerprint": "deadbeefdeadbeef", "responder": "x"}

    gate = TeamsCardApprovalGate(
        notifier=MockTeamsNotifier(), decision_source=ForgedSource(), poll_seconds=0.01
    )
    decision = await gate.request_approval(make_request(timeout_seconds=2))
    assert decision.granted is False
    assert decision.outcome == "error"


async def test_a_well_formed_approval_is_accepted() -> None:
    class GoodSource:
        async def poll(self, request_id):
            return {"decision": "approve", "responder": "ops@contoso.com", "reason": "go ahead"}

    gate = TeamsCardApprovalGate(
        notifier=MockTeamsNotifier(), decision_source=GoodSource(), poll_seconds=0.01
    )
    decision = await gate.request_approval(make_request(timeout_seconds=2))
    assert decision.granted is True
    assert decision.decided_by == "ops@contoso.com"


def test_the_card_states_the_impact_and_the_default() -> None:
    """An approval request that hides the consequence is a rubber stamp."""
    request = make_request(impact="Affects every dataset on the gateway.")
    card = TeamsCardApprovalGate(notifier=MockTeamsNotifier()).build_card(request)
    blob = str(card)

    assert "Affects every dataset on the gateway." in blob
    assert "No response means no action" in blob
    assert request.fingerprint in blob, "the answer must be tied back to the question"


# ---------------------------------------------------------------------------
# 5. Budget accounting
# ---------------------------------------------------------------------------


def test_gated_actions_are_also_remediations() -> None:
    assert APPROVAL_REQUIRED_ACTIONS <= REMEDIATION_ACTIONS
    assert requires_approval(REBIND)
    assert not requires_approval("refresh_powerbi_dataset")


def test_deferring_the_write_does_not_consume_the_remediation_budget() -> None:
    ledger = PolicyLedger(TriagePolicy(max_write_actions=1))
    ledger.charge_tool_call(REBIND, defer_write=True)
    assert ledger.write_actions == 0
    assert ledger.tool_calls == 1


def test_the_write_is_charged_once_approval_lands() -> None:
    ledger = PolicyLedger(TriagePolicy(max_write_actions=1))
    ledger.charge_tool_call(REBIND, defer_write=True)
    ledger.charge_write(REBIND)
    assert ledger.write_actions == 1


# ---------------------------------------------------------------------------
# 6. End to end through the agent
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_datasets(repo_root) -> dict[str, DatasetSource]:
    return {
        "well_production": DatasetSource(
            name="well_production",
            path=repo_root / "mock" / "data" / "well_production_clean.csv",
            key_columns=["well_id", "production_date"],
        )
    }


def deps_with(gate, tmp_path, clean_datasets) -> TriageDeps:
    return TriageDeps(
        powerbi=MockPowerBIClient(latency_ms=0),
        teams=MockTeamsNotifier(),
        flag_table=DataQualityFlagTable(tmp_path / "flags.csv"),
        datasets=clean_datasets,
        signature="sigapproval00001",
        approval_gate=gate,
    )


def agent(provider) -> TriageAgent:
    return TriageAgent(
        provider, dq_agent=DataQualityAgent(ScriptedDataQualityProvider())
    )


def rebind_then_report(outcome: str) -> CannedProvider:
    return CannedProvider(
        responses=[
            _tool_response(
                REBIND,
                {"target_gateway": "gw-onprem-02", "justification": "repeating failure"},
            ),
            _tool_response(
                "report_resolution",
                {"outcome": outcome, "summary": "s", "root_cause": "repeating gateway failure"},
            ),
        ]
    )


async def test_denied_action_never_reaches_the_client(
    sample_request, tmp_path, clean_datasets
) -> None:
    gate = AutoDenyGate(reason="shared with finance")
    deps = deps_with(gate, tmp_path, clean_datasets)

    result = await agent(rebind_then_report("approval_denied")).run(sample_request, deps)

    assert result.outcome == "approval_denied"
    assert not any(c[0] == "rebind_gateway" for c in deps.powerbi.calls), (
        "the un-approved action was dispatched anyway"
    )
    assert result.write_actions == 0, "a denial must not spend the remediation budget"
    assert result.denied_actions == [REBIND]
    assert result.approvals and result.approvals[0].granted is False


async def test_approved_action_executes_and_is_charged(
    sample_request, tmp_path, clean_datasets
) -> None:
    deps = deps_with(AutoApproveGate(approver="ops@contoso.com"), tmp_path, clean_datasets)

    result = await agent(rebind_then_report("resolved")).run(sample_request, deps)

    assert result.outcome == "resolved"
    assert any(c[0] == "rebind_gateway" for c in deps.powerbi.calls)
    assert result.write_actions == 1
    assert result.approvals[0].granted is True
    assert result.approvals[0].decided_by == "ops@contoso.com"


async def test_timeout_behaves_like_a_denial(sample_request, tmp_path, clean_datasets) -> None:
    deps = deps_with(TimeoutGate(), tmp_path, clean_datasets)

    result = await agent(rebind_then_report("approval_denied")).run(sample_request, deps)

    assert result.outcome == "approval_denied"
    assert not any(c[0] == "rebind_gateway" for c in deps.powerbi.calls)
    assert result.approvals[0].outcome == "timed_out"


async def test_no_gate_configured_means_the_action_is_unapprovable(
    sample_request, tmp_path, clean_datasets
) -> None:
    """Not 'approved by default' — the far more dangerous reading."""
    deps = deps_with(None, tmp_path, clean_datasets)

    result = await agent(rebind_then_report("needs_human")).run(sample_request, deps)

    assert not any(c[0] == "rebind_gateway" for c in deps.powerbi.calls)
    assert result.write_actions == 0
    assert result.approvals[0].outcome == "error"


async def test_cannot_claim_a_denial_that_was_never_sought(
    sample_request, tmp_path, clean_datasets
) -> None:
    """Inventing a human's refusal would manufacture authority for inaction."""
    deps = deps_with(AutoApproveGate(), tmp_path, clean_datasets)
    provider = CannedProvider(
        responses=[
            _tool_response(
                "report_resolution",
                {"outcome": "approval_denied", "summary": "s", "root_cause": "r"},
            )
        ]
    )
    result = await agent(provider).run(sample_request, deps)

    assert result.outcome == "needs_human"
    assert "no approval was ever requested" in result.summary


async def test_cannot_claim_success_for_an_unapproved_action(
    sample_request, tmp_path, clean_datasets
) -> None:
    deps = deps_with(AutoDenyGate(), tmp_path, clean_datasets)

    result = await agent(rebind_then_report("resolved")).run(sample_request, deps)

    assert result.outcome == "needs_human"
    assert "not approved" in result.summary


async def test_approval_is_recorded_on_the_incident(repo_root, runner) -> None:
    from triage_demo.runner import Scenario

    scenario = Scenario.load(repo_root / "scenarios" / "scenario6-approval-denied.yaml")
    artifacts = (await runner.run_scenario(scenario))[-1]

    assert artifacts.incident is not None
    assert artifacts.incident.requires_investigation, (
        "a human declining a proposal is the highest-signal event in the system"
    )
    assert artifacts.result.approvals[0].reason
