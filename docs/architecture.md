# Architecture

## The flow, mapped

The requested triage flow, and where each box lives in this repo.

| Flow box | Implementation | Notes |
|---|---|---|
| BI Request Inbox | `tools/inbox.py` — `MockInbox` \| `GraphInbox` | Poll or Graph subscription; same `BIRequest` either way |
| Data Quality Issue? | `consult_data_quality_agent` → `agents/data_quality_agent.py` | A separate agent, reached through a tool |
| Is There a Known Related Issue? | `signature.py` + `store/incidents.py::find_open` | 16-char signature over a normalized error |
| Wait for Resolution, Then Continue | outcome `duplicate_suppressed` | Increments the parent incident; no second remediation |
| Does It Qualify as Tier 1? | `TriageClassification.tier` | Model classifies; controller constrains what follows |
| Agentic Resolution | `refresh_powerbi_dataset` | The single allowlisted remediation |
| Is Issue Resolved? | `TriageAgent._validate_outcome` | Checks the claim against the evidence |
| Send Resolution Summary | `notify_teams` → `tools/teams.py` | Report, error, action, outcome, timestamp |
| Human Involvement | outcome `needs_human` | The branch itself is out of scope; the exit is wired |

## Run sequence

```
BIRequest
   |
   v
[runner] compute signature ---> [store] find_open(signature)
   |                                       |
   v                                       v
[TriageAgent.run]  <-------------- known incident (or None)
   |
   |  loop, each pass charged against PolicyLedger
   |
   +--> get_request_context
   +--> get_known_incidents ----------> known? -> notify -> duplicate_suppressed
   +--> consult_data_quality_agent ---> [DataQualityAgent]
   |                                        |
   |                                        +--> check_duplicates (deterministic CSV scan)
   |                                        +--> reconcile(model claim, scan evidence)
   |                                        v
   |                                    DataQualityFinding
   |
   +--> has_issue? -> write_data_quality_flag -> notify -> flagged_data_quality
   +--> else -> get_dataset_refresh_history
   +--> refresh_powerbi_dataset   [charged: 1 of 1 remediation]
   +--> notify_teams
   +--> report_resolution
   |
   v
[_validate_outcome]  downgrade any claim the evidence does not support
   |
   v
TriageResult ---> [store] record()  (redact -> dedup -> persist)
```

## Why the controller owns the loop

There are two ways to build this.

**Prompt-orchestrated.** Give the model the tools and instructions and let it
decide. Fast to build, and everything above is a suggestion. "Only take one
action" competes with every other sentence in the prompt, and loses whenever the
model's reasoning finds a good argument against it.

**Controller-orchestrated.** The model proposes; a Python loop decides whether to
dispatch. That is `PolicyLedger`:

```python
ledger.charge_llm_turn()          # max_llm_turns
ledger.charge_tokens(n)           # max_tokens
ledger.charge_tool_call(name)     # allowlist, max_tool_calls, max_write_actions
```

Each raises `PolicyViolation` rather than returning a boolean, so a forgotten
check is a failing test rather than a silent budget overrun.

### The asymmetry in how violations are handled

Not every violation should end the run:

| Kind | Handling | Why |
|---|---|---|
| `policy_blocked` | Returned to the model **as a tool result** | The agent can still escalate. Silence is the worse failure |
| `timed_out` | Propagates, ends the run | Allowance spent |
| `budget_exceeded` | Propagates, ends the run | Allowance spent |
| `max_turns_exceeded` | Propagates, ends the run | Allowance spent |

This is why `scenario3-policy-block` ends in `needs_human` with a Teams message,
rather than in a stack trace.

### Two action classes

```python
REMEDIATION_ACTIONS = {"refresh_powerbi_dataset"}                     # budgeted
REPORTING_ACTIONS   = {"write_data_quality_flag", "notify_teams",
                       "report_resolution"}                           # audited, not budgeted
```

Reporting is deliberately exempt. If posting to Teams consumed the same budget as
fixing something, the agent would go quiet exactly when it most needs to speak.

## The agent boundary

The Data Quality agent is a real agent — own provider, own prompt, own tool, own
loop — not a function on the Triage agent. The handoff is a typed
`DataQualityFinding`, so the boundary is testable without either model.

It reports; it does not decide. `recommended_action` is a recommendation. The
Triage agent owns the decision. Add a third agent later and the flow does not
change shape.

### Evidence outranks assertion

`check_duplicates` is a plain CSV scan. The model writes the sentence; the scan
produces the numbers. `_reconcile` enforces this:

```python
truth = evidence.duplicate_row_count > 0
if claimed is not None and bool(claimed) != truth:
    logger.warning("... deferring to the scan.")
```

An agent that can talk itself out of its own evidence is not deployable, and the
inverse — an agent inventing findings that aren't there — writes a false row into
a table someone acts on. Both directions are tested.

## Outcome validation

The agent's self-report is a hypothesis, not a result:

- `resolved` with no successful remediation → downgraded to `needs_human`
- `flagged_data_quality` with no positive scan → downgraded to `needs_human`
- an unrecognized outcome string → `needs_human`

A production deployment shipped an autonomous recovery agent that reported
"Fixed" three times consecutively while the underlying notebook kept failing,
because nothing compared the claim to the evidence. That is the bug this
prevents.

## Signatures and suppression

```python
signature = sha1(source | artifact_kind | artifact_name | exception_class | normalized_error)[:16]
```

Normalization strips GUIDs, timestamps, line numbers, URL paths, IPs, hex
suffixes, temp paths, long hashes and request IDs. Case is preserved — SQL
identifier case is significant in some dialects, and folding it merges genuinely
distinct failures.

`artifact_name` is in the payload on purpose: the same error class on two
different reports stays two incidents, because suppressing across unrelated
reports would hide a real second outage.

**Only open incidents suppress.** A resolved incident recurring is new
information and must be allowed to trigger action again.

**A suppressed duplicate increments its parent.** It does not write a parallel
row — that would produce one incident per alert, which is the state the signature
exists to prevent.

## The incident store

Every terminal outcome is persisted:

```
resolved · flagged_data_quality · duplicate_suppressed · needs_human
declared_failed · agent_crashed · timed_out · budget_exceeded
max_turns_exceeded · policy_blocked
```

The original production gate was `status == "fixed"`. Ten Foundry agent
crashes over two weeks left zero trace in the queue operators actually read.

`requires_investigation` is set for crashes, budget exhaustion, escalations, and
**any run containing a blocked attempt** — a refusal is a signal about the gap
between what the agent wanted and what it was allowed to do, which is precisely
the population you mine to decide what to automate next.

Redaction happens *inside* `record()`, not at call sites, so a new code path
cannot forget it.

## Providers

One interface, three implementations:

| Mode | Class | Use |
|---|---|---|
| `mock` | `ScriptedProvider` | Offline rehearsal, tests, live fallback |
| `direct` | `AzureOpenAIProvider` | Chat completions, client-side tools |
| `foundry` | `FoundryAgentProvider` | Foundry agents, both handoff shapes |

`ScriptedProvider` is a fixed state machine, not an agent, and the docstring says
so. It exists so the repo runs with nothing but pydantic installed, and so the
tests assert on orchestration rather than model output.

Foundry is reached over REST with `DefaultAzureCredential` rather than through a
client SDK. Preview SDKs churn; a demo that breaks because a package minor-bumped
the week before is a bad demo. It also puts the wire format on screen, which is
what was asked for.

## Observability

Every LLM call emits an OTel GenAI span: `gen_ai.system`, `gen_ai.request.model`,
`gen_ai.operation.name`, `agent.name`, token counts, finish reason. Tool calls
emit `tool.*` spans — that is what makes the handoff visible in a trace.

Without the OTel SDK installed, every helper is a no-op. Telemetry is not allowed
to be a hard dependency of the demo running.

**Metadata only.** No prompt or completion content. In a multi-tenant system,
content recording ingests customer data and secrets into a telemetry store with
different access controls than the source system.

## Extending it

**A new remediation**: add the tool schema to `TRIAGE_TOOLS`, add a branch to
`ToolDispatcher._execute`, add the name to `REMEDIATION_ACTIONS`, and add a
scenario. Adding a capability is a code review, not a prompt edit — which is the
property that makes the allowlist worth anything.

**A new agent**: mirror `DataQualityAgent`. Own provider, own prompt, own tools,
returns a typed model. Expose it to the orchestrator as one tool.

**A durable store**: implement `find_open`, `record`, `list_all`, `reset` against
Cosmos or SQL. Keep redaction inside `record`.

**A real flag table**: replace `DataQualityFlagTable` with three methods against
the real table. Keep the CSV path for demos — a table you can open in Excel is
easier to show than a query result.
