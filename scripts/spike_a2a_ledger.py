"""SPIKE — does controller-enforced policy survive agent-to-agent in Foundry?

Run against a live Foundry project. Verified 2026-08-27 against
``denverdata-foundry-cus/denver``.

The question
------------
Does using Foundry's agent-to-agent capability mean giving up the
:class:`PolicyLedger`? If Foundry routes the handoff server-side, the budget
and the allowlist stop being enforced code and become prompt wording.

What was actually found
-----------------------
Two distinct mechanisms, and the distinction matters:

1. ``a2a_preview`` (a toolbox tool) targets an **A2A-protocol** endpoint and
   fetches an agent card first. A Foundry *prompt* agent does not publish one —
   it speaks the ``responses`` protocol — so pointing ``a2a_preview`` at a
   prompt agent fails with ``Failed to fetch agent card ... 401``. To use it,
   the callee must be a **hosted** agent declaring
   ``container_protocol_versions: [{"protocol": "a2a"}]``.

2. Invoking another agent over ``POST {project}/openai/v1/responses`` with an
   ``agent_reference``. This is a genuine call to a separate, independently
   versioned Foundry agent that has its own managed identity — and because
   *our* code makes the call, the ledger charges it.

This script proves (2) end to end, which is what the demo runs. It also records
the exact failure mode of (1) so the finding is not lost.

Run:
    az login
    .\\.venv\\Scripts\\python.exe scripts\\spike_a2a_ledger.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from triage_demo.policy import PolicyLedger, PolicyViolation, TriagePolicy  # noqa: E402
from triage_demo.tools.dataset import detect_duplicates  # noqa: E402

PROJECT_ENDPOINT = (
    "https://denverdata-foundry-cus.services.ai.azure.com/api/projects/denver"
)
RESPONSES_URL = f"{PROJECT_ENDPOINT}/openai/v1/responses"
DQ_AGENT = "bi-data-quality"
SCOPE = "https://ai.azure.com"

REPO_ROOT = Path(__file__).resolve().parents[1]


def token() -> str:
    out = subprocess.run(
        ["az", "account", "get-access-token", "--resource", SCOPE,
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, check=True, shell=True,
    )
    return out.stdout.strip()


def rule(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def main() -> int:
    headers = {"Authorization": f"Bearer {token()}", "Content-Type": "application/json"}

    # Deliberately tight so the limits are demonstrable rather than theoretical.
    policy = TriagePolicy(max_llm_turns=4, max_tool_calls=3, max_write_actions=1)
    ledger = PolicyLedger(policy)

    rule("1. Deterministic scan (no model involved)")
    evidence = detect_duplicates(
        path=REPO_ROOT / "mock" / "data" / "well_production.csv",
        key_columns=["well_id", "production_date"],
        table_name="well_production",
    )
    print(f"   {evidence.headline()}")
    print("   These numbers come from a CSV scan. The model never produces them.")

    rule("2. Charge the agent-to-agent handoff BEFORE dispatching it")
    ledger.charge_tool_call("consult_data_quality_agent")
    ledger.charge_llm_turn()
    print(f"   tool_calls={ledger.tool_calls}/{policy.max_tool_calls}   "
          f"llm_turns={ledger.llm_turns}/{policy.max_llm_turns}")

    rule("3. Invoke the separate Foundry agent")
    payload: dict[str, Any] = {
        "agent_reference": {"type": "agent_reference", "name": DQ_AGENT, "version": "1"},
        "input": (
            "A BI refresh failed for 'Production Daily Summary'.\n\n"
            f"Deterministic scan evidence:\n{evidence.model_dump_json()}\n\n"
            "Return your finding as JSON."
        ),
    }
    with httpx.Client(timeout=120, headers=headers) as client:
        resp = client.post(RESPONSES_URL, json=payload)
        if resp.status_code != 200:
            print(f"   !! HTTP {resp.status_code}: {resp.text[:400]}")
            return 1
        body = resp.json()

    text = ""
    for item in body.get("output", []):
        for chunk in item.get("content", []):
            if chunk.get("type") == "output_text":
                text = chunk.get("text", "")

    ref = body.get("agent_reference", {})
    usage = body.get("usage", {})
    ledger.charge_tokens(int(usage.get("total_tokens", 0)))

    print(f"   responded: {ref.get('name')}:{ref.get('version')}  "
          "(a different agent, own identity and version)")
    print(f"   tokens charged to the shared ledger: {usage.get('total_tokens')}")
    print(f"   reply: {text.strip()[:300]}")

    rule("4. Native guardrails ran on the untrusted input")
    for cf in body.get("content_filters", []):
        if cf.get("source_type") == "prompt":
            r = cf.get("content_filter_results", {})
            print(f"   jailbreak detected      : {r.get('jailbreak', {}).get('detected')}")
            print(f"   indirect_attack detected: {r.get('indirect_attack', {}).get('detected')}")
            print("   XPIA detection is on by default and reported per response, which")
            print("   matters because this agent ingests attacker-influenceable email.")

    rule("5. An action outside the allowlist")
    try:
        ledger.charge_tool_call("delete_dataset")
        print("   !! FAIL - the ledger permitted an unlisted action")
        return 1
    except PolicyViolation as v:
        print(f"   refused ({v.kind})")
        print(f"   {v.message[:150]}")
        print("   -> no HTTP call was made. There is no path from model to unlisted action.")

    rule("RESULT")
    print(json.dumps(ledger.snapshot(), indent=2))
    print()
    print("   A separate Foundry agent was invoked, and every turn, token and tool")
    print("   call was charged against the controller's ledger.")
    print("   Agent-to-agent and enforced policy are not mutually exclusive.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
