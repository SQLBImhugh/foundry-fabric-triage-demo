# BI Request Triage & Resolution — Azure AI Foundry demo

A multi-agent demo: a Power BI failure alert lands in a monitored inbox, a
**Triage agent** works it, hands off to a **Data Quality agent**, takes at most
one bounded remediation, and reports to Teams.

It runs **fully offline** with mock tools and a scripted provider, so you can
rehearse it on a laptop with no tenant, no Azure login, and no network. Point
it at a real tenant when you're ready.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

.\.venv\Scripts\triage-demo.exe list
.\.venv\Scripts\triage-demo.exe run scenario1-transient
.\.venv\Scripts\triage-demo.exe run scenario2-data-quality --show-data
```

## The point of the demo

Anyone can wire an LLM to a refresh button. The interesting question is what
stops it refreshing forty times during an outage, or deleting something because
a prompt was worded ambiguously.

So the demo has two halves. The first half is the happy path the customer
asked for. The second half is the part that decides whether they trust it:

| Scenario | What it shows |
|---|---|
| `scenario1-transient` | Tier 1 transient failure → one refresh → resolved |
| `scenario2-data-quality` | Duplicates found → flagged + notified → **no automated fix** |
| `scenario2b-known-issue` | Same alert twice → second one suppressed, occurrence counted |
| `scenario3-policy-block` | Agent tries a second remediation → **controller refuses** |
| `scenario4-unknown-action` | Agent proposes an unlisted action → **never dispatched**, agent recovers |

Scenarios 3 and 4 are the ones worth building the conversation around.

## Design decisions that carry the weight

**Limits live in the controller, not the prompt.** `PolicyLedger` charges every
turn and every tool call before it happens. One remediation per run, a tool-call
budget, a token budget, a wall clock, and an allowlist. No wording change raises
any of them.

**A refusal is data, not a crash.** When the controller blocks an action it
returns the refusal *to the agent*, which can then escalate to a human. Killing
the run at that moment would leave the operator with silence.

**Deterministic evidence beats model assertion.** Duplicate detection is a plain
CSV scan. The Data Quality agent interprets the numbers; it does not produce
them. If the model contradicts the scan, the scan wins and the disagreement is
logged — see `test_scan_overrides_a_model_that_denies_the_duplicates`.

**Success has to be earned.** If the agent reports `resolved` but no remediation
actually succeeded, the controller downgrades it to `needs_human`. This is not
hypothetical: the production platform shipped a recovery agent that reported "Fixed"
three times in a row while the underlying job kept failing.

**Every terminal outcome is persisted** — crashes, timeouts and policy blocks
included, not just successes. The same system originally recorded only
successful recoveries and consequently missed 10 agent crashes over two weeks.

**Repeats are suppressed by signature.** A 16-char hash over an error normalized
of GUIDs, timestamps and IDs. The second alert increments a counter instead of
triggering a second fix.

## Repo map

```
src/triage_demo/
  policy.py            TriagePolicy + PolicyLedger      <- the safety story
  signature.py         Failure signatures for dedup
  redaction.py         11 secret patterns, applied at the store boundary
  models.py            Typed contracts for every agent boundary
  observability.py     OTel GenAI spans (no-op without the SDK)
  prompts.py           Prompt loading + version hashing
  runner.py            Scenario orchestration
  cli.py               The on-screen surface
  agents/
    triage_agent.py    Orchestrator + controller loop
    data_quality_agent.py
    prompts/*.md       System prompts (hashed onto every incident)
  tools/
    registry.py        Tool schemas + the dispatcher that enforces policy
    dataset.py         Deterministic duplicate detection
    flags.py           The data quality flag table
    powerbi.py         Refresh via REST (mock + live)
    teams.py           Resolution summary (mock + webhook)
    inbox.py           Graph polling + mock inbox
  providers/
    mock.py            Scripted, deterministic — offline rehearsal
    azure_openai.py    Chat completions, client-side tools
    foundry.py         Foundry agents, both handoff shapes
  store/incidents.py   Dedup, occurrence counting, redaction

scenarios/*.yaml       Five runnable scenarios with assertions
mock/                  Seeded data + inbox messages
scripts/               Foundry agent registration
docs/                  Plan, architecture, run sheet, FAQ, provisioning
tests/                 90 tests, all offline
```

## Docs

| Question | File |
|---|---|
| What's the build plan, and how far along is it? | [`docs/plan.md`](docs/plan.md) |
| How does it work and why is it built this way? | [`docs/architecture.md`](docs/architecture.md) |
| How do I actually run the demo live? | [`docs/run-sheet.md`](docs/run-sheet.md) |
| What do I say when they ask X? | [`docs/faq.md`](docs/faq.md) |
| What has to exist in the tenant first? | [`docs/provisioning.md`](docs/provisioning.md) |

## Running against a real tenant

Copy `.env.example` to `.env` and fill in what you need. Two independent
switches, which can be mixed:

- `TRIAGE_PROVIDER_MODE` = `mock` | `direct` | `foundry` — who does the reasoning
- `TRIAGE_TOOL_MODE` = `mock` | `live` — whether the tools hit real services

`provider=mock, tools=live` is a genuinely useful combination: real refreshes and
real Teams messages, deterministic reasoning. Good for a dress rehearsal.

```powershell
.\.venv\Scripts\triage-demo.exe preflight     # what's configured, what's missing
python scripts\register_foundry_agents.py --dry-run
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q      # 90 tests, no network
.\.venv\Scripts\python.exe -m ruff check .
```

The scenario tests run the same code path the demo runs. If they pass, the demo
works — that is the entire reason they exist.

## Provenance

The policy ledger, failure signatures, redaction patterns, incident model and
"validate the outcome against the evidence" rule are ported from
**the production platform**, where equivalents have been running against real Microsoft
Fabric deployments. The comments call out which production incident motivated
each one; those are the details worth repeating to a customer, because they are
the ones nobody arrives at from first principles.
