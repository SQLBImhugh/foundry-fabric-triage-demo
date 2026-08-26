"""Teams notification — the "Send Resolution Summary" terminal step.

Message contents are fixed by the customer's spec: report name, error, action
taken, outcome, timestamp. Everything posted is redacted first, because error
text from a BI platform routinely carries tokens.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from triage_demo.redaction import redact_text

logger = logging.getLogger("triage.teams")


@dataclass
class ResolutionSummary:
    """The card posted to Teams."""

    title: str
    report_name: str
    error: str
    action_taken: str
    outcome: str
    timestamp: str
    detail: str = ""
    facts: dict[str, str] = field(default_factory=dict)

    def to_markdown(self) -> str:
        lines = [
            f"**{self.title}**",
            "",
            f"- **Report**: {self.report_name or 'n/a'}",
            f"- **Error**: {redact_text(self.error) or 'n/a'}",
            f"- **Action taken**: {self.action_taken or 'none'}",
            f"- **Outcome**: {self.outcome}",
            f"- **Timestamp**: {self.timestamp}",
        ]
        for key, value in self.facts.items():
            lines.append(f"- **{key}**: {redact_text(str(value))}")
        if self.detail:
            lines += ["", redact_text(self.detail)]
        return "\n".join(lines)

    def to_adaptive_card(self) -> dict[str, Any]:
        facts = [
            {"title": "Report", "value": self.report_name or "n/a"},
            {"title": "Error", "value": redact_text(self.error)[:300] or "n/a"},
            {"title": "Action taken", "value": self.action_taken or "none"},
            {"title": "Outcome", "value": self.outcome},
            {"title": "Timestamp", "value": self.timestamp},
        ] + [{"title": k, "value": redact_text(str(v))[:300]} for k, v in self.facts.items()]

        body: list[dict[str, Any]] = [
            {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": self.title},
            {"type": "FactSet", "facts": facts},
        ]
        if self.detail:
            body.append(
                {"type": "TextBlock", "wrap": True, "text": redact_text(self.detail)[:1000]}
            )

        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": body,
                    },
                }
            ],
        }


class TeamsNotifier(Protocol):
    async def post(self, summary: ResolutionSummary) -> dict[str, Any]: ...


@dataclass
class MockTeamsNotifier:
    """Collects messages in memory so tests can assert on them."""

    messages: list[ResolutionSummary] = field(default_factory=list)

    async def post(self, summary: ResolutionSummary) -> dict[str, Any]:
        self.messages.append(summary)
        logger.info("Teams (mock) <- %s", summary.title)
        return {"status": "ok", "delivered": True, "transport": "mock"}

    @property
    def last(self) -> ResolutionSummary | None:
        return self.messages[-1] if self.messages else None


class WebhookTeamsNotifier:
    """Posts an Adaptive Card to an incoming webhook.

    Lowest-friction path for a demo. For production, post via Graph
    ``/chats/{id}/messages`` with an app registration so the message is
    attributable to an identity rather than an unauthenticated URL.
    """

    def __init__(self, webhook_url: str, timeout: int = 20):
        self._url = webhook_url
        self._timeout = timeout

    async def post(self, summary: ResolutionSummary) -> dict[str, Any]:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._url, json=summary.to_adaptive_card())
            ok = 200 <= resp.status_code < 300
            if not ok:
                logger.warning("Teams webhook returned %s: %s", resp.status_code, resp.text[:200])
            return {
                "status": "ok" if ok else "error",
                "delivered": ok,
                "http_status": resp.status_code,
                "transport": "webhook",
            }
