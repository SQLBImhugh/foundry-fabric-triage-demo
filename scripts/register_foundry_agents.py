"""Register (or re-version) the two Foundry agents from local source.

Run this before any demo in ``foundry`` mode. It is idempotent: it hashes the
local prompt + tool schema, compares against the latest registered version, and
posts a new version only when something actually changed.

Why this script exists at all
-----------------------------
In ``direct`` mode the local files ARE the agent — a prompt edit takes effect on
the next process start. In ``foundry`` mode the agent definition lives in the
Foundry control plane, and a local edit changes nothing until it is pushed. On
the production platform that gap silently shipped a stale agent more than once: the
tool was added, the tests passed against direct mode, and the named agent went
on running the previous definition.

Treat "did I re-register?" as part of the deploy checklist, not as a detail.

Handoff shapes
--------------
``--handoff client`` (default)
    The Triage agent gets ``consult_data_quality_agent`` as an ordinary
    function tool. Your process performs the handoff, which means your process
    can also refuse it. This is the shape the demo runs.

``--handoff connected``
    The Data Quality agent is attached to the Triage agent as a connected
    agent, and Foundry performs the handoff server-side. Fewer moving parts,
    a very legible trace — and no place to put a budget.

    **This mode is not end-to-end runnable as registered here**, and the demo
    does not run it. Foundry executes a connected agent server-side, so that
    agent's tools must also be server-callable. The Data Quality agent's only
    tool is ``check_duplicates``, a local CSV scan running in this process —
    Foundry cannot invoke it. To make connected mode real you would expose the
    scan as an OpenAPI or Azure Function tool first.

    Register it to show the shape and the trace, and be explicit about both the
    tooling requirement and the fact that the ledger no longer sits in the
    middle. Do not claim it is the running configuration.

Usage
-----
    az login
    python scripts/register_foundry_agents.py --dry-run
    python scripts/register_foundry_agents.py --handoff connected
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from triage_demo.prompts import load_prompt  # noqa: E402
from triage_demo.tools.registry import DQ_TOOLS, TRIAGE_TOOLS  # noqa: E402

API_VERSION = "2025-05-15-preview"
RESOURCE_SCOPE = "https://ai.azure.com/.default"

DEFAULT_MODEL = os.environ.get("FOUNDRY_AGENT_MODEL", "gpt-4o")


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _hash(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def _token() -> str:
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential().get_token(RESOURCE_SCOPE).token


def build_definitions(*, handoff: str, dq_agent_name: str, model: str) -> dict[str, dict]:
    """Build both agent definitions for the requested handoff shape."""
    triage_tools: list[dict[str, Any]] = list(TRIAGE_TOOLS)

    if handoff == "connected":
        # Replace the function tool with a connected-agent tool. Foundry then
        # routes the call to the other agent itself.
        triage_tools = [
            tool
            for tool in triage_tools
            if tool.get("function", {}).get("name") != "consult_data_quality_agent"
        ]
        triage_tools.append(
            {
                "type": "connected_agent",
                "connected_agent": {
                    "name": "consult_data_quality_agent",
                    "agent_name": dq_agent_name,
                    "description": (
                        "Hand off to the Data Quality agent to inspect the underlying "
                        "dataset for duplicate records and return a structured finding."
                    ),
                },
            }
        )

    return {
        "triage": {
            "model": model,
            "instructions": load_prompt("triage_system.md"),
            "tools": triage_tools,
            "metadata": {
                "handoff_mode": handoff,
                "source": "foundry-fabric-triage-demo",
            },
        },
        "data_quality": {
            "model": model,
            "instructions": load_prompt("data_quality_system.md"),
            "tools": list(DQ_TOOLS),
            "metadata": {"source": "foundry-fabric-triage-demo"},
        },
    }


def get_latest_version(client, endpoint: str, agent_name: str) -> dict[str, Any] | None:
    resp = client.get(
        f"{endpoint}/agents/{agent_name}", params={"api-version": API_VERSION}
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    latest = (resp.json().get("versions") or {}).get("latest")
    return latest if isinstance(latest, dict) else None


def post_new_version(client, endpoint: str, agent_name: str, definition: dict) -> dict:
    resp = client.post(
        f"{endpoint}/agents/{agent_name}/versions",
        params={"api-version": API_VERSION},
        json={"definition": definition},
    )
    resp.raise_for_status()
    return resp.json()


def sync_agent(client, endpoint: str, agent_name: str, definition: dict, *, dry_run: bool) -> str:
    local = _hash(definition)
    latest = get_latest_version(client, endpoint, agent_name)

    if latest is not None and _hash(latest.get("definition", {})) == local:
        return f"{agent_name}: already in sync (version {latest.get('version', '?')})"

    if dry_run:
        state = "does not exist" if latest is None else "differs from local"
        return f"{agent_name}: WOULD POST a new version ({state})"

    created = post_new_version(client, endpoint, agent_name, definition)
    return f"{agent_name}: posted version {created.get('version', '?')}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("FOUNDRY_PROJECT_ENDPOINT", ""),
        help="Foundry project endpoint (or set FOUNDRY_PROJECT_ENDPOINT)",
    )
    parser.add_argument(
        "--handoff", choices=["client", "connected"], default="client",
        help="Agent-to-agent handoff shape to register",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--triage-name", default=os.environ.get("FOUNDRY_TRIAGE_AGENT_NAME", "bi-triage")
    )
    parser.add_argument(
        "--dq-name", default=os.environ.get("FOUNDRY_DQ_AGENT_NAME", "bi-data-quality")
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--print-definitions", action="store_true",
        help="Print the definitions and exit without contacting Azure",
    )
    args = parser.parse_args(argv)

    definitions = build_definitions(
        handoff=args.handoff, dq_agent_name=args.dq_name, model=args.model
    )

    if args.print_definitions:
        print(json.dumps(definitions, indent=2))
        return 0

    if not args.endpoint:
        print(
            "ERROR: no Foundry endpoint. Pass --endpoint or set FOUNDRY_PROJECT_ENDPOINT.\n"
            "       Use --print-definitions to inspect what would be registered.",
            file=sys.stderr,
        )
        return 2

    endpoint = args.endpoint.rstrip("/")

    try:
        import httpx
    except ImportError:
        print("ERROR: httpx is required. pip install -e '.[azure]'", file=sys.stderr)
        return 2

    headers = {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}

    # The Data Quality agent is registered first: in connected mode the Triage
    # agent references it by name, and a reference to an agent that does not
    # exist yet is rejected.
    with httpx.Client(timeout=60, headers=headers) as client:
        print(sync_agent(client, endpoint, args.dq_name, definitions["data_quality"], dry_run=args.dry_run))
        print(sync_agent(client, endpoint, args.triage_name, definitions["triage"], dry_run=args.dry_run))

    print(f"\nHandoff mode registered: {args.handoff}")
    if args.handoff == "connected":
        print(
            "\nWARNING: connected mode is registered, but NOT end-to-end runnable as-is.\n"
            "  Foundry executes a connected agent server-side, so that agent's tools must\n"
            "  be server-callable. The Data Quality agent's only tool (check_duplicates)\n"
            "  is a local scan in the calling process, which Foundry cannot invoke.\n"
            "  Expose it as an OpenAPI or Azure Function tool to make this mode real.\n"
            "\n"
            "  Also note: in connected mode the local PolicyLedger no longer sits between\n"
            "  the two agents, so budgets and the action allowlist become agent\n"
            "  instructions rather than enforced code. Show the shape; ship client mode."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
