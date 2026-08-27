"""Tool schemas and the dispatcher that enforces policy.

The dispatcher is where the safety story lives. Every call is charged against
the :class:`~triage_demo.policy.PolicyLedger` *before* it executes.

One deliberate asymmetry in how violations are handled:

* ``policy_blocked`` is returned to the model **as a tool result**. The agent
  sees "refused, and here's why", and can still notify a human. Killing the
  run at that moment would leave the operator with silence.
* ``timed_out`` / ``budget_exceeded`` / ``max_turns_exceeded`` propagate and
  end the run. Those mean the agent has consumed its allowance; letting it
  keep talking is how you get a runaway.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from triage_demo.approvals import ApprovalGate, ApprovalRequest
from triage_demo.models import ApprovalRecord, BIRequest, DataQualityFinding, TriageAction
from triage_demo.observability import tool_span
from triage_demo.policy import (
    PolicyLedger,
    PolicyViolation,
    is_remediation,
    requires_approval,
)
from triage_demo.tools.dataset import DatasetSource
from triage_demo.tools.flags import DataQualityFlagTable, build_flag
from triage_demo.tools.powerbi import PowerBIClient
from triage_demo.tools.teams import ResolutionSummary, TeamsNotifier

logger = logging.getLogger("triage.tools")


# ---------------------------------------------------------------------------
# Function-calling schemas
# ---------------------------------------------------------------------------

TRIAGE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_request_context",
            "description": (
                "Return the parsed BI request: subject, body, and any report / "
                "dataset / workspace / error-code hints extracted deterministically."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_known_incidents",
            "description": (
                "Check whether this failure signature has been seen before and is "
                "still open. Call this BEFORE proposing any remediation. If an open "
                "incident exists, the correct action is to wait, not to act again."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dataset_refresh_history",
            "description": (
                "Return the last N refresh attempts for the dataset. Use this to "
                "distinguish a one-off transient failure from a repeating one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "top": {
                        "type": "integer",
                        "description": "How many history entries to return (1-20).",
                        "default": 5,
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consult_data_quality_agent",
            "description": (
                "Hand off to the Data Quality agent, a separate agent that inspects "
                "the underlying dataset for duplicate records and returns a "
                "structured finding. This is the first triage decision: a data "
                "quality issue is never resolved by refreshing a report."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why this consultation is needed.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refresh_powerbi_dataset",
            "description": (
                "REMEDIATION. Trigger a Power BI dataset refresh via the REST API. "
                "Only valid for a Tier 1 transient failure with no outstanding data "
                "quality issue. The controller permits exactly one remediation per run."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "justification": {
                        "type": "string",
                        "description": "One sentence on why a refresh resolves this.",
                    }
                },
                "required": ["justification"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rebind_dataset_gateway",
            "description": (
                "REMEDIATION REQUIRING HUMAN APPROVAL. Rebind the dataset to a "
                "different data gateway. Use only when refresh history shows the "
                "SAME failure repeating, so another refresh will not help. This "
                "affects every dataset bound to the gateway, not just this one, "
                "so a human must authorise it before it runs. Propose it, explain "
                "why, and accept the answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_gateway": {
                        "type": "string",
                        "description": "Name or id of the gateway to bind to.",
                    },
                    "justification": {
                        "type": "string",
                        "description": (
                            "Why a rebind is the right call, citing the refresh history."
                        ),
                    },
                },
                "required": ["target_gateway", "justification"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_data_quality_flag",
            "description": (
                "Record a data quality issue in the flag table. Flags the affected "
                "table and the nature of the issue. Does NOT fix the data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "detail": {
                        "type": "string",
                        "description": "Human-readable description of the issue.",
                    }
                },
                "required": ["detail"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notify_teams",
            "description": "Post a summary to the Teams channel.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "outcome": {"type": "string"},
                    "action_taken": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["title", "outcome"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_resolution",
            "description": (
                "REQUIRED FINAL CALL. Declare the terminal outcome of this triage "
                "run. Call this exactly once, after any action and notification."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "outcome": {
                        "type": "string",
                        "enum": [
                            "resolved",
                            "flagged_data_quality",
                            "duplicate_suppressed",
                            "approval_denied",
                            "needs_human",
                            "declared_failed",
                        ],
                    },
                    "tier": {"type": "string", "enum": ["tier_1", "tier_2", "needs_human"]},
                    "category": {
                        "type": "string",
                        "enum": ["transient", "data_quality", "config", "app", "user"],
                    },
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "root_cause": {"type": "string"},
                    "summary": {"type": "string"},
                    "reasoning": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["outcome", "summary", "root_cause"],
            },
        },
    },
]

DQ_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "check_duplicates",
            "description": (
                "Deterministically scan a registered table for rows sharing a "
                "composite key. Returns counts and sample key values only - never "
                "row contents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "table": {
                        "type": "string",
                        "description": "Registered table name to inspect.",
                    }
                },
                "required": ["table"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Execution context
# ---------------------------------------------------------------------------


@dataclass
class ToolContext:
    """Everything the tools need. Constructed once per run by the runner."""

    request: BIRequest
    ledger: PolicyLedger
    powerbi: PowerBIClient
    teams: TeamsNotifier
    flag_table: DataQualityFlagTable
    datasets: dict[str, DatasetSource] = field(default_factory=dict)
    known_incident: Any = None  # Incident | None, set by the orchestrator
    signature: str = ""

    # Filled in as the run proceeds so the orchestrator can build its result.
    dq_finding: DataQualityFinding | None = None
    remediation_outcome: Any = None
    resolution: dict[str, Any] | None = None
    notifications: list[ResolutionSummary] = field(default_factory=list)
    flag_written: bool = False
    notification_attempted: bool = False
    notification_delivered: bool = False

    workspace_id: str = ""
    dataset_id: str = ""
    approval_gate: ApprovalGate | None = None
    approvals: list[ApprovalRecord] = field(default_factory=list)
    gateway_rebound_to: str = ""

    def default_dataset(self) -> DatasetSource | None:
        return next(iter(self.datasets.values()), None)


class ToolDispatcher:
    """Executes one tool call, charging policy first."""

    def __init__(self, ctx: ToolContext, *, dq_agent=None):
        self.ctx = ctx
        self._dq_agent = dq_agent
        self.actions: list[TriageAction] = []

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        gated = requires_approval(name)

        try:
            # Gated actions defer the remediation charge until a human has
            # actually said yes. A denial must not spend the run's one
            # remediation, or the next legitimate fix gets refused for a reason
            # nobody can see.
            self.ctx.ledger.charge_tool_call(name, defer_write=gated)
        except PolicyViolation as violation:
            if violation.kind == "policy_blocked":
                # Surface the refusal to the model instead of ending the run.
                self._record(
                    name, arguments, f"BLOCKED: {violation.message}", started, blocked=True
                )
                logger.warning("Policy blocked '%s': %s", name, violation.message)
                return {
                    "status": "blocked_by_policy",
                    "reason": violation.message,
                    "guidance": (
                        "You may not perform this action. Report the situation to a "
                        "human via notify_teams, then call report_resolution with "
                        "outcome 'needs_human'."
                    ),
                }
            raise

        if gated:
            allowed, approval_result = await self._seek_approval(name, arguments)
            if not allowed:
                self._record(
                    name,
                    arguments,
                    f"NOT APPROVED: {approval_result.get('reason', '')}",
                    started,
                    blocked=True,
                )
                return approval_result
            try:
                self.ctx.ledger.charge_write(name)
            except PolicyViolation as violation:
                self._record(name, arguments, f"BLOCKED: {violation.message}", started, blocked=True)
                return {
                    "status": "blocked_by_policy",
                    "reason": violation.message,
                    "guidance": (
                        "Approval was granted but the remediation budget is spent. "
                        "Notify a human and report 'needs_human'."
                    ),
                }

        with tool_span(name):
            try:
                result = await self._execute(name, arguments)
            except PolicyViolation:
                raise
            except Exception as exc:  # noqa: BLE001 - tool errors are data
                logger.exception("Tool '%s' raised", name)
                result = {"status": "error", "error_type": type(exc).__name__, "error": str(exc)[:500]}

        self._record(name, arguments, _summarize(result), started)
        return result

    # --- human approval ----------------------------------------------------

    async def _seek_approval(
        self, name: str, arguments: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """Ask a human. Return (allowed, result_to_return_if_not_allowed).

        Every path that is not an explicit, matching, unexpired, unused "yes"
        returns False. That includes the absence of a gate entirely: an
        approval-required action with nowhere to send the request is not
        approved, it is unapprovable.
        """
        ctx = self.ctx
        gate = ctx.approval_gate
        started = time.monotonic()

        request = ApprovalRequest(
            action=name,
            arguments=dict(arguments or {}),
            justification=str(arguments.get("justification", "")),
            request_id=f"{ctx.request.request_id}:{name}",
            report_name=ctx.request.report_name or "",
            signature=ctx.signature,
            impact=_impact_of(name, arguments),
        )

        if gate is None:
            logger.warning("No approval gate configured; refusing '%s'", name)
            record = ApprovalRecord(
                action=name,
                fingerprint=request.fingerprint,
                requested_at=request.requested_at.isoformat(),
                justification=request.justification,
                impact=request.impact,
                granted=False,
                outcome="error",
                reason="No approval channel is configured.",
            )
            ctx.approvals.append(record)
            ctx.ledger.record_approval_denied(name)
            return False, {
                "status": "approval_unavailable",
                "reason": record.reason,
                "guidance": (
                    "This action requires human approval and no approval channel "
                    "exists. Notify a human and report 'needs_human'."
                ),
            }

        decision = await gate.request_approval(request)
        waited_ms = int((time.monotonic() - started) * 1000)

        valid, why = decision.is_valid_for(request)
        if valid and hasattr(gate, "consume") and not gate.consume(decision):
            valid, why = False, "approval already used"

        record = ApprovalRecord(
            action=name,
            fingerprint=request.fingerprint,
            requested_at=request.requested_at.isoformat(),
            justification=request.justification,
            impact=request.impact,
            granted=bool(valid),
            outcome=decision.outcome if valid else (decision.outcome or "denied"),
            decided_by=decision.decided_by,
            decided_at=decision.decided_at.isoformat(),
            reason=decision.reason or why,
            waited_ms=waited_ms,
        )
        ctx.approvals.append(record)

        if valid:
            logger.info("Approval granted for %s by %s", name, decision.decided_by or "unknown")
            return True, {}

        ctx.ledger.record_approval_denied(name)
        logger.info("Approval not granted for %s: %s", name, record.reason)
        return False, {
            "status": "not_approved",
            "outcome": record.outcome,
            "decided_by": record.decided_by,
            "reason": record.reason,
            "guidance": (
                "A human did not authorise this action, so it was not performed. "
                "Do not attempt it again and do not look for another route to the "
                "same effect. Notify the requester and call report_resolution with "
                "outcome 'approval_denied'."
            ),
        }

    # --- individual tools --------------------------------------------------

    async def _execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        ctx = self.ctx

        if name == "get_request_context":
            return {
                "status": "ok",
                "request_id": ctx.request.request_id,
                "received_at": ctx.request.received_at,
                "subject": ctx.request.subject,
                "body": ctx.request.body[:4000],
                "report_name": ctx.request.report_name,
                "dataset_id": ctx.request.dataset_id or ctx.dataset_id,
                "workspace_id": ctx.request.workspace_id or ctx.workspace_id,
                "error_code": ctx.request.error_code,
                "signature": ctx.signature,
            }

        if name == "get_known_incidents":
            known = ctx.known_incident
            if known is None:
                return {"known_related_issue": False, "signature": ctx.signature}
            return {
                "known_related_issue": True,
                "signature": ctx.signature,
                "incident_id": known.id,
                "status": known.status,
                "occurrence_count": known.occurrence_count,
                "first_seen_at": known.first_seen_at,
                "diagnosed_root_cause": known.diagnosed_root_cause,
                "guidance": (
                    "An open incident already exists for this exact failure. Do not "
                    "remediate again. Notify and report 'duplicate_suppressed'."
                ),
            }

        if name == "get_dataset_refresh_history":
            top = max(1, min(int(args.get("top", 5) or 5), 20))
            history = await ctx.powerbi.get_refresh_history(
                ctx.workspace_id, ctx.dataset_id, top=top
            )
            return {"count": len(history), "history": history}

        if name == "consult_data_quality_agent":
            if self._dq_agent is None:
                return {"status": "unavailable", "reason": "No Data Quality agent configured"}
            finding = await self._dq_agent.investigate(
                request=ctx.request,
                datasets=ctx.datasets,
                reason=str(args.get("reason", "")),
                ledger=ctx.ledger,
            )
            ctx.dq_finding = finding
            return finding.model_dump()

        if name == "refresh_powerbi_dataset":
            outcome = await ctx.powerbi.refresh_dataset(ctx.workspace_id, ctx.dataset_id)
            ctx.remediation_outcome = outcome
            return {
                "status": outcome.status,
                "succeeded": outcome.succeeded,
                "request_id": outcome.request_id,
                "duration_ms": outcome.duration_ms,
                "detail": outcome.detail,
            }

        if name == "rebind_dataset_gateway":
            target = str(args.get("target_gateway") or "").strip()
            if not target:
                return {"status": "refused", "reason": "No target gateway supplied."}
            outcome = await ctx.powerbi.rebind_gateway(
                ctx.workspace_id, ctx.dataset_id, target
            )
            ctx.remediation_outcome = outcome
            ctx.gateway_rebound_to = target
            return {
                "status": outcome.status,
                "succeeded": outcome.succeeded,
                "target_gateway": target,
                "detail": outcome.detail,
            }

        if name == "write_data_quality_flag":
            finding = ctx.dq_finding
            if finding is None or finding.evidence is None:
                return {
                    "status": "refused",
                    "reason": (
                        "No deterministic duplicate evidence is available. Consult the "
                        "Data Quality agent before writing a flag."
                    ),
                }
            # A flag row asserting zero duplicates is worse than no row: someone
            # triages it, finds nothing, and stops trusting the table.
            if not finding.has_issue or finding.evidence.duplicate_row_count <= 0:
                return {
                    "status": "refused",
                    "reason": (
                        f"The scan found no duplicates in {finding.evidence.table}. "
                        "There is nothing to flag."
                    ),
                }
            flag = build_flag(
                request_id=ctx.request.request_id,
                evidence=finding.evidence,
                detail=str(args.get("detail") or finding.detail)[:1000],
            )
            ctx.flag_table.append(flag)
            ctx.flag_written = True
            return {"status": "written", "flag": flag.model_dump()}

        if name == "notify_teams":
            summary = ResolutionSummary(
                title=str(args.get("title", "BI request triage")),
                report_name=ctx.request.report_name or "",
                error=ctx.request.subject,
                action_taken=str(args.get("action_taken", "")),
                outcome=str(args.get("outcome", "")),
                timestamp=ctx.request.received_at,
                detail=str(args.get("detail", "")),
                facts=_facts_for(ctx),
            )
            ctx.notifications.append(summary)
            ctx.notification_attempted = True
            delivery = await ctx.teams.post(summary)
            ctx.notification_delivered = bool(
                delivery.get("delivered") if isinstance(delivery, dict) else False
            )
            return delivery

        if name == "report_resolution":
            ctx.resolution = dict(args)
            return {"status": "recorded"}

        return {"status": "unknown_tool", "tool": name}

    # --- bookkeeping -------------------------------------------------------

    def _record(
        self,
        name: str,
        args: dict[str, Any],
        summary: str,
        started: float,
        *,
        blocked: bool = False,
    ) -> None:
        self.actions.append(
            TriageAction(
                tool_name=name,
                arguments=args,
                result_summary=summary[:600],
                duration_ms=int((time.monotonic() - started) * 1000),
                is_remediation=is_remediation(name),
                blocked=blocked,
            )
        )


def _impact_of(action: str, arguments: dict[str, Any]) -> str:
    """Plain-language blast radius, shown to whoever is asked to approve.

    An approval request that does not state the consequence is a rubber stamp
    with extra steps.
    """
    if action == "rebind_dataset_gateway":
        target = arguments.get("target_gateway", "the target gateway")
        return (
            f"Repoints this dataset to {target}. Other datasets bound to either "
            "gateway may be affected, and in-flight refreshes will fail."
        )
    return "Modifies the live BI environment."


def _facts_for(ctx: ToolContext) -> dict[str, str]:
    facts: dict[str, str] = {"Signature": ctx.signature or "n/a"}
    finding = ctx.dq_finding
    if finding is not None and finding.has_issue and finding.evidence is not None:
        facts["Data quality"] = finding.evidence.headline()
    if ctx.known_incident is not None:
        facts["Known incident"] = (
            f"{ctx.known_incident.id} (seen {ctx.known_incident.occurrence_count}x)"
        )
    return facts


def _summarize(result: Any) -> str:
    if isinstance(result, dict):
        keys = ("status", "succeeded", "known_related_issue", "has_issue", "count")
        parts = [f"{k}={result[k]}" for k in keys if k in result]
        if parts:
            return ", ".join(parts)
    return str(result)[:300]


def load_dataset_sources(config: list[dict[str, Any]], base_dir: Path) -> dict[str, DatasetSource]:
    """Build the registry of tables the DQ agent may inspect."""
    out: dict[str, DatasetSource] = {}
    for entry in config or []:
        name = entry["name"]
        out[name] = DatasetSource(
            name=name,
            path=(base_dir / entry["path"]).resolve(),
            key_columns=list(entry.get("key_columns") or []),
        )
    return out
