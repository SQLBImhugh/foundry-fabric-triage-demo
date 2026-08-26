# Build plan — BI Request Triage & Resolution demo

**Status**: Phase 1 complete (offline demo runs end to end, 90 tests green).
Phases 2–7 are tenant-dependent and not started.

**Goal**: a live, interactive demo of an agentic BI triage loop on Azure AI
Foundry, executing both requested scenarios end to end, with the ability to
pause and take questions mid-flow.

---

## Scope

### In scope

The two scenarios as specified, plus three additions that are cheap to build and
change the nature of the conversation:

| # | Scenario | Source | Why |
|---|---|---|---|
| 1 | Transient failure → Tier 1 agentic resolution | Requested | The happy path |
| 2 | Data quality issue → detect, flag, notify | Requested | Bounded, no auto-fix |
| 2b | Same alert twice → suppressed | **Added** | Their own "Known Related Issue" branch |
| 3 | Second remediation → refused by controller | **Added** | The trust conversation |
| 4 | Unlisted action → never dispatched | **Added** | Bounded blast radius |

### Explicitly out of scope

Per the requirements: Enterprise vs. non-Enterprise routing, the Enablement and
intake paths, Analytics Apps, the human-involvement branch, and any remediation
of the data quality issue itself. Also out: production hardening, customer
tenant integration, security review.

### The reframe worth raising in the room

The requirements ask for a Data Quality agent, and the stated reason is *"we
specifically want to see agent-to-agent interaction in Foundry."* That makes the
DQ agent a demonstration device rather than the business case.

What the flow actually describes — ingest a failure, gather evidence, classify,
take a bounded action, report — is a **troubleshooting and remediation loop**.
That is where the value is, and it is worth saying so directly rather than
letting them anchor on data quality as the use case.

The recommendation is not to drop the DQ agent. It is to keep it in the role it
naturally occupies: a **precondition gate** that answers "is this a data problem
or a system problem?" before any remediation is considered. That is exactly the
role `SchemaProbeAgent` plays in the production platform — a second agent that probes
real data to verify a low-confidence decision and hands a structured finding
back to the orchestrator.

Scenarios 3 and 4 exist because "can it fix things?" is answered in the first
five minutes, and "what stops it breaking things?" is what determines whether
the project proceeds.

---

## Phases

### Phase 1 — Offline demo skeleton ✅ COMPLETE

Everything runs with no tenant, no Azure login, no network.

| Deliverable | Where |
|---|---|
| Controller-enforced policy | `src/triage_demo/policy.py` |
| Failure signatures for dedup | `src/triage_demo/signature.py` |
| Secret redaction at the store boundary | `src/triage_demo/redaction.py` |
| Typed contracts for every agent boundary | `src/triage_demo/models.py` |
| Triage agent + controller loop | `src/triage_demo/agents/triage_agent.py` |
| Data Quality agent | `src/triage_demo/agents/data_quality_agent.py` |
| Tool schemas + policy-enforcing dispatcher | `src/triage_demo/tools/registry.py` |
| Deterministic duplicate detection | `src/triage_demo/tools/dataset.py` |
| Mock + live Power BI, Teams, inbox | `src/triage_demo/tools/` |
| Scripted / Azure OpenAI / Foundry providers | `src/triage_demo/providers/` |
| Incident store with occurrence counting | `src/triage_demo/store/incidents.py` |
| OTel GenAI spans with no-op fallback | `src/triage_demo/observability.py` |
| 5 scenarios with machine-checked assertions | `scenarios/` |
| CLI with live event rendering | `src/triage_demo/cli.py` |
| 90 tests | `tests/` |

**Exit criteria (met)**: all five scenarios pass their assertions offline;
`ruff check` clean; every scenario is reproducible byte-for-byte across runs.

**Effort**: ~1 engineer-day equivalent.

---

### Phase 2 — Tenant provisioning ⬜ NOT STARTED

Mostly waiting on other people. Start it first; it has the longest lead time.

| Item | Detail | Blocker risk |
|---|---|---|
| Demo tenant + subscription | Microsoft demo tenant | Low |
| AI Foundry project | Plus a model deployment | Low |
| Monitored mailbox | Shared or licensed user | Low |
| App registration | `Mail.Read` (application), admin consented | **Consent delay** |
| Power BI workspace + semantic model | Bound to the mock table | Low |
| **Power BI service principal access** | Tenant setting *"Allow service principals to use Power BI APIs"* + SP added to the workspace | **Highest risk item** |
| Teams channel + incoming webhook | Or a Graph app registration | Low |
| Data quality flag table | A table the demo can write to and show on screen | Low |

**The one to start today**: the Power BI tenant setting. It is a tenant admin
action, it is not obvious it is needed until a refresh returns 401, and it has
blocked more demos than every other item on this list combined. A managed
identity cannot be added to a Power BI workspace, so a service principal is the
path.

Full steps: [`provisioning.md`](provisioning.md).

**Exit criteria**: `triage-demo preflight` shows every live-mode row green.

**Effort**: 0.5–1 day of work, 2–5 days of calendar time for approvals.

---

### Phase 3 — Wire the live tools ⬜ NOT STARTED

Swap the mocks for real services one at a time, verifying each independently.
Interfaces already exist; this is configuration plus troubleshooting.

1. **Inbox** — `GraphInbox` polling at 30s. Measure and record actual
   inbox-to-first-action latency; they asked for the number.
2. **Power BI** — `LivePowerBIClient`. The 202-Accepted refresh does not return
   a body, so terminal state comes from polling refresh history. Verify against
   a deliberately failing refresh, not only a passing one.
3. **Teams** — `WebhookTeamsNotifier` posting an Adaptive Card.
4. **Flag table** — repoint from CSV to the real table. Keep the CSV fallback:
   showing the flag table before and after is a required demo beat, and a CSV
   opened in Excel is easier to show than a Lakehouse query.

Run `provider=mock, tools=live` first. Real side effects, deterministic
reasoning — it isolates tool problems from model problems, which otherwise
present identically.

**Exit criteria**: both scenarios pass end to end with `TRIAGE_TOOL_MODE=live`.

**Effort**: 0.5 day if Phase 2 landed clean; 1–2 days if Power BI SP access needs
escalation.

---

### Phase 4 — Foundry agents ⬜ NOT STARTED

```powershell
python scripts\register_foundry_agents.py --handoff client --dry-run
python scripts\register_foundry_agents.py --handoff client
```

Register **both** handoff shapes and show both:

- `--handoff client` — the Triage agent calls `consult_data_quality_agent` as a
  function tool; this process performs the handoff and can refuse it.
- `--handoff connected` — the DQ agent is attached as a connected agent and
  Foundry performs the handoff server-side.

Connected agents demo better. Client orchestration is what you ship, because the
ledger has to sit between the two agents to mean anything. Showing both, and
being explicit about the tradeoff, is a stronger answer than picking one.

**Known trap**: a Foundry-registered agent does not pick up local prompt or tool
changes. Re-register or the demo silently runs the previous definition. This has
bitten the production platform more than once. Put it on the pre-demo checklist.

**Exit criteria**: both scenarios pass with `TRIAGE_PROVIDER_MODE=foundry`, in
both handoff modes; the handoff is visible in the Foundry trace.

**Effort**: 0.5 day.

---

### Phase 5 — Observability ⬜ NOT STARTED

`gen_ai_span()` already emits OTel GenAI spans. Remaining work is Azure-side.

1. Set `APPLICATIONINSIGHTS_CONNECTION_STRING`; confirm spans arrive.
2. Build one workbook: per-agent latency, tokens by model, tool-call frequency.
3. Rehearse the **failed-run** view. They asked specifically how a failed run
   surfaces, and a green dashboard does not answer that question. Run
   `scenario3-policy-block` and show the resulting incident with
   `requires_investigation` set.

Spans are metadata only — no prompt or completion content. State this explicitly.
In a multi-tenant system, content recording ingests customer data into a
telemetry store with different access controls than the source system.

**Exit criteria**: a live run's spans are queryable within ~5 minutes, and a
deliberately failed run is demonstrable in App Insights.

**Effort**: 0.5 day.

---

### Phase 6 — Rehearsal ⬜ NOT STARTED

Full dry run against the live tenant, twice, on the machine and network you will
present from.

- [ ] Both scenarios end to end, live
- [ ] Scenario 2b (suppression) — needs an open incident, so ordering matters
- [ ] Scenario 3 (refusal) — the most important beat
- [ ] Reset procedure verified: `triage-demo reset` between runs
- [ ] Inbox-to-first-action latency measured and written down
- [ ] Re-register Foundry agents; confirm version numbers moved
- [ ] Screenshot fallbacks captured for every beat

**Have a fallback.** The demo runs offline with `TRIAGE_PROVIDER_MODE=mock` and
`TRIAGE_TOOL_MODE=mock`. If the tenant misbehaves mid-session, switch two env
vars and keep going rather than debugging in front of the customer.

**Exit criteria**: two consecutive clean full rehearsals.

**Effort**: 0.5 day.

---

### Phase 7 — Demo day ⬜ NOT STARTED

Run sheet: [`run-sheet.md`](run-sheet.md). Question prep: [`faq.md`](faq.md).

---

### Phase 8 — Post-demo handoff ⬜ NOT STARTED

They asked for the assets afterward. Share this repo plus:

- agent definitions — `scripts/register_foundry_agents.py --print-definitions`
- prompts — `src/triage_demo/agents/prompts/`
- tool schemas — `triage-demo tools`
- connection config — `.env.example` (never a filled-in `.env`)
- the honest effort estimate below

---

## Effort estimate

They asked directly, so answer directly.

### This demo

| Phase | Engineering | Calendar |
|---|---|---|
| 1. Offline skeleton | ~1 day | done |
| 2. Tenant provisioning | 0.5–1 day | 2–5 days (approvals) |
| 3. Live tools | 0.5–2 days | — |
| 4. Foundry agents | 0.5 day | — |
| 5. Observability | 0.5 day | — |
| 6. Rehearsal | 0.5 day | — |
| **Total** | **3.5–5 engineer-days** | **1.5–2 weeks elapsed** |

The plumbing dominates. The agents are the small part — which is itself worth
saying, because it is the opposite of what most people expect.

### Production-grade equivalent

Not a multiple of the demo. A different category of work.

| Area | What production actually requires |
|---|---|
| **Policy** | The ledger, scaled: per-tenant budgets, per-action rate limits, circuit breakers on repeated failure |
| **Secrets** | Redaction at every persistence boundary. This repo ships 11 patterns; the production platform runs the same set and it is not over-engineered |
| **Dedup** | Signature scheme, versioned, with a migration story when normalization changes |
| **Incidents** | Durable store, partitioned, indexed, rate-limited per source, with a triage queue humans actually read |
| **Auth** | Managed identity wherever possible; documented rotation for everything that cannot use it. Entra app secrets expire — a design that depends on one fails about a month after go-live |
| **Observability** | Traces, alerts that fire, and a path from alert to a durable replay fixture |
| **Regression** | Offline replay of real production runs. Prompt drift is invisible until something catches it |
| **Human-in-the-loop** | The branch this demo omits: approval gates, timeouts, escalation, audit |
| **Failure modes** | Partial failure, duplicate delivery, out-of-order alerts, poison messages, the agent itself being down |

**Weeks, not days**, and most of it is not agent work. That is the honest answer,
and it lands better than a smaller number that turns out to be wrong.

---

## Open questions

| # | Question | Owner | Needed by |
|---|---|---|---|
| 1 | Is the Power BI SP tenant setting enabled in the demo tenant? | Tenant admin | Phase 2 |
| 2 | Teams: incoming webhook, or Graph app registration? | Demo owner | Phase 2 |
| 3 | Flag table: real table or CSV on screen? | Demo owner | Phase 3 |
| 4 | Show connected-agent mode as well as client orchestration? | Presenter | Phase 4 |
| 5 | Present the Tier 2 / human-gate branch as a roadmap slide? | Presenter | Phase 7 |

---

## Decision log

| Decision | Rationale |
|---|---|
| Controller-enforced policy, not prompt-enforced | A prompt limit is a request. A ledger is a guarantee. This is the whole trust argument |
| Refusal returned as data, not raised | An agent that goes silent when blocked is worse than one that escalates |
| Deterministic duplicate detection | Reproducible on stage, and the right production shape — the model interprets, it does not measure |
| Scripted offline provider | Rehearsal without a tenant; tests that assert orchestration rather than model output |
| Foundry via REST, not SDK | Preview SDKs churn; a demo that breaks on a minor bump is a bad demo. Also puts the wire format on screen |
| Persist every terminal outcome | The original success-only gate hid 10 agent crashes over two weeks |
| Validate outcome against evidence | An agent reported "Fixed" three times while the job kept failing |
| Generic customer persona in the repo | Reusable for the next engagement; no customer name in a shared asset |
| Python | Near-direct port of the the production platform components rather than a rewrite |
