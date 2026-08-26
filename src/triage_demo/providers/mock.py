"""Scripted provider — deterministic, offline, no network.

**This is not an agent.** It is a fixed state machine that emits the tool
calls a competent agent would emit, so that:

* the repo runs end-to-end on a laptop with no tenant,
* the tests assert on orchestration and policy rather than on model output,
* a demo rehearsal produces byte-identical results every time.

Switch ``TRIAGE_PROVIDER_MODE`` to ``direct`` or ``foundry`` for the real
thing. Everything downstream of this class is identical in all three modes —
that is the point of the interface.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from triage_demo.providers.base import LLMResponse, ToolCall


def _tool_results(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Map tool name -> parsed result, for the most recent call of each tool."""
    out: dict[str, Any] = {}
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        name = msg.get("name")
        if not name:
            continue
        raw = msg.get("content", "")
        try:
            out[name] = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            out[name] = {"raw": raw}
    return out


def _call_counts(messages: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for msg in messages:
        for call in msg.get("tool_calls") or []:
            fn = (call.get("function") or {}).get("name") or call.get("name")
            if fn:
                counts[fn] = counts.get(fn, 0) + 1
    return counts


@dataclass
class ScriptedProvider:
    """Emits one tool call per turn following the triage flow."""

    provider_name: str = "mock"
    model_name: str = "scripted-triage-v1"

    #: When True, attempts a second remediation after the first succeeds.
    #: Used to demonstrate that the controller — not the prompt — refuses it.
    rogue_second_refresh: bool = False

    #: When True, proposes an action that is not on the allowlist at all.
    rogue_unknown_action: bool = False

    turns: list[str] = field(default_factory=list)

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.1,
    ) -> LLMResponse:
        results = _tool_results(messages)
        counts = _call_counts(messages)
        name, args, thought = self._next_step(results, counts)
        self.turns.append(name)

        return LLMResponse(
            content=thought,
            tool_calls=[ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name=name, arguments=args)],
            finish_reason="tool_calls",
            prompt_tokens=420 + 60 * len(messages),
            completion_tokens=90,
        )

    # --- the state machine -------------------------------------------------

    def _next_step(
        self, results: dict[str, Any], counts: dict[str, int]
    ) -> tuple[str, dict[str, Any], str]:
        called = counts.keys()

        if "get_request_context" not in called:
            return ("get_request_context", {}, "Reading the request before deciding anything.")

        # Known-issue check ALWAYS precedes remediation.
        if "get_known_incidents" not in called:
            return (
                "get_known_incidents",
                {},
                "Checking whether this failure signature is already open.",
            )

        known = results.get("get_known_incidents", {})
        if known.get("known_related_issue"):
            if "notify_teams" not in called:
                return (
                    "notify_teams",
                    {
                        "title": "Known issue - no action taken",
                        "outcome": "duplicate_suppressed",
                        "action_taken": "none",
                        "detail": (
                            f"This matches open incident {known.get('incident_id')}, "
                            f"seen {known.get('occurrence_count')} time(s). Waiting for "
                            "the existing investigation rather than remediating again."
                        ),
                    },
                    "Already tracked. Notifying without acting.",
                )
            return (
                "report_resolution",
                {
                    "outcome": "duplicate_suppressed",
                    "tier": "tier_1",
                    "category": "transient",
                    "severity": "low",
                    "root_cause": known.get("diagnosed_root_cause")
                    or "Recurrence of a known open incident.",
                    "summary": (
                        "Suppressed as a duplicate of an open incident. No remediation "
                        "performed."
                    ),
                    "reasoning": [
                        "Signature matched an open incident.",
                        "Repeat remediation would add load without adding information.",
                    ],
                },
                "Reporting suppression.",
            )

        # Data quality gate — always before remediation.
        if "consult_data_quality_agent" not in called:
            return (
                "consult_data_quality_agent",
                {"reason": "Rule out a data quality cause before treating this as transient."},
                "Handing off to the Data Quality agent.",
            )

        finding = results.get("consult_data_quality_agent", {})
        if finding.get("has_issue"):
            if "write_data_quality_flag" not in called:
                return (
                    "write_data_quality_flag",
                    {"detail": finding.get("detail", "Duplicate records detected.")},
                    "Recording the issue in the flag table.",
                )
            if "notify_teams" not in called:
                return (
                    "notify_teams",
                    {
                        "title": "Data quality issue detected",
                        "outcome": "flagged_data_quality",
                        "action_taken": "Flagged in the data quality table. No automated fix.",
                        "detail": finding.get("detail", ""),
                    },
                    "Notifying the owner with the specific finding.",
                )
            return (
                "report_resolution",
                {
                    "outcome": "flagged_data_quality",
                    "tier": "tier_2",
                    "category": "data_quality",
                    "severity": "medium",
                    "root_cause": finding.get("detail", "Duplicate records in the source table."),
                    "summary": "Data quality issue flagged and notified. No automated remediation.",
                    "reasoning": [
                        "Data Quality agent returned duplicate evidence.",
                        "Refreshing a report does not remove duplicate source rows.",
                        "Remediation deliberately withheld; a human owns the fix.",
                    ],
                },
                "Reporting a flagged data quality issue.",
            )

        # No data quality issue -> transient path.
        if "get_dataset_refresh_history" not in called:
            return (
                "get_dataset_refresh_history",
                {"top": 5},
                "Checking history to confirm this is a one-off.",
            )

        if self.rogue_unknown_action and "delete_dataset" not in called:
            return (
                "delete_dataset",
                {"justification": "Clean slate."},
                "Attempting an action outside the allowlist.",
            )

        refresh_count = counts.get("refresh_powerbi_dataset", 0)
        if refresh_count == 0:
            return (
                "refresh_powerbi_dataset",
                {"justification": "Transient refresh failure with a clean data quality check."},
                "Applying the single permitted remediation.",
            )

        if self.rogue_second_refresh and refresh_count == 1:
            return (
                "refresh_powerbi_dataset",
                {"justification": "Trying once more for good measure."},
                "Attempting a second remediation.",
            )

        refresh = results.get("refresh_powerbi_dataset", {})
        blocked = refresh.get("status") == "blocked_by_policy"
        succeeded = bool(refresh.get("succeeded"))

        if "notify_teams" not in called:
            return (
                "notify_teams",
                {
                    "title": "Transient failure resolved" if succeeded else "Triage needs a human",
                    "outcome": "resolved" if succeeded else "needs_human",
                    "action_taken": "Power BI dataset refresh via REST API",
                    "detail": refresh.get("detail", ""),
                },
                "Posting the resolution summary.",
            )

        if succeeded and not blocked:
            return (
                "report_resolution",
                {
                    "outcome": "resolved",
                    "tier": "tier_1",
                    "category": "transient",
                    "severity": "low",
                    "root_cause": "Transient dataset refresh failure.",
                    "summary": "Refresh re-triggered via the Power BI REST API and completed.",
                    "reasoning": [
                        "No open incident for this signature.",
                        "Data Quality agent reported no issue.",
                        "Refresh history showed an isolated failure.",
                        "Single remediation applied and verified.",
                    ],
                },
                "Reporting a resolved Tier 1 failure.",
            )

        return (
            "report_resolution",
            {
                "outcome": "needs_human",
                "tier": "needs_human",
                "category": "app",
                "severity": "medium",
                "root_cause": "Remediation did not succeed or was refused by policy.",
                "summary": "Escalating to a human; the agent's remediation allowance is spent.",
                "reasoning": ["Remediation unavailable or unsuccessful.", "Escalating rather than retrying."],
            },
            "Escalating.",
        )

    async def close(self) -> None:
        return None


@dataclass
class ScriptedDataQualityProvider:
    """Scripted reasoning for the Data Quality agent."""

    provider_name: str = "mock"
    model_name: str = "scripted-dq-v1"

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.1,
    ) -> LLMResponse:
        results = _tool_results(messages)
        counts = _call_counts(messages)

        if "check_duplicates" not in counts:
            table = ""
            for msg in messages:
                if msg.get("role") == "user" and "tables:" in str(msg.get("content", "")):
                    table = str(msg["content"]).split("tables:", 1)[1].split("\n", 1)[0].strip()
                    break
            return LLMResponse(
                content="Scanning the registered table for duplicate keys.",
                tool_calls=[
                    ToolCall(
                        id=f"call_{uuid.uuid4().hex[:8]}",
                        name="check_duplicates",
                        arguments={"table": table.split(",")[0].strip()},
                    )
                ],
                finish_reason="tool_calls",
                prompt_tokens=280,
                completion_tokens=40,
            )

        evidence = results.get("check_duplicates", {})
        dupes = int(evidence.get("duplicate_row_count", 0) or 0)
        if dupes > 0:
            payload = {
                "has_issue": True,
                "issue_type": "duplicates",
                "confidence": 1.0,
                "detail": evidence.get("headline", "Duplicate records detected."),
                "recommended_action": "flag_and_notify",
            }
        else:
            payload = {
                "has_issue": False,
                "issue_type": "none",
                "confidence": 1.0,
                "detail": "No duplicate keys found in the registered tables.",
                "recommended_action": "no_action",
            }

        return LLMResponse(
            content=json.dumps(payload),
            finish_reason="stop",
            prompt_tokens=320,
            completion_tokens=60,
        )

    async def close(self) -> None:
        return None
