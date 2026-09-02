# Operations

Running it after the demo is over: what runs on a schedule, how to turn each
part off, and what to look at when it behaves unexpectedly.

For first-time setup see [`provisioning.md`](provisioning.md). For the live
demo script see [`run-sheet.md`](../demo/run-sheet.md).

## What runs, and when

| Trigger | What it does | Off switch |
|---|---|---|
| New mail in the monitored mailbox | Filters, triages, acts or escalates | Disable the routine in the Foundry portal, or unset `GRAPH_MAILBOX` |
| Scheduled sweep | Drains due retries, runs the silent-failure scan | `SILENT_SWEEP_ENABLED=false` |
| Approval reply | Applies or abandons a proposed Tier 2 action | Unset `APPROVAL_CALLBACK_URL` — with no gate configured, every gated action is refused |

**Disable routines after deploying, not before.** `azd deploy` re-upserts every
routine declared in `azure.yaml` and will silently re-enable one you turned off
in the portal. Deploy, then disable.

**The silent-sweep off switch is configuration, not routine state**, for the same
reason. A deploy resets routine state; it does not reset an environment variable
you set deliberately.

## Budgets

Each run is bounded. These are charged by the controller before an action, so no
prompt wording raises them.

| Setting | Default | What it bounds |
|---|---|---|
| `TRIAGE_MAX_LLM_TURNS` | 14 | Reasoning turns per incident |
| `TRIAGE_MAX_TOOL_CALLS` | 20 | Tool calls per incident |
| `TRIAGE_MAX_WRITE_ACTIONS` | 1 | Remediations per incident |
| `TRIAGE_MAX_TOKENS` | 80,000 | Tokens per incident, across every agent |
| `TRIAGE_TIMEOUT_SECONDS` | 300 | Wall clock per incident |

Raising `TRIAGE_MAX_WRITE_ACTIONS` above 1 removes the property that most of the
safety argument rests on. If a scenario seems to need it, the action is probably
mis-tiered — a Tier 2 action needing a second step should be a single tool that
does both, so it is approved once with its real blast radius stated.

## Inspecting state

```powershell
triage-demo incidents            # what was seen, its signature, occurrence count
triage-demo approvals            # actions awaiting a human decision
triage-demo retries              # postponed retries and when they are due
triage-demo retries --drain      # perform the ones whose window has passed
triage-demo health               # scan for failures that raised no alert
triage-demo health --probes      # what is watched, and how
triage-demo health --baselines   # what healthy looked like last time
triage-demo health --preflight   # configuration that would silently detect nothing
triage-demo health --accept all  # accept a planned change as the new normal
triage-demo flags                # data quality findings, reported not fixed
triage-demo preflight            # configured vs missing, printing no secret values
```

Everything printed is already redacted: redaction happens inside the store
boundary, so a display path cannot forget it.

## When it behaves unexpectedly

**It did nothing when mail arrived.** The inbox filter is a security control and
fails closed. Check the sender against `GRAPH_SENDER_ALLOWLIST` and the subject
against `GRAPH_SUBJECT_PATTERN`. The run reports what it ignored and why, rather
than dropping it silently. Do not widen the filter to make it find something —
send a message that matches. An agent that acts on every message is steerable by
anyone who can email it.

**It triaged, but took no action.** Expected for anything above Tier 1. Check
`triage-demo approvals`: a Tier 2 action waits for an explicit human yes, and
timeout, error, malformed reply and no-gate-configured all read as a decline.

**It reported `needs_human` when it looked successful.** Outcome validation
downgraded it: the agent claimed a result the evidence does not support. The
incident records the claim and the contradiction.

**The same alert produced no second action.** Signature suppression. The second
occurrence increments a counter. Notification is deduplicated too — an incident
is announced once, not once per occurrence.

**A refresh was not attempted during a capacity incident.** Deliberate. A
throttled retry is postponed with exponential backoff, capped at three attempts,
rather than retried immediately and made worse. `triage-demo retries` shows when.

**The hosted agent starts and immediately fails.** Check the environment
variables it was deployed with. pydantic-settings JSON-decodes complex field
types in the environment source *before* any validator runs, so a malformed
value for such a field crashes the process at import — taking down mail triage,
approvals and remediation over one optional feature. This is why
`SILENT_HEALTH_PROBES` is typed `str` and parsed afterwards. Keep new
configuration fields simple for the same reason.

**A prompt or tool change had no effect.** Foundry-registered agents do not pick
up local changes. Re-register:

```powershell
python scripts\register_foundry_agents.py
```

## Verifying a deployment actually deployed

```powershell
azd deploy bi-triage-controller --no-prompt
azd ai agent invoke bi-triage-controller "sweep"
azd ai agent monitor bi-triage-controller
```

A deploy that finishes in ~25 seconds instead of the usual minute and a half
detected no source change and shipped nothing. Exit code 0 is not proof; invoke
it and read the result.

## Telemetry

With `APPLICATIONINSIGHTS_CONNECTION_STRING` set, each run emits OpenTelemetry
GenAI spans: the incident, each agent, each tool call, the policy decisions and
the terminal outcome.

Spans carry **metadata only** — never prompt or completion content. Traces are
retained and widely readable inside a tenant, and prompt content routinely
contains customer data pasted into an alert.

Without the connection string the instrumentation is a no-op, so nothing needs
disabling to run offline.

## Cost control

Model tokens dominate. The per-run token budget is the direct control: it bounds
the cost of a single incident, and signature suppression bounds how many times
the same failure can be paid for.

If cost rises unexpectedly, look for a signature that is not matching — a
failure whose error text varies on every occurrence defeats deduplication and
gets triaged from scratch each time. `triage-demo incidents` shows occurrence
counts; many near-identical incidents with a count of 1 is the symptom.
