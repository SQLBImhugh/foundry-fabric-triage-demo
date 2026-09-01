# Contributing

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

The suite runs offline. If it needs a tenant, something is wrong.

## Invariants

`AGENTS.md` holds the full list. These four are the ones a pull request is most
likely to break:

1. **Tests never touch the network.** No live calls, no credentials, no Azure.
   A test that needs a tenant is not a test.
2. **Policy is enforced in the controller, not the prompt.** A new limit goes in
   `PolicyLedger` with a test proving it fires. A limit that exists only as
   prompt wording is not a limit.
3. **Every tool is on an allowlist** — `REMEDIATION_ACTIONS`,
   `REPORTING_ACTIONS` or `DIAGNOSTIC_ACTIONS`. Anything else is refused before
   dispatch. That is the property the whole system rests on.
4. **Deterministic evidence outranks model output.** If a model claim and a
   measurement disagree, the measurement wins and the disagreement is logged.

## Adding things

**A remediation tool**: schema in `TRIAGE_TOOLS` → branch in
`ToolDispatcher._execute` → name in an action allowlist → a scenario → a test.
Decide deliberately whether it belongs in `APPROVAL_REQUIRED_ACTIONS`.

**An agent**: mirror `DataQualityAgent`. Own provider, own prompt, own tools,
returns a typed Pydantic model, exposed to the orchestrator as one tool. It
reports; the orchestrator decides.

**A scenario**: a YAML in `scenarios/` with an `expect` block, then add it to the
parametrized lists in `tests/test_scenarios.py` *and* `tests/test_hardening.py`.
The `expect` block is the test. Give it its own alert fixture — scenarios that
share an email suppress each other, and there is a test for that.

**A playbook**: a `Playbook` in `knowledge/playbooks.py` with triggers, a
`retry_useful` verdict and a **public** Microsoft Learn source, then a test that
it fires on realistic error text.

> Playbook content must come from public documentation. Internal engineering
> TSGs are more detailed but are written for on-call engineers debugging the
> service, carry owner and incident-management references, and this repository
> is shared with customers. Use internal sources to decide what matters; write
> the entry from public docs. A test enforces this.

**A prompt change**: prompts are hashed onto every incident. If you are running
in Foundry mode, **re-register the agents** or the change has no effect:

```powershell
.\.venv\Scripts\python.exe scripts\register_foundry_agents.py
```

## Negative controls

A test that passes whether or not the feature works is not a test. For anything
that guards a safety property, verify the guard by temporarily disabling it and
confirming a *named* test fails. Several bugs in this repository were checks
incapable of detecting what they existed to catch.

## Style

- `from __future__ import annotations` at the top of every module
- `str | None`, not `Optional[str]`
- Pydantic models for anything crossing an agent boundary
- Per-module loggers: `logging.getLogger("triage.<module>")`
- Comment the **why**, not the what — especially where a design choice traces to
  a real failure. Those comments are the most valuable content here
- Printed output stays ASCII where practical; Windows consoles mangle the rest

## Never

- Commit a filled-in `.env`, a webhook URL, a callback URL, or any credential.
  `scripts/build_handoff.py` scans for these and refuses to build a bundle that
  contains one.
- Add a network call to the default test path
- Weaken a policy limit to make a scenario pass — change the scenario
- Put a customer's name, or tenant-specific values, in a committed file. Use an
  `azd` environment variable with a placeholder default.

## Pull requests

Run the checks above and state in the description what you verified rather than
what you intended. If you changed a safety property, say which negative control
proves it.

This project follows the [Microsoft Open Source Code of
Conduct](CODE_OF_CONDUCT.md).
