# Live demo run sheet

Target: 45 minutes with interruptions. The scenarios take about 12 minutes; the
rest is questions, which is the point.

---

## Before the room

```powershell
cd <repo>
.\.venv\Scripts\triage-demo.exe preflight     # every live-mode row green
.\.venv\Scripts\triage-demo.exe reset         # clean flag table + incident store
python scripts\register_foundry_agents.py --dry-run   # must say "already in sync"
azd ai agent show bi-triage-controller        # hosted controller is running
```

- [ ] `preflight` clean for the mode you are presenting
- [ ] Foundry agents in sync — **a stale registration silently runs the old definition**
- [ ] Hosted controller responding: `azd ai agent invoke bi-triage-controller "sweep"`
- [ ] Teams channel open in a visible window
- [ ] Flag table open (Excel or the portal) showing its "before" state
- [ ] Terminal font large enough to read from the back of the room
- [ ] Foundry trace view open in a second tab
- [ ] **Fallback ready**: `TRIAGE_PROVIDER_MODE=mock`, `TRIAGE_TOOL_MODE=mock`

On the fallback: if the tenant misbehaves mid-session, change two environment
variables and keep going. Do not debug Azure in front of the customer.

**Model fallback.** If `gpt-5.6-luna` is unavailable, a `gpt-5.4` pair is
already registered and passes every scenario — see `docs/model-selection.md`:

```powershell
$env:FOUNDRY_TRIAGE_AGENT_NAME = "bi-triage-54"
$env:FOUNDRY_DQ_AGENT_NAME     = "bi-data-quality-54"
```

---

## 0b. Who these agents are (4 min) — optional opener

Worth doing first if the room contains anyone from security or identity,
because it reframes everything that follows.

```powershell
.\.venv\Scripts\triage-demo.exe identity --check-scope
```

Four panels, read left to right:

- Three **agent identities**, one per agent, created by Foundry automatically.
  `stored secrets: none` — the blueprint behind each has zero keys and zero
  passwords. They authenticate with a federated credential instead. There is
  no secret to rotate because there is no secret.
- `sponsor: Mark Hughes` — Entra records the human accountable for the agent.
  Nobody typed that in; Foundry set it at creation.
- The two **reasoning** agents show `Graph permissions: none` and no Azure
  roles at all. They think; they cannot act. The controller holds exactly two
  Azure roles — write an incident, emit a trace — and no Graph permissions.
- The fourth panel is a conventional **app registration**, shown deliberately:
  `passwords=1` and an expiry date, `sponsor: none recorded`.

> That last panel is the one thing here still holding a secret. It exists
> because mailbox access is the one door an agent identity cannot open yet —
> Graph accepts it, Exchange does not. Everything else on this screen has
> nothing to steal.

If asked how the mailbox is contained: the `mailbox scope` rows are a live
check. The agent is granted one mailbox and denied another, and the controller
**refuses to start** if it can read a mailbox it should not.

---

## 0. Frame (2 min)

> Two agents. One orchestrates, one inspects data. Two scenarios as specified,
> and then three more that I think are the ones that actually matter — because
> "can it fix things?" is answered in five minutes, and "what stops it breaking
> things?" is what decides whether you deploy it.

Show the tool surface:

```powershell
.\.venv\Scripts\triage-demo.exe tools
```

> That's the entire set of things this agent can do. Not a suggestion in a
> prompt — a list in code. Everything else is refused before it is dispatched,
> and I'll show you that.

---

## 1. Scenario 1 — transient failure (5 min)

```powershell
.\.venv\Scripts\triage-demo.exe run scenario1-transient --verbose
```

Talk over the timeline as it renders:

| When you see | Say |
|---|---|
| `get_known_incidents` | "First real decision: have we seen this before. Before any remediation is proposed, not after." |
| `consult_data_quality_agent` (`=>`) | "That arrow is the handoff. Separate agent, own prompt, own tools. It returns a typed finding, not prose." |
| `get_dataset_refresh_history` | "Two clean runs then one failure. 'Isolated, therefore safe to retry' is now a conclusion from evidence." |
| `refresh_powerbi_dataset` | "One remediation. The controller permits exactly one." |
| Policy ledger table | "Turns, tool calls, remediation writes, tokens, wall clock. All enforced in code." |

Switch to Teams. Report, error, action, outcome, timestamp.

**Pause for questions.** This is where "how does the agent know it's Tier 1?"
comes up — see [`faq.md`](faq.md).

---

## 2. Scenario 2 — data quality (6 min)

Show the source table first.

```powershell
.\.venv\Scripts\triage-demo.exe run scenario2-data-quality --show-data
```

| When you see | Say |
|---|---|
| Source table panel | "Fourteen rows. Duplicates seeded deliberately — you can see them." |
| Flag table (before) | "Empty." |
| `consult_data_quality_agent` | "Same handoff. Different answer." |
| DQ evidence panel | "Four duplicate rows, two key groups, on well_id plus production_date. The email only said 'duplicate values exist' — it did not say which rows or how many." |
| `write_data_quality_flag` | "Table, issue type, counts, detail." |
| Flag table (after) | "One row." |
| Outcome | "`flagged_data_quality`. **No remediation.** Refreshing a report does not remove duplicate source rows." |

The line worth landing:

> Those numbers come from a deterministic scan, not from the model. I can run
> this again and get exactly four. That matters more than it sounds — a number
> that moves between runs is a number nobody acts on.

And:

> It didn't attempt a fix. That's a decision, not a limitation. Deduplicating
> production data is not something I want an agent doing unsupervised, and if it
> tried, the controller would refuse it.

---

## 3. Scenario 2b — the same alert again (3 min)

> During a real outage that alert doesn't arrive once. It arrives every fifteen
> minutes for two hours.

```powershell
.\.venv\Scripts\triage-demo.exe run scenario2b-known-issue
.\.venv\Scripts\triage-demo.exe incidents
```

Note: 2b deliberately runs **without** `--keep-incidents` — it needs a clean
store so run 1 opens the incident that run 2 then matches.

| When you see | Say |
|---|---|
| Run 1 | "Flagged, as before." |
| Run 2, `get_known_incidents` → `known_related_issue=True` | "Different message id. Same failure signature." |
| Run 2 outcome | "`duplicate_suppressed`. Four tool calls instead of six. No second flag." |
| `incidents` output | "One incident. Occurrence count two. Not two incidents." |

> This is your 'Is there a known related issue?' branch. The signature is a hash
> of the error with the GUIDs, timestamps and request IDs normalized out — so
> the same failure matches itself, and a different failure doesn't.

---

## 4. Scenario 3 — the refusal (5 min) ← **the important one**

> Everything so far assumed the agent behaves. Here it doesn't.

```powershell
.\.venv\Scripts\triage-demo.exe run scenario3-policy-block --keep-incidents
```

From here on, pass `--keep-incidents` so the queue you show in section 6 still
contains this run. Each scenario uses a distinct failure signature, so they
accumulate rather than suppressing one another.

| When you see | Say |
|---|---|
| First `refresh_powerbi_dataset` | "First remediation. Permitted." |
| **REFUSED BY CONTROLLER** panel | "It asked for a second one. That limit is a Python integer, not a sentence in a prompt. No wording change raises it." |
| `notify_teams` after the refusal | "And note it still reported. The refusal is returned to the agent as data, so it can escalate rather than go silent." |
| Outcome `needs_human` | "Not a false success. It escalated." |
| Actions attempted vs dispatched | "Eight asked for, seven dispatched. The gap is the refusal." |
| Blocked attempts row | "Recorded on the incident, flagged for investigation." |

> The alternative is the one everybody has heard about: an agent that retries a
> failing refresh forty times overnight because nothing was counting.

---

## 5. Scenario 4 — outside the allowlist (3 min)

```powershell
.\.venv\Scripts\triage-demo.exe run scenario4-unknown-action --keep-incidents
```

> Here it proposes deleting the dataset.

| When you see | Say |
|---|---|
| REFUSED panel, `delete_dataset` | "Never dispatched. There is no code path from the model to an unlisted action." |
| Subsequent `refresh_powerbi_dataset` | "It adapted and did the right thing. A refusal doesn't have to mean a failed run." |
| Outcome `resolved` | "Correct outcome, bounded blast radius." |

> Adding a capability here is a code review, not a prompt edit. That's the
> property that makes the allowlist worth having.

---

## 6. Observability (5 min)

Foundry trace: show the handoff, the tool calls, the timing.

Then — deliberately — the failed run:

```powershell
.\.venv\Scripts\triage-demo.exe incidents
```

> You asked how a failed run surfaces. Here's the refusal from scenario 3,
> flagged for investigation. Every terminal outcome is recorded — crashes,
> timeouts, budget exhaustion, refusals. Not just the successes.
>
> That's a scar. The system this is ported from originally recorded only
> successful recoveries. Ten agent crashes over two weeks left no trace at all
> in the queue anyone was reading.

If App Insights is wired, show the GenAI spans and note: metadata only, no
prompt or completion content.

---

## 7. Close (5 min)

Three things to land:

1. **The agents were the easy part.** Roughly a day. The plumbing — auth,
   provisioning, the inbox trigger — was most of the effort, and that is the
   opposite of what most people expect.
2. **Production is a different category.** Weeks, and mostly not agent work:
   per-tenant budgets, secret redaction at every boundary, a durable incident
   store, credential rotation, alerting, replay-based regression testing, and
   the human-in-the-loop branch we scoped out today.
3. **The reframe.** What you've drawn is a troubleshooting-and-remediation loop.
   Data quality is the gate that decides whether remediation is appropriate at
   all. That framing generalizes past refresh failures — and refresh failures
   are the smallest version of the problem you have.

Offer: this repo, the agent definitions, the prompts, the tool schemas, the
`.env.example`, and the plan with the effort estimate.

---

## Recovery

| Problem | Action |
|---|---|
| Refresh returns 401/403 | Power BI SP tenant setting or workspace membership. Switch to `TRIAGE_TOOL_MODE=mock`, move on |
| Foundry call fails | `TRIAGE_PROVIDER_MODE=mock`. Narrative is unchanged |
| Teams post fails | `TEAMS_MODE` unset → mock notifier renders the card in the terminal |
| Suppression doesn't fire | An earlier run left no *open* incident. `triage-demo reset`, re-run scenario 2b whole |
| Incident queue looks empty in section 6 | A later `run` cleared it. Re-run scenario 3, then use `--keep-incidents` on anything after it |
| Agent behaves oddly in Foundry mode | Almost certainly a stale registration. `register_foundry_agents.py --dry-run` |
| Everything is on fire | Both modes to `mock`. Every scenario still runs, offline, in seconds |
