# AGENTS.md

Bootstrap contract for any coding agent working in this repo.

## What this is

A demo of a multi-agent BI triage loop on Azure AI Foundry. It is a **customer-
facing demonstration asset**, not a product. Optimise for legibility and for
being able to explain any line of it out loud, over cleverness.

It runs fully offline. Keep it that way.

## Commands

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

.\.venv\Scripts\python.exe -m pytest -q       # 235 tests, must stay offline
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\triage-demo.exe list
.\.venv\Scripts\triage-demo.exe run scenario1-transient
.\.venv\Scripts\triage-demo.exe identity --check-scope   # who the agents are

# Hosted (runs in Azure, no laptop in the loop)
azd deploy bi-triage-controller --no-prompt
azd ai agent invoke bi-triage-controller "sweep"
azd ai agent monitor bi-triage-controller
```

## Invariants — do not break these

1. **The test suite never touches the network.** No live calls, no credentials,
   no Azure. A test that needs a tenant is not a test.
2. **Policy is enforced in the controller, not the prompt.** Any new limit goes
   in `PolicyLedger` with a test proving it fires. A limit that exists only as
   prompt wording is not a limit.
3. **Every new tool gets an entry in `REMEDIATION_ACTIONS`, `REPORTING_ACTIONS`
   or `DIAGNOSTIC_ACTIONS`.** Anything not on the allowlist is refused before
   dispatch — that is the property the whole demo rests on.
4. **An approval is only a yes if it is explicit, fingerprint-matched, unexpired
   and unused.** Everything else — timeout, error, malformed reply, no gate
   configured — is a no. Silence never reads as consent.
5. **A denial must not consume the remediation budget.** Otherwise one "no"
   silently disarms the agent for the rest of the incident.
6. **Deterministic evidence outranks model output.** If a model claim and a
   scan disagree, the scan wins and the disagreement is logged.
7. **Redaction stays inside the store boundary.** Do not move it to call sites;
   a call site can forget.
8. **Every terminal outcome is persisted**, including crashes and refusals.
9. **Spans carry metadata only.** Never attach prompt or completion content.
10. **Scenarios are reproducible.** Same input, same tool sequence, same numbers.
    A demo you cannot rehearse is a demo you should not give. When a live model
    and the mock diverge, make the **controller** decide so both agree.
11. **The inbox filter is a security control, not housekeeping.** An agent that
    acts on every message is steerable by anyone who can email it. The filter
    fails closed — including when its own pattern is invalid — and counts what
    it ignored rather than dropping it silently. Never widen it to "make the
    demo find something"; send a message that matches instead.
12. **Identity claims are read from the directory, never configured.** Whether
    an agent holds a secret, what it can reach, and who sponsors it are read
    live via Graph. A hardcoded claim about security posture is a claim that
    will eventually be false without anyone noticing.
13. **Grant permissions to the component that acts, not the one that reasons.**
    The prompt agents hold no permissions at all. Only the controller does, and
    each agent gets its own identity, so each needs its own grant. If a new
    permission seems needed on a reasoning agent, the design is wrong.
14. **A tool must fail loudly rather than return something interpretable.**
    Calling Power BI with an empty id returned 404, and the model turned that
    into a confident, wrong conclusion. Validate inputs at the boundary; a
    plausible answer built on a failed call is the worst outcome available.

## Adding things

**A remediation tool**: schema in `TRIAGE_TOOLS` → branch in
`ToolDispatcher._execute` → name in `REMEDIATION_ACTIONS` → a scenario → a test.

**An agent**: mirror `DataQualityAgent`. Own provider, own prompt, own tools,
returns a typed Pydantic model. Expose it to the orchestrator as one tool. It
reports; the orchestrator decides.

**A scenario**: a YAML in `scenarios/` with an `expect` block, then add it to the
parametrized list in `tests/test_scenarios.py`. The `expect` block is the test.

**A prompt change**: prompts are hashed onto every incident, so a change is
traceable. If running in Foundry mode, **re-register the agents** or the change
does not take effect.

**A playbook**: add a `Playbook` to `knowledge/playbooks.py` with triggers, a
`retry_useful` verdict and a **public** Microsoft Learn source, then a test that
it fires on realistic error text. Retrieval is capped at 3 — if a new entry
matters more than an existing one, raise its trigger specificity rather than the
cap.

> **Sourcing rule.** Playbook content must come from public documentation.
> Internal engineering TSGs exist and are more detailed, but they are written for
> on-call engineers debugging the *service*, carry owner and incident-management
> references, and this repo is shared with customers. Use internal sources to
> decide *what matters*; write the entry from public docs. A test enforces this.

## Style

- `from __future__ import annotations` at the top of every module
- `str | None`, not `Optional[str]`
- Pydantic models for anything crossing an agent boundary
- Per-module loggers: `logging.getLogger("triage.<module>")`
- Comment the *why*, not the *what* — especially where a design choice traces to
  a real production incident. Those comments are the most valuable content here
  and are frequently quoted verbatim to customers
- Printed output stays ASCII where practical; Windows consoles mangle the rest

## Provenance

The policy ledger, signature scheme, redaction patterns, incident model and
outcome-validation rule are ported from a **production Fabric operations
platform**, where equivalents run against real Microsoft Fabric deployments.
When changing one of these, preserve the comment explaining which production
failure motivated it.

## Never

- Commit a filled-in `.env`, a webhook URL, or any credential
- Add a network call to the default test path
- Weaken a policy limit to make a scenario pass — change the scenario
- Put a customer's name in a committed file
