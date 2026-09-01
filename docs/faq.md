# Question prep

Their §7 asked five things directly. Those are first, then the questions that
actually get asked in the room.

---

## 1. How is agent-to-agent communication configured, and what does the handoff look like in logs?

Two shapes, both registered by `scripts/register_foundry_agents.py`.

**Connected agent** (`--handoff connected`). The Data Quality agent is attached
to the Triage agent as a tool in the Foundry control plane; Foundry performs the
handoff server-side. Least code, cleanest trace.

**Be precise about what you're showing here.** We register connected mode to
show the shape and the trace, but the demo does not *run* it, for a concrete
reason: Foundry executes a connected agent server-side, so that agent's tools
must be server-callable. Our Data Quality agent's only tool is a local CSV scan
in the calling process. Making connected mode real means exposing that scan as
an OpenAPI or Azure Function tool first.

The tradeoff is the interesting part:

> Connected agents demo better. Client orchestration is what I'd ship — because
> in connected mode our process no longer sits between the two agents, so the
> budget and the allowlist stop being enforced code and become agent
> instructions. Everything I showed you in scenarios 3 and 4 depends on being in
> the middle of that call.

**In logs**: `agent.name` on every span, `tool.consult_data_quality_agent` as a
child span, and the DQ agent's own `gen_ai.chat` spans nested under it. The
handoff is a span boundary, so the trace shows both the sequence and the timing.

---

## 2. Observability: run traces, step-level visibility, how a failed run surfaces

**Traces.** Every LLM call emits an OTel GenAI span — `gen_ai.system`,
`gen_ai.request.model`, `agent.name`, token counts, finish reason. Every tool
call emits a `tool.*` span.

**Metadata only.** No prompt or completion content. In a multi-tenant system that
would ingest customer data and secrets into a telemetry store with different
access controls than the source system. Enabling it later is a deliberate,
scoped decision — redacted, one tenant, failure paths, capped retention.

**Failed runs** are the part worth showing rather than describing. Every terminal
outcome is persisted:

```
resolved · flagged_data_quality · duplicate_suppressed · needs_human
declared_failed · agent_crashed · timed_out · budget_exceeded
max_turns_exceeded · policy_blocked
```

Run `triage-demo incidents` and show the refusal from scenario 3, flagged
`requires_investigation`.

> The system this is ported from originally recorded only successful recoveries.
> Ten agent crashes over two weeks left zero trace in the queue people actually
> read. Recording only the good outcomes means your dashboard measures your
> logging, not your system.

---

## 3. How does the email trigger work, and what's the latency?

Two mechanisms, both producing the same `BIRequest`:

| | Poll | Graph change notification |
|---|---|---|
| Mechanism | Timer reads the mailbox every N seconds | Graph POSTs to a public HTTPS endpoint |
| Latency | Half the interval on average (~15s at 30s) | Seconds to ~1 minute typical |
| Needs | Nothing | A reachable validation endpoint |
| Lifecycle | None | Renewal before expiry — max **4230 minutes (~2.9 days)** for mail |
| Failure mode | Late | **Silent** — a lapsed renewal stops delivery with no error |

The demo polls. Production is event-driven, and the renewal has to be monitored,
because the failure mode is silence rather than an error. Most teams renew at
least daily rather than waiting for the ~3-day ceiling.

Either way, the token is **app-only** (`Mail.Read` as an application permission,
admin-consented) — no signed-in user, no stored refresh token. Verified working
on a work tenant.

One thing to raise before they ask it in a security review: that permission is
**tenant-wide** unless you add an Exchange `ApplicationAccessPolicy` scoping the
app to the single alerts mailbox. We verified the unscoped case really does read
other mailboxes, so it is worth scoping on day one rather than day ninety.

Measure the real number during rehearsal and quote it rather than estimating.

---

## 4. Auth model for Power BI REST and Teams

**Power BI.** Dataset refresh needs a token the *dataset* accepts. Two things
that are easy to miss:

1. The tenant setting *"Allow service principals to use Power BI APIs"* must be
   on. It is a tenant admin action, and until it is, refresh returns a 401 whose
   message does not mention it.
2. The principal must be a member of the workspace.

A **managed identity can** be added to a Fabric/Power BI workspace, so if the
orchestrator runs in Azure, prefer MI and skip secret rotation entirely. A
service principal is the right choice when the caller is outside Azure — which
is why the demo uses one.

**Teams.** The old Office 365 connector "Incoming Webhook" was **retired on
22 May 2026**; the demo uses a **Power Automate Workflows webhook**, which takes
the same Adaptive Card payload.

There is deliberately **no app-only path** for normal channel posting — Graph
restricts the Application permission to `Teamwork.Migrate.All` (migration and
import only). Sending a normal channel message needs delegated
`ChannelMessage.Send`, a bot, or a Workflows webhook. So the asymmetry is:

| | Unattended? |
|---|---|
| Read a mailbox (`Mail.Read`, application) | Yes — verified |
| Post to a Teams channel as an app | No — migration scenarios only |

Microsoft makes it harder for an application to speak into a human conversation
than to read a mailbox. That is a defensible position, and it shapes the design:
webhook for the demo, a bot for production.

**Azure control plane.** `DefaultAzureCredential` throughout — managed identity in
Azure, developer credentials locally. No API keys anywhere.

The durable point:

> Entra app secrets expire — the lifetime is configurable, and tenant policy
> often caps it well below the old two-year default. Any design that depends on a
> long-lived client secret eventually fails at 2am. Plan the rotation, or use
> federated credentials, at design time rather than after the first outage.

---

## 5. Effort: this demo, and a production equivalent

**This demo**: 3.5–5 engineer-days, 1.5–2 weeks elapsed once approvals are
included. Roughly a day of that is the agents. The rest is auth, provisioning
and the inbox trigger.

> Which is worth noticing on its own. The agent part is small. Almost everyone
> estimates this backwards.

**Production**: weeks, and mostly not agent work — per-tenant budgets and circuit
breakers, secret redaction at every persistence boundary, a durable and indexed
incident store with a triage queue, credential rotation, alerting that fires,
replay-based regression testing for prompt drift, and the human-in-the-loop
branch scoped out of today.

Full table in [`plan.md`](plan.md#effort-estimate).

---

## Questions that actually get asked

### "How does it know it's Tier 1?"

The model classifies; the controller constrains. Classification comes from the
error, the refresh history, and the data quality finding. But the tier only
determines *which* action is proposed — whether it is permitted is decided by the
allowlist and the ledger. A misclassification cannot produce an action that
wasn't already allowed.

The prompt also says: when between two tiers, choose the higher one. Escalating
costs someone five minutes; a wrong automated action costs trust, and you get
that once.

### "What if the model hallucinates a fix?"

Scenario 4. It proposes `delete_dataset`, and the call is never dispatched —
`assert_action_allowed` runs before anything executes. The agent receives a
refusal, adapts, and completes correctly.

### "Could it delete data?"

Not through this agent. The allowlist has one remediation on it: refresh a
dataset. Adding another is a code change and a review, not a prompt edit.

### "What stops it looping?"

Four independent limits: max turns, max tool calls, max tokens, wall clock. Each
maps to a distinct terminal outcome so you can tell them apart in the queue.

### "Why not let it fix the duplicates?"

Because deduplication requires judgement this agent doesn't have. Look at the
data: two of the duplicate rows are byte-identical, and two differ by a single
value. Which one is correct? Keeping the first, the last, or the max are three
different business answers, and getting it wrong silently corrupts a production
table.

Flagging is cheap and reversible. Deduplicating is neither.

### "Does it learn from past incidents?"

Not in this build, deliberately. Incidents are never fed back into a prompt. That
keeps behaviour reproducible and stops a bad past outcome from steering future
ones. The store is for humans to read — deciding what to automate next is a human
judgement, and the incident queue is the input to it.

### "What happens if the agent itself is down?"

The alert stays in the inbox. Nothing is marked read, nothing is consumed
destructively. When the agent comes back it picks up from where it stopped. That
is a design choice worth making explicitly: a triage agent that silently drops
alerts when it crashes is worse than no agent.

### "Can we run this against our own tenant?"

Not today — production hardening, tenant integration and security review were
scoped out. What exists is the shape: the interfaces are already there and the
tool implementations swap behind them. The work that remains is the production
table in [`plan.md`](plan.md), and it is real work.

### "How do you test something non-deterministic?"

Three layers. The controller loop is tested with a scripted provider, so tests
assert on orchestration rather than model output. The deterministic pieces —
signatures, redaction, duplicate detection, policy — are tested directly. And the
every scenario runs end to end offline in CI, so a broken demo fails a test rather
than failing in front of a customer.

90 tests, no network.

### "Why is the Data Quality agent separate? It's one function."

Today, yes. Three reasons it is still separate:

1. It is the question you asked to see — agent-to-agent handoff in Foundry.
2. It reports rather than decides, so adding a third agent doesn't change the
   flow's shape.
3. In production it grows: schema drift, freshness, referential integrity, null
   rates. That belongs behind one boundary with its own prompt and its own
   evidence tools, not inlined into the orchestrator.

### "What would you build next?"

The branch scoped out today: human-in-the-loop. An agent that proposes a bounded
fix and *stops* at an approval gate covers far more of your flow than the
auto-remediation path, because most real failures aren't safe to fix
automatically. It is also the branch that makes the pattern general instead of
being a refresh button with extra steps.
