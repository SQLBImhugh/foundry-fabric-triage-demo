"""The Data Quality agent — a genuinely separate agent, not a tool.

It has its own provider, its own system prompt, its own tool, and its own
small controller loop. The Triage agent reaches it only through the
``consult_data_quality_agent`` tool, and receives a typed
:class:`DataQualityFinding` back.

The important property: **deterministic evidence overrides the model.** The
scan produces the numbers; the model produces the sentence. If the model
contradicts the scan, the scan wins and the disagreement is logged. An agent
that can talk itself out of its own evidence is not one you can put in front
of an operations team.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from triage_demo.models import BIRequest, DataQualityFinding, DuplicateEvidence
from triage_demo.observability import tool_span, with_agent_context
from triage_demo.prompts import load_prompt, prompt_version_hash
from triage_demo.providers.base import BaseProvider
from triage_demo.tools.dataset import DatasetSource, detect_duplicates
from triage_demo.tools.registry import DQ_TOOLS

logger = logging.getLogger("triage.agent.dq")

PROMPT_FILE = "data_quality_system.md"


class DataQualityAgent:
    AGENT_NAME = "DataQualityAgent"

    def __init__(self, provider: BaseProvider, *, max_turns: int = 4):
        self._provider = provider
        self._max_turns = max_turns

    @property
    def model_info(self) -> str:
        return f"{self._provider.model_name} -> {self._provider.provider_name}"

    @property
    def prompt_hash(self) -> str:
        return prompt_version_hash(PROMPT_FILE)

    @with_agent_context(AGENT_NAME)
    async def investigate(
        self,
        *,
        request: BIRequest,
        datasets: dict[str, DatasetSource],
        reason: str = "",
        ledger: Any = None,
    ) -> DataQualityFinding:
        """Inspect the registered tables and return a structured finding.

        ``ledger`` is the orchestrator's :class:`PolicyLedger`. It is shared
        rather than per-agent on purpose: a budget that only covers the
        orchestrator is not a budget for the run. Without this, a second agent
        could burn an unbounded number of turns and tokens while the reported
        totals stayed small.
        """
        if not datasets:
            return DataQualityFinding(
                has_issue=False,
                issue_type="unknown",
                confidence=0.0,
                detail="No tables are registered for inspection.",
                agent_name=self.AGENT_NAME,
            )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": load_prompt(PROMPT_FILE)},
            {
                "role": "user",
                "content": (
                    f"A BI failure was reported.\n"
                    f"subject: {request.subject}\n"
                    f"report: {request.report_name or 'unknown'}\n"
                    f"consultation reason: {reason or 'routine data quality gate'}\n"
                    f"tables: {', '.join(datasets)}\n\n"
                    "Inspect the table(s) and return your finding as JSON."
                ),
            },
        ]

        evidence: DuplicateEvidence | None = None
        model_payload: dict[str, Any] = {}

        for _ in range(self._max_turns):
            if ledger is not None:
                ledger.charge_llm_turn()

            resp = await self._provider.complete(messages=messages, tools=DQ_TOOLS)

            if ledger is not None:
                ledger.charge_tokens(resp.total_tokens)

            if not resp.tool_calls:
                model_payload = _parse_json_object(resp.content)
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": resp.content or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments),
                            },
                        }
                        for call in resp.tool_calls
                    ],
                }
            )

            for call in resp.tool_calls:
                if ledger is not None:
                    ledger.charge_tool_call(call.name)
                result = self._execute(call.name, call.arguments, datasets)
                if call.name == "check_duplicates" and isinstance(result, dict):
                    ev = result.get("_evidence")
                    if isinstance(ev, DuplicateEvidence):
                        evidence = ev
                        result = {k: v for k, v in result.items() if k != "_evidence"}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": json.dumps(result, default=str),
                    }
                )

        return self._reconcile(model_payload, evidence, list(datasets))

    # --- tools -------------------------------------------------------------

    def _execute(
        self, name: str, args: dict[str, Any], datasets: dict[str, DatasetSource]
    ) -> Any:
        if name != "check_duplicates":
            return {"status": "unknown_tool", "tool": name}

        requested = str(args.get("table") or "").strip()
        source = datasets.get(requested) or next(iter(datasets.values()))

        with tool_span("check_duplicates", table=source.name):
            if not source.exists():
                return {
                    "status": "missing",
                    "table": source.name,
                    "reason": f"No file at {source.path}",
                }

            evidence = detect_duplicates(
                path=source.path,
                key_columns=source.key_columns,
                table_name=source.name,
            )

        return {
            "status": "ok",
            "table": evidence.table,
            "key_columns": evidence.key_columns,
            "duplicate_group_count": evidence.duplicate_group_count,
            "duplicate_row_count": evidence.duplicate_row_count,
            "total_row_count": evidence.total_row_count,
            "sample_keys": evidence.sample_keys,
            "headline": evidence.headline(),
            "_evidence": evidence,
        }

    # --- reconciliation ----------------------------------------------------

    def _reconcile(
        self,
        payload: dict[str, Any],
        evidence: DuplicateEvidence | None,
        checked: list[str],
    ) -> DataQualityFinding:
        """Build the finding, letting deterministic evidence win any conflict."""
        detail = str(payload.get("detail") or "").strip()
        confidence = _as_float(payload.get("confidence"), default=0.0)
        recommended = payload.get("recommended_action")
        if recommended not in ("flag_and_notify", "no_action", "escalate"):
            recommended = None

        if evidence is None:
            return DataQualityFinding(
                has_issue=bool(payload.get("has_issue")),
                issue_type="unknown",
                confidence=min(confidence, 0.5),
                detail=detail or "No deterministic scan was completed.",
                checked_tables=checked,
                recommended_action=recommended or "escalate",
                agent_name=self.AGENT_NAME,
            )

        truth = evidence.duplicate_row_count > 0
        claimed = payload.get("has_issue")
        if claimed is not None and bool(claimed) != truth:
            logger.warning(
                "DQ agent claimed has_issue=%s but the scan found %d duplicate rows in %s "
                "— deferring to the scan.",
                claimed,
                evidence.duplicate_row_count,
                evidence.table,
            )

        if truth:
            # The model's prose is discarded when it contradicts the scan.
            # Returning has_issue=True alongside "looks fine to me" would put a
            # self-contradicting sentence in a Teams message.
            contradicted = claimed is False
            return DataQualityFinding(
                has_issue=True,
                issue_type="duplicates",
                confidence=1.0,
                detail=evidence.headline() if contradicted else (detail or evidence.headline()),
                evidence=evidence,
                checked_tables=checked,
                recommended_action=(
                    "flag_and_notify" if contradicted else (recommended or "flag_and_notify")
                ),
                agent_name=self.AGENT_NAME,
            )

        contradicted = claimed is True
        return DataQualityFinding(
            has_issue=False,
            issue_type="none",
            confidence=1.0,
            detail=(
                f"No duplicate keys found in {evidence.table}."
                if contradicted
                else (detail or f"No duplicate keys found in {evidence.table}.")
            ),
            evidence=evidence,
            checked_tables=checked,
            recommended_action="no_action",
            agent_name=self.AGENT_NAME,
        )

    async def close(self) -> None:
        await self._provider.close()


def _parse_json_object(content: str) -> dict[str, Any]:
    """Tolerate fenced code blocks and leading prose around the JSON."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        text = text.removeprefix("json").strip()

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                pass
    logger.warning("Data Quality agent returned unparseable output")
    return {}


def _as_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
