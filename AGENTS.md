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

.\.venv\Scripts\python.exe -m pytest -q       # 90 tests, must stay offline
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\triage-demo.exe list
.\.venv\Scripts\triage-demo.exe run scenario1-transient
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
4. **Deterministic evidence outranks model output.** If a model claim and a
   scan disagree, the scan wins and the disagreement is logged.
5. **Redaction stays inside the store boundary.** Do not move it to call sites;
   a call site can forget.
6. **Every terminal outcome is persisted**, including crashes and refusals.
7. **Spans carry metadata only.** Never attach prompt or completion content.
8. **Scenarios are reproducible.** Same input, same tool sequence, same numbers.
   A demo you cannot rehearse is a demo you should not give.

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
outcome-validation rule are ported from **the production platform**, where equivalents
run against real Microsoft Fabric deployments. When changing one of these,
preserve the comment explaining which production failure motivated it.

## Never

- Commit a filled-in `.env`, a webhook URL, or any credential
- Add a network call to the default test path
- Weaken a policy limit to make a scenario pass — change the scenario
- Put a customer's name in a committed file
