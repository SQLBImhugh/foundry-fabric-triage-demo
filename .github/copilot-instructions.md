# Copilot instructions — BI Request Triage & Resolution demo

The full contract for agents working in this repo is [`AGENTS.md`](../AGENTS.md).
This file carries the parts you need before touching anything, so you do not
have to read a second file to avoid breaking something.

## What this is

A multi-agent BI triage loop on Azure AI Foundry: a Power BI refresh fails, an
alert arrives by email, and a controller gathers evidence, consults a data
quality agent, and remediates within policy — or refuses to.

It is a **customer-facing demonstration asset**, not a product. Optimise for
legibility and for being able to explain any line of it out loud, over
cleverness. Assume every comment may be read aloud to a customer.

It runs **fully offline** with mock providers and mock tools. Keep it that way:
that is the rehearsal path and the demo-day fallback.

This project was built with the **microsoft-foundry** skill. Read that skill
before working on Foundry agents, routines, deployment or agent identity — the
platform diverges from its documentation in several places, and
`docs/hosted-architecture.md` records the ones this repo has already paid for.

## Commands

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

.\.venv\Scripts\python.exe -m pytest -q                  # the offline suite -- no network
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\triage-demo.exe run scenario1-transient
.\.venv\Scripts\triage-demo.exe identity --check-scope   # who the agents are

azd deploy bi-triage-controller --no-prompt              # hosted controller
azd ai agent invoke bi-triage-controller "sweep"
azd ai agent monitor bi-triage-controller
```

## Rules that are not negotiable

1. **Tests never touch the network.** No live calls, no credentials, no Azure. A
   test that needs a tenant is not a test.
2. **Policy is enforced in the controller, not the prompt.** New limits go in
   `PolicyLedger` with a test proving they fire. A limit that exists only as
   prompt wording is not a limit.
3. **Every tool is on an allowlist** (`REMEDIATION_ACTIONS`, `REPORTING_ACTIONS`
   or `DIAGNOSTIC_ACTIONS`). Anything else is refused before dispatch. That is
   the property the whole demo rests on.
4. **An approval is only a yes if it is explicit, fingerprint-matched,
   unexpired and unused.** Everything else -- timeout, error, malformed reply,
   no gate configured -- is a no. Silence never reads as consent.
5. **A denial must not consume the remediation budget.** Otherwise one "no"
   silently disarms the agent for the rest of the incident.
6. **Deterministic evidence outranks model output.** If a model claim and a scan
   disagree, the scan wins and the disagreement is logged.
7. **Redaction stays inside the store boundary.** Never move it to call sites; a
   call site can forget.
8. **Every terminal outcome is persisted**, including crashes and refusals.
9. **Spans carry metadata only.** Never attach prompt or completion content.
10. **The inbox filter is a security control.** An agent that acts on every
   message is steerable by anyone who can email it. Never widen the filter to
   make the demo find something — send a matching message instead.
11. **Identity claims are read from the directory, never configured.** A
   hardcoded claim about security posture will eventually be false silently.
12. **Grant permissions to the component that acts, not the one that reasons.**
    The prompt agents hold nothing. If a reasoning agent seems to need a
    permission, the design is wrong.
13. **Tools fail loudly rather than return something interpretable.** Calling
    Power BI with an empty id returned 404, and the model turned that into a
    confident, wrong conclusion.
14. **Scenarios are reproducible.** Same input, same tool sequence, same
    numbers. When a live model and the mock diverge, make the *controller*
    decide so both agree.
15. **Never weaken a policy limit to make a scenario pass** — change the
    scenario.
16. **The walkthrough pages stay self-contained.** The SharePoint/Teams preview
    drops external CSS and images without reporting it. After changing a
    screenshot or the stylesheet, run `demo/scripts/inline_assets.py`.
17. **State that must survive an invocation goes in a store, never on an
    object.** A hosted agent is rebuilt for every request, so an instance
    attribute is always empty on arrival.
18. **An incident is announced once, not once per occurrence.** Enforced in the
    controller against `notified_count`, never in prompt wording.

## Style

- `from __future__ import annotations` at the top of every module
- `str | None`, not `Optional[str]`
- Pydantic models for anything crossing an agent boundary
- Per-module loggers: `logging.getLogger("triage.<module>")`
- Comment the **why**, not the what — especially where a design choice traces to
  a real production failure. Those comments are the most valuable content in the
  repo and are quoted verbatim to customers
- Printed output stays ASCII where practical; Windows consoles mangle the rest

## Adding things

- **A remediation tool**: schema in `TRIAGE_TOOLS` → branch in
  `ToolDispatcher._execute` → name in an action allowlist → a scenario → a test.
- **An agent**: mirror `DataQualityAgent`. Own provider, prompt and tools,
  returns a typed Pydantic model, exposed to the orchestrator as one tool. It
  reports; the orchestrator decides.
- **A scenario**: a YAML in `scenarios/` with an `expect` block, added to the
  parametrized list in `tests/test_scenarios.py`. The `expect` block is the test.
- **A prompt change**: prompts are hashed onto every incident. If running in
  Foundry mode, **re-register the agents** or the change has no effect.

## Never

- Commit a filled-in `.env`, a webhook URL, or any credential
- Add a network call to the default test path
- Put a customer's name, or an internal project name, in a committed file
- Claim a capability without verifying it against the tenant first
