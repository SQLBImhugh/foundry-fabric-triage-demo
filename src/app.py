"""Hosted entry point for the BI triage controller.

This is the same controller the CLI runs, wrapped in the HTTP contract Foundry
expects from a hosted agent. Deploying it changes *where* the loop runs, not
what it does -- the policy ledger, approval gate, deterministic scans and
incident dedup are all the existing code paths.

Two ways in, deliberately
-------------------------
1. **Scheduled (a Foundry routine).** Invoked with no meaningful input, it
   drains the alerts mailbox and triages whatever is new. This is the
   production shape: nobody is watching, and the agent runs on a timer.
2. **Interactive (the Foundry Playground).** Invoked with the text of an alert,
   it triages just that alert. This is what makes the thing demoable without
   waiting for a real email to land.

Both paths run identical logic. That matters: a demo path that diverges from
the production path eventually demos something that does not exist.

Why the controller is hosted rather than left on a laptop
---------------------------------------------------------
Running here means the process can authenticate as the agent's own Microsoft
Entra agent identity. That removes the client secret entirely -- not rotated,
not vaulted, *absent*. It also means the mailbox read, the Power BI call and
the incident write are all attributable to one identity with one named human
sponsor, which is the difference between "an automation did it" and "this
agent, owned by this person, did it".
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

from agent_framework import (
    AgentResponse,
    AgentResponseUpdate,
    BaseAgent,
    Message,
)
from agent_framework._agents import ResponseStream
from agent_framework_foundry_hosting import ResponsesHostServer

from triage_demo.runner import TriageRunner
from triage_demo.settings import settings
from triage_demo.tools.inbox import BIRequest, parse_hints

logging.basicConfig(
    level=os.getenv("TRIAGE_LOG_LEVEL", "INFO"),
    format="%(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("triage.hosted")

REPO_ROOT = Path(__file__).resolve().parent.parent

# A scheduled run says this. Anything else is treated as an alert to triage.
# Deliberately an explicit sentinel rather than a heuristic: the first version
# inferred "no alert" from a short message, and the host turned out to pass
# conversation history, so a five-character sweep request arrived as several
# hundred characters of a previous alert and got re-triaged.
_SWEEP_COMMANDS = frozenset({"sweep", "scheduled sweep", "run", "check mail", ""})


def _latest_text(messages: Any) -> str:
    """Return the most recent inbound message, not the whole conversation.

    The host may pass prior turns. Concatenating them makes every request look
    like the last alert we saw, which silently re-triages stale work.
    """
    if messages is None:
        return ""
    if isinstance(messages, str):
        return messages.strip()

    items = list(messages) if isinstance(messages, (list, tuple)) else [messages]
    if not items:
        return ""

    # Prefer the last user-authored message; fall back to the last of anything.
    def _role(item: Any) -> str:
        return str(getattr(item, "role", "") or "").lower()

    user_items = [i for i in items if _role(i) in ("user", "")]
    candidate = (user_items or items)[-1]
    return _text_of(candidate)


def _text_of(messages: Any) -> str:
    """Flatten whatever the host handed us into plain text."""
    if messages is None:
        return ""
    if isinstance(messages, str):
        return messages
    items = messages if isinstance(messages, (list, tuple)) else [messages]

    parts: list[str] = []
    for item in items:
        if isinstance(item, str):
            parts.append(item)
            continue
        text = getattr(item, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
            continue
        contents = getattr(item, "contents", None) or []
        for content in contents:
            inner = getattr(content, "text", None)
            if isinstance(inner, str) and inner:
                parts.append(inner)
    return "\n".join(p for p in parts if p).strip()


class TriageControllerAgent(BaseAgent):
    """The orchestration loop, exposed as a hosted Foundry agent."""

    def __init__(self) -> None:
        super().__init__(
            name="bi-triage-controller",
            description=(
                "Triages Power BI refresh failures: gathers deterministic evidence, "
                "consults the data quality agent, and remediates within policy."
            ),
        )
        self._runner = TriageRunner(settings, base_dir=REPO_ROOT)
        self._lock = asyncio.Lock()

    def run(  # type: ignore[override]
        self,
        messages: Any = None,
        *,
        stream: bool = False,
        session: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Entry point for both streaming and non-streaming callers.

        Deliberately *not* an ``async def``. The host calls this with
        ``stream=True`` and immediately iterates the result, so an async
        function -- which returns a coroutine -- fails with
        "'coroutine' object has no attribute '__anext__'". The contract is a
        sync method returning either an awaitable or an async iterable.

        Triage is not meaningfully incremental: it runs tools and returns a
        verdict. So the streaming path emits the finished summary as a single
        update rather than pretending to produce tokens.
        """
        if stream:
            return ResponseStream(self._stream(messages), finalizer=self._finalize)
        return self._run_once(messages)

    async def _stream(self, messages: Any) -> AsyncIterator[AgentResponseUpdate]:
        response = await self._run_once(messages)
        text = response.messages[0].text if response.messages else ""
        yield AgentResponseUpdate(
            role="assistant", contents=[{"type": "text", "text": text}]
        )

    @staticmethod
    def _finalize(updates: Sequence[AgentResponseUpdate]) -> AgentResponse[Any]:
        text = "".join(
            content.text
            for update in updates
            for content in (update.contents or [])
            if getattr(content, "text", None)
        )
        return AgentResponse(messages=[Message("assistant", [text])])

    async def _run_once(self, messages: Any) -> AgentResponse[Any]:
        text = _latest_text(messages)
        is_sweep = text.strip().lower() in _SWEEP_COMMANDS

        # One triage at a time. Two concurrent runs would race on the incident
        # store and could remediate the same failure twice -- the exact
        # duplicate-action problem the dedup logic exists to prevent.
        async with self._lock:
            try:
                summary = await (
                    self._drain_mailbox() if is_sweep else self._triage_text(text)
                )
            except Exception as exc:
                logger.exception("Triage run failed")
                summary = (
                    f"Triage failed: {type(exc).__name__}: {exc}\n"
                    "The incident store records every terminal outcome, including this one."
                )

        return AgentResponse(messages=[Message("assistant", [summary])])

    # --- the two entry paths ----------------------------------------------

    async def _triage_text(self, text: str) -> str:
        """Triage an alert pasted straight into the Playground."""
        subject, _, body = text.partition("\n")
        hints = parse_hints(subject, text)
        request = BIRequest(
            request_id=f"interactive-{abs(hash(text)) % 10**10}",
            received_at="",
            sender="playground",
            subject=subject.strip() or "Interactive alert",
            body=body.strip() or text,
            report_name=hints["report_name"],
            dataset_id=hints["dataset_id"],
            workspace_id=hints["workspace_id"],
            error_code=hints["error_code"],
            source="interactive",
        )
        artifacts = await self._runner.run_request(request)
        return _summarise(artifacts)

    async def _drain_mailbox(self) -> str:
        """Triage everything new in the alerts mailbox."""
        inbox = self._runner.build_inbox()

        # Say which identity we are actually presenting. A container can hold a
        # valid token for the wrong principal, which surfaces as a 401 and
        # looks like a missing permission rather than an identity mix-up.
        verify = getattr(inbox, "verify", None)
        if verify is not None:
            auth = await verify()
            logger.info(
                "Graph auth: ok=%s app_id=%s object_id=%s roles=%s has_upn=%s",
                auth.get("ok"),
                auth.get("app_id", ""),
                auth.get("object_id", ""),
                ",".join(auth.get("roles") or []) or "(none)",
                auth.get("has_upn"),
            )
            if auth.get("has_upn"):
                return (
                    "Refusing to read mail: this is a delegated user token, not "
                    "the agent's own identity."
                )

        # Fail closed. App-only Mail.Read is tenant-wide unless Exchange scopes
        # it to a mailbox, so an unscoped agent could read the whole tenant.
        # Refusing here is the difference between a bounded demo and an
        # incident report.
        verify_scope = getattr(inbox, "verify_scope", None)
        if verify_scope is not None and settings.graph_canary_mailbox:
            scope = await verify_scope(settings.graph_canary_mailbox)
            if scope.get("checked") and not scope.get("scoped"):
                return (
                    "Refusing to read mail: this agent is not confined to "
                    f"{settings.graph_mailbox}. {scope.get('reason', '')}"
                )

        requests = await inbox.fetch(limit=10)
        if not requests:
            return "No new alerts."

        lines: list[str] = []
        for request in requests:
            artifacts = await self._runner.run_request(request)
            # Only after the outcome is persisted. A crash before this point
            # means the alert is triaged again next sweep, which is safe; a
            # crash after marking would lose it silently.
            inbox.mark_processed(request.request_id, received_at=request.received_at)
            lines.append(f"- {request.subject or '(no subject)'}: {_summarise(artifacts)}")
        return f"Triaged {len(requests)} alert(s).\n" + "\n".join(lines)


def _summarise(artifacts: Any) -> str:
    """One line a human can act on, not a dump of the run."""
    result = getattr(artifacts, "result", None)
    if result is None:
        return "completed"

    parts = [f"outcome={getattr(result, 'outcome', 'unknown')}"]
    actions = getattr(result, "actions_taken", None)
    if actions:
        parts.append(f"actions={', '.join(str(a) for a in actions)}")
    summary = getattr(result, "summary", "")
    if summary:
        parts.append(str(summary))
    return " | ".join(parts)


def main() -> None:
    agent = TriageControllerAgent()
    logger.info(
        "Starting hosted triage controller (provider=%s, tools=%s, mailbox=%s)",
        settings.triage_provider_mode,
        settings.triage_tool_mode,
        settings.graph_mailbox or "(none)",
    )
    ResponsesHostServer(agent).run()


if __name__ == "__main__":
    main()
