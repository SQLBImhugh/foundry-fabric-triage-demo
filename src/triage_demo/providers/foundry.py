"""Azure AI Foundry provider — `foundry` mode.

Talks to the Foundry agents REST surface with ``httpx`` +
``DefaultAzureCredential`` rather than through a client SDK. That is a
deliberate choice: the Foundry Python SDKs have churned repeatedly across
preview versions, and a demo that breaks because a package minor-bumped the
week before is a bad demo. The REST contract has been comparatively stable,
and it makes the wire format visible on screen — which is exactly what the
customer asked to see.

Two handoff shapes are supported, and the difference matters:

**Client-orchestrated** (``handoff_mode="client"``, the default here)
    The Triage agent declares ``consult_data_quality_agent`` as an ordinary
    tool. When the model calls it, *this process* invokes the Data Quality
    agent and feeds the structured result back. The controller sees the
    handoff, can charge it against the ledger, and can refuse it.

**Connected agent** (``handoff_mode="connected"``)
    The Data Quality agent is registered as a tool *of the Triage agent* in
    the Foundry control plane. Foundry performs the handoff server-side. It
    demos beautifully and the trace is legible — but the calling process no
    longer sits between the two agents, so any budget or allowlist you rely
    on has to be expressed as agent instructions rather than code.

Show both. Ship the first.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from triage_demo.observability import gen_ai_span
from triage_demo.providers.base import LLMResponse, ToolCall

logger = logging.getLogger("triage.provider.foundry")

FOUNDRY_SCOPE = "https://ai.azure.com/.default"
API_VERSION = "2025-05-15-preview"


class FoundryAgentProvider:
    """Invokes a named Foundry agent, returning tool calls for local execution."""

    provider_name = "foundry"

    def __init__(
        self,
        *,
        project_endpoint: str,
        agent_name: str,
        api_version: str = API_VERSION,
        handoff_mode: str = "client",
    ):
        if not project_endpoint:
            raise ValueError("FOUNDRY_PROJECT_ENDPOINT is required for foundry mode")
        self._endpoint = project_endpoint.rstrip("/")
        self.model_name = agent_name
        self._agent_name = agent_name
        self._api_version = api_version
        self.handoff_mode = handoff_mode
        self._token: str = ""
        self._token_expires_at: float = 0.0

    # --- auth --------------------------------------------------------------

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        from azure.identity import DefaultAzureCredential

        cred = DefaultAzureCredential()
        token = cred.get_token(FOUNDRY_SCOPE)
        self._token = token.token
        self._token_expires_at = float(token.expires_on)
        return self._token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    # --- invocation --------------------------------------------------------

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.1,
    ) -> LLMResponse:
        import httpx

        url = f"{self._endpoint}/agents/{self._agent_name}/responses"
        payload: dict[str, Any] = {
            "input": _to_foundry_input(messages),
            "temperature": temperature,
        }
        # In connected-agent mode the tool list lives on the registered agent
        # definition; sending it per-request would shadow it.
        if tools and self.handoff_mode == "client":
            payload["tools"] = tools

        with gen_ai_span(
            provider=self.provider_name,
            model=self.model_name,
            **{"gen_ai.agent.name": self._agent_name, "handoff.mode": self.handoff_mode},
        ) as span:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    url,
                    params={"api-version": self._api_version},
                    headers=self._headers(),
                    json=payload,
                )
                resp.raise_for_status()
                body = resp.json()

            parsed = _parse_response(body)
            span.record_usage(parsed.prompt_tokens, parsed.completion_tokens)
            span.record_finish(parsed.finish_reason)
            return parsed

    async def close(self) -> None:
        return None


def _to_foundry_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate chat-style messages into Foundry response input items."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            out.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id", ""),
                    "output": msg.get("content", ""),
                }
            )
            continue

        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            out.append(
                {
                    "type": "function_call",
                    "call_id": call.get("id", ""),
                    "name": fn.get("name", ""),
                    "arguments": fn.get("arguments", "{}"),
                }
            )

        content = msg.get("content")
        if content:
            out.append({"role": role, "content": content})
    return out


def _parse_response(body: dict[str, Any]) -> LLMResponse:
    """Extract text + function calls from a Foundry response payload."""
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []

    for item in body.get("output", []) or []:
        item_type = item.get("type")
        if item_type in ("function_call", "tool_call"):
            raw_args = item.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except json.JSONDecodeError:
                logger.warning("Foundry returned unparseable arguments for %s", item.get("name"))
                args = {}
            tool_calls.append(
                ToolCall(
                    id=item.get("call_id") or item.get("id", ""),
                    name=item.get("name", ""),
                    arguments=args,
                )
            )
        elif item_type == "message":
            for chunk in item.get("content", []) or []:
                if chunk.get("type") in ("output_text", "text"):
                    text_parts.append(chunk.get("text", ""))

    usage = body.get("usage") or {}
    return LLMResponse(
        content="\n".join(p for p in text_parts if p),
        tool_calls=tool_calls,
        finish_reason="tool_calls" if tool_calls else (body.get("status") or "stop"),
        prompt_tokens=int(usage.get("input_tokens", 0) or 0),
        completion_tokens=int(usage.get("output_tokens", 0) or 0),
    )
