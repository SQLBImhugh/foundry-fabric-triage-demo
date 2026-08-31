# Build plan — BI Request Triage & Resolution demo

**Status**: Deployed and rehearsed. The controller runs as a
Foundry hosted agent with its own Entra agent identity, reading a real mailbox
and acting on a real Power BI dataset. Live email ingestion is proven end to
end. The Foundry routine that should trigger it on a schedule is registered and
enabled but does not fire — a preview defect, documented in
`docs/hosted-architecture.md`; any external scheduler calling the agent endpoint
gives unattended operation today.
287 tests green and still fully offline; the mock path remains the rehearsal
fallback. See `docs/hosted-architecture.md` for what each identity can actually
reach — including the things that only surfaced by trying them.

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
role `SchemaProbeAgent` plays in the production platform this is ported from — a
second agent that probes real data to verify a low-confidence decision and hands
a structured finding back to the orchestrator.

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
| 7 scenarios with machine-checked assertions | `scenarios/` |
| CLI with live event rendering | `src/triage_demo/cli.py` |
| 287 tests | `tests/` |

**Exit criteria (met)**: all five scenarios pass their assertions offline;
`ruff check` clean; every scenario produces the same outcome, the same tool
sequence and the same evidence numbers on every run. (Timestamps and generated
ids do vary — the *decisions* are reproducible, not the bytes.)

**Effort**: ~1 engineer-day equivalent, plus a review pass.

**Post-build review**: a rubber-duck pass found 14 issues; the material ones are
fixed and pinned by regression tests in `tests/test_hardening.py`. Summary in
[Review findings](#review-findings) below.

---

### Phase 2 — Tenant provisioning ✅ COMPLETE

| Item | Delivered |
|---|---|
| Foundry project | `bitriage-foundry-eus` / `bi-request-triage` (East US) |
| Model deployment | `gpt-5.6-luna`, plus `gpt-5.4` as a measured fallback |
| Monitored mailbox | A real user mailbox, scoped to this app alone |
| App registration | `Mail.Read` (application), admin consented |
| Power BI workspace | `BI Triage Demo`, on Fabric capacity |
| Semantic model | Points at a SQL host that does not resolve, so refresh genuinely fails |
| Storage account | Durable incident table |
| Application Insights | Connected to the project |

**Correction to the original plan.** It stated that a managed identity cannot be
added to a Power BI workspace, so a service principal was the only path. That is
wrong, and it was verified wrong: a managed identity *can* be added, and so can
an Entra **agent identity** — which is what the deployed controller actually
uses, with no secret at all.

**The mailbox is the item that needed care, not Power BI.** App-only `Mail.Read`
is tenant-wide by default. An Exchange `ApplicationAccessPolicy` confines it to
one mailbox, and the controller refuses to start if it can read a mailbox it
should not.

**Exit criteria (met)**: `triage-demo preflight` green; `triage-demo identity
--check-scope` shows the mailbox boundary enforced.

---

### Phase 3 — Wire the live tools ✅ COMPLETE (flag table deliberately still CSV)

1. **Inbox** — `GraphInbox`, live. Gained a **relevance filter** that was not in
   the original plan and turned out to be a security control rather than
   housekeeping: an agent that acts on every message is steerable by anyone who
   can email it. Unfiltered against a real mailbox it triaged security digests
   and crashed on several.
2. **Power BI** — `LivePowerBIClient`, live, against a model whose refresh
   genuinely fails. The plan's instinct to verify against a *failing* refresh
   rather than a passing one was the right one: the failure is
   `ModelRefreshFailed_CredentialsNotSpecified`, which is unrecoverable by
   retry, and the agent correctly declines to retry and escalates instead.
3. **Teams** — live. The agent posts an Adaptive Card to **Data Platform
   Operations → BI Alerts** over a Power Automate Workflows webhook. Office 365
   connector webhooks were retired on 22 May 2026 and there is no
   application-permission path for posting to a channel, so the webhook has to
   be created by hand from the channel — the one manual step in the whole
   deployment. Posts appear as the Workflows bot rather than a custom name.
4. **Flag table** — still CSV, deliberately. A CSV opened in Excel is easier to
   show on screen than a Lakehouse query, and the before/after is a required
   demo beat.

**Exit criteria (met)**: scenarios pass with `TRIAGE_TOOL_MODE=live`, and the
deployed controller reads real refresh history from the real dataset.

---

### Phase 4 — Foundry agents ✅ COMPLETE (design changed)

The original two-handoff plan did not survive contact with the platform.

- **Connected agents are now "(classic)"** and cannot call local functions.
- **`a2a_preview` cannot call a prompt agent.** It fetches an agent card first;
  a prompt agent publishes none, and the call fails at card fetch with a
  misleading 401. The callee must be a *hosted* agent speaking `a2a`.

So the shipped design is the `responses` handoff: the orchestrator invokes the
DQ agent over `/openai/v1/responses`, which keeps the policy ledger between the
two agents. That was always the shape worth shipping — the ledger has to sit in
the middle to mean anything — but it is now the only one that works, rather than
one of two options.

**Known trap, still true**: a Foundry-registered agent does not pick up local
prompt or tool changes. Re-register or the demo silently runs the previous
definition. It is on the pre-demo checklist.

**Exit criteria (met)**: 7/7 scenarios pass with `TRIAGE_PROVIDER_MODE=foundry`.

---

### Phase 5 — Observability ✅ COMPLETE

`APPLICATIONINSIGHTS_CONNECTION_STRING` is set and spans arrive. One thing the
plan did not anticipate: the hosted container needs
**`Monitoring Metrics Publisher`** on the App Insights resource, or the log
floods with a 403 every second and drowns everything useful.

Spans remain metadata only — no prompt or completion content.

**Exit criteria (met)**: live spans queryable; a deliberately failed run is
demonstrable, and `requires_investigation` is set on it.

---

### Phase 6 — Hosted deployment and agent identity ✅ COMPLETE

**Not in the original plan at all.** It turns the demo from a laptop script into
something that could actually be operated, and it became the strongest part of
the story.

| Deliverable | Where |
|---|---|
| Controller as a Foundry hosted agent | `src/app.py`, `azure.yaml` |
| Scheduled trigger | Foundry routine, `bi-triage-schedule` — **registered, does not fire** |
| Durable incident store | `src/triage_demo/store/azure_table.py` |
| Inbox relevance filter | `src/triage_demo/tools/mail_filter.py` |
| Agent identity inspection | `src/triage_demo/identity.py`, `triage-demo identity` |
| Model chosen by measurement | `scripts/compare_models.py`, `docs/model-selection.md` |

**The routine does not work, and that is a platform issue rather than a
configuration one.** It is registered with a five-minute cron, the API reports
`enabled: true`, and `azd ai routine dispatch` returns dispatch and task ids.
It has never invoked the agent: zero entries in `routine run list` over three
days, and after a dispatch the container logs nothing at all. `azd ai routine
list` also returns `null` for a routine that `routine show` returns in full,
which suggests the preview's registration and execution paths disagree about
what exists.

Nothing downstream depends on it. The scheduled path and the interactive path
are the same code, so any external scheduler that can call the agent endpoint
gives unattended operation today. Worth re-testing when routines leave preview.

What the tenant actually does, verified rather than assumed:

- Foundry **creates an Entra agent identity per agent, automatically**, with a
  blueprint holding **zero keys and zero passwords** and a federated credential
  instead. There is no secret to rotate because there is no secret.
- Entra records a **sponsor** — the accountable human — at creation.
- **Exchange rejects agent identities** for app-only mailbox access (401), while
  Graph's directory endpoint accepts the same token (200) and Power BI accepts
  the identity as a workspace principal. So one conventional app registration
  remains, for mail only, and it is the only credential in the deployment.
- The container federates a **workload identity**, not an IMDS managed identity.

Full detail, including the things that only surfaced by trying them:
[`hosted-architecture.md`](hosted-architecture.md).

**Exit criteria (met)**: an incident written by the container in Azure is
readable from another machine, which proves it authenticated as itself.

---

### Phase 7 — Rehearsal ✅ COMPLETE

- [x] All 7 scenarios pass live, and offline
- [x] Suppression, refusal, and both approval directions rehearsed
- [x] Reset procedure verified, including the durable table
- [x] Customer walkthrough captured — `walkthrough/WALKTHROUGH.html`
- [x] Offline fallback verified: two env vars and the demo keeps running
- [x] Model fallback registered and passing, needing no provisioning
- [x] **Two consecutive clean rehearsals** — `python scripts/rehearse.py`

**The rehearsal earned its place immediately.** The first attempt was clean on
run one and failed two scenarios on run two, which is exactly the failure a
single rehearsal cannot find. `python scripts/flake_check.py` then measured it:
`scenario2b` was 5/5, but `scenario4-unknown-action` failed **2 runs in 5**.

The cause was ours, not the model's. When the controller refused an action, the
guidance returned to the agent said, for every kind of refusal:

> "You may not perform this action. Report the situation to a human ... outcome
> 'needs_human'."

That is correct when the remediation budget is exhausted and wrong when the
action simply was not on the allowlist — in that case the budget is untouched
and the agent should adapt. The model was following the instruction; the
instruction was wrong. The refusal is now budget-aware and names the permitted
actions, and scenario 4 went to **6/6**.

Outstanding, and honestly small:

- [x] A genuine Power BI alert delivered to the monitored mailbox and found by
      the agent unaided. Proven: the filter ignored 9 unrelated messages, found
      the one real alert, matched two playbooks and recorded the incident.
- [x] Inbox-to-first-action measured. **~47 s** from the agent starting a sweep
      to a recorded outcome, with first response at ~11 s. Add the trigger
      interval to that for the real end-to-end number — which currently means
      whatever external scheduler you use, since the Foundry routine does not
      fire.
- [x] Teams delivery. Wired and verified: the agent posts a real Adaptive Card
      to **Data Platform Operations → BI Alerts** over a Power Automate
      Workflows webhook. The walkthroughs now show the real post rather than a
      rendering.

---

### Phase 8 — Demo day ⬜ NOT STARTED

Run sheet: [`run-sheet.md`](run-sheet.md). Question prep: [`faq.md`](faq.md).

Open with `triage-demo identity --check-scope` if anyone from security or
identity is in the room; it reframes everything that follows.

**Teams is wired — confirm it still delivers.** The agent posts to
**Data Platform Operations → BI Alerts**. A webhook can be deleted or its flow
disabled without anything else changing, so run one scenario before the session
rather than assuming. If it has stopped, recreate it from the channel and
`python scripts\setup_teams_webhook.py --set-url "<url>"`.

---

### Phase 9 — Post-demo handoff ✅ READY

```powershell
python scripts\build_handoff.py
```

**Two walkthroughs ship, not one.** `WALKTHROUGH.html` explains the architecture;
`PERSONAS.html` tells the same runs through the analyst who reads the report and
the engineer who gets paged. The second is the one to open first if the room is
more business than technical, and the one to send to people who were not there.

**Not done: persona accounts in the tenant.** The persona document uses named
roles rather than real users. Creating users and assigning licences needs
Microsoft Graph write access, which was blocked throughout by a
continuous-access-evaluation challenge (`InteractionRequired`,
`TokenCreatedWithOutdatedPolicies`) that only an interactive `az login` clears.
The document is built so it does not need them — every screenshot in it is a real
captured run — but real accounts would be the prerequisite for avatars in Teams.

Generates rather than curates, so the bundle cannot drift from the code, and
refuses to ship if it finds a credential. Contents and the framing to use when
handing it over: [`handoff.md`](handoff.md).

The credential scanner shipped broken and was caught by testing it rather than
trusting it: a leading word boundary meant `GRAPH_CLIENT_SECRET=...` never
matched, so it reported "clean" while being incapable of detecting the most
likely leak. `tests/test_handoff_scanner.py` now pins both directions — real
secrets detected, benign mentions ignored.

---

## Effort estimate

They asked directly, so answer directly.

### This demo

| Phase | Engineering | Status |
|---|---|---|
| 1. Offline skeleton | ~1 day | done |
| 2. Tenant provisioning | ~0.5 day | done |
| 3. Live tools | ~1 day | done |
| 4. Foundry agents | ~0.5 day | done |
| 5. Observability | ~0.5 day | done |
| 6. Hosted deployment + agent identity | ~1.5 days | done |
| 7. Rehearsal | 0.5 day | outstanding |
| **Total** | **~5.5 engineer-days** | |

The plumbing dominates. The agents are the small part — which is itself worth
saying, because it is the opposite of what most people expect.

Phase 6 was not in the original estimate and came in around 1.5 days. Most of
that was not writing code: it was establishing which Microsoft services accept
an Entra agent identity and which do not, by trying them. That is the cost
nobody budgets for, and it is the reason
[`hosted-architecture.md`](hosted-architecture.md) exists — so the next project
pays it once rather than again.

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

## Review findings

A rubber-duck review was run over the completed Phase 1 build. It raised 14
blocking and 4 non-blocking issues. Recording them here rather than quietly
fixing them, because several are worth saying out loud to the customer — they
are exactly the class of thing that separates a demo from a system.

### Fixed, with regression tests

| Finding | Why it mattered |
|---|---|
| Data Quality agent returned `has_issue=True` alongside the model's "looks fine to me" | A self-contradicting sentence would have gone into a Teams message on stage |
| `flagged_data_quality` was accepted without a flag row ever being written | The agent could report an issue as flagged when nothing was recorded |
| `duplicate_suppressed` was accepted with no matching open incident | Suppression with nothing to point at, and it created a fake open incident that would then suppress everything after it |
| A zero-duplicate flag row could be written | Poisons the table someone triages |
| DQ agent turns and tokens bypassed the ledger entirely | The central "policy covers the run" claim was only true of the orchestrator. A second agent could burn unbounded turns while reported totals stayed small |
| Scenarios 3 and 4 used exactly 8 of 8 turns | One extra step would have failed the demo with `max_turns_exceeded` for a reason unrelated to the point being made. Limits now cover all agents, with enforced headroom |
| A resolved incident kept `status=open` and went on suppressing | A fixed issue would silently swallow every recurrence |
| Live Power BI client polled `$top=1` | Could report a *previous* refresh as this run's success |
| Teams delivery failures reported success | A run claiming it told a human when it did not |
| Teams title/action/outcome fields skipped redaction | Redaction was claimed as a boundary property but had holes |
| Redaction missed Cosmos keys, Basic auth, camelCase JSON secrets, quoted SQL passwords, URL-encoded SAS | All realistic in Power BI / Azure error payloads |
| `configure_telemetry` had no caller | Tracing was wired in the code and dead in the process |
| Refused actions were invisible in the tool-call count | The refusal is the interesting part; it should not be the hidden part |
| Already-triaged mail was tracked in a set on the inbox instance | A hosted agent is rebuilt per invocation, so the set was always empty and every scheduled sweep re-triaged the whole mailbox. Found in the tenant, not in review: two unread alerts and a five-minute routine produced ~24 Teams cards an hour and drove one incident to 130 occurrences |
| Every suppressed duplicate still posted a card | Dedup stopped the remediation but not the notification, so a recurring failure produced exactly the alert fatigue the system claims to remove. The controller now announces an incident once and counts the rest |
| Run sheet's own reset order destroyed the evidence it told you to show | Added `--keep-incidents` |

### Documentation corrections

Two of our claims were wrong, and one of the review's corrections was wrong. All
three were checked against current sources rather than argued from memory.

| Claim | Verdict |
|---|---|
| "Use a Teams Incoming Webhook (Channel → Connectors)" | **Wrong.** Office 365 connectors were retired 22 May 2026. Now uses a Power Automate Workflows webhook |
| "A managed identity cannot be added to a Power BI workspace" | **Wrong.** It can, like any other principal. Corrected, with the SP-vs-MI tradeoff stated |
| "Graph mail subscriptions renew roughly every 3 days" | **Correct** — the ceiling is 4230 minutes (~2.9 days). The review's "under seven days" was wrong; kept ours, added the exact figure |
| "Entra secrets expire about a month after go-live" | Overstated — lifetime is configurable and policy-dependent. Softened |
| "Reproducible byte-for-byte" | Overstated — timestamps and generated ids vary. Now says decisions and evidence are reproducible |

### Accepted as out of scope, with rationale

These are correct criticisms of a *production* system and wrong criticisms of a
demo. They belong in the production table above, not in Phase 1.

| Finding | Why it is deferred |
|---|---|
| Dedup lookup and record are not an atomic claim, so two concurrent identical alerts could both remediate | The demo is single-process and sequential. In production this needs a lease or a unique-constraint claim per signature — it is listed under **Policy** and **Dedup** in the production table |
| Tier-1 prerequisites (known-incident check, DQ consult, history review) are enforced by the prompt, not the dispatcher | Deliberate: the model classifies, the controller constrains *which actions are permitted*. A misclassification cannot produce an action that was not already allowed. Making the ordering itself deterministic is a reasonable production hardening step and a defensible design conversation to have in the room |
| No `watch` command consuming a live inbox; `GraphInbox` is unreachable from the CLI | Phase 3 work. Scenarios deliberately load a fixed message so they stay reproducible |
| Wall-clock is checked between steps, so a hung provider is never interrupted mid-call | Needs a per-await timeout wrapper. Real, and listed under **Failure modes** in the production table |
| Connected-agent mode is not end-to-end runnable | Documented explicitly rather than fixed — see Phase 4. Requires exposing the scan as a server-callable tool |

---


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
| Python | Near-direct port of the production platform's components rather than a rewrite |
| Controller hosted in Foundry, not a Container App | Most Foundry-native option, and it gets an Entra agent identity for free. Also unblocked the routine trigger |
| One code path for scheduled and interactive runs | A demo path that differs from the production path eventually demonstrates something that does not exist |
| Incidents in Azure Tables, not a file | A container's filesystem goes away on recycle. Incident state is what stops the agent remediating the same failure twice after a restart |
| Agent identity everywhere it works; one app registration for mail | Verified: Graph directory and Power BI accept an agent identity, Exchange does not. Showing the one remaining secret is a stronger argument than hiding it |
| Inbox relevance filter, failing closed | An agent that acts on every message is steerable by anyone who can email it. Unfiltered it triaged security digests and crashed |
| Model chosen by running all seven scenarios | Both passed; the chosen one was faster and needed one fewer controller correction. A preference backed by a number survives a challenge |
| Both model pairs left registered | The fallback needs no provisioning under time pressure |
