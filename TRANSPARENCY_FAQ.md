# Transparency FAQ

This accelerator uses generative AI to triage Power BI refresh failures and, in
bounded cases, to remediate them. The answers below describe what it does, what
it cannot do, and where a human stays in the loop.

## What is this system?

A multi-agent loop on Azure AI Foundry. A Power BI refresh fails, an alert
arrives in a monitored mailbox, and the system gathers evidence, classifies the
failure, and either performs one bounded remediation or escalates to a person.
It also runs a deterministic detector for failures that never raise an alert.

## What can it do?

| Capability | Bound |
|---|---|
| Read a monitored mailbox | `Mail.Read` only, scoped to one mailbox by an Exchange application access policy. It cannot send mail or modify any mailbox. |
| Read Power BI refresh history, schedules and semantic model measurements | Read-only. |
| Trigger one dataset refresh | At most one remediation per run, enforced in code. |
| Rebind a dataset gateway | Requires explicit human approval. |
| Re-enable a disabled refresh schedule | Requires explicit human approval, and is refused unless the most recent refresh succeeded. |
| Postpone a retry during capacity throttling | Writes a scheduling record; touches nothing in Power BI. |
| Post to one Teams channel | Via an outbound webhook. It cannot read the channel. |

Anything not on an action allowlist is refused before dispatch. Adding a
capability is a code change and a review, not a prompt edit.

## Does it use generative AI?

Yes, for two things: classifying a failure and writing the human-readable
explanation. It does not use a model to decide whether an action is permitted.

Two prompt agents run on an Azure OpenAI model deployment in your own Foundry
project. A separate controller — ordinary Python — enforces every limit. The
prompt agents hold no permissions of their own.

## What decisions does a model make, and what does it not?

| Made by a model | Made deterministically in code |
|---|---|
| Which failure class this looks like | Whether an action is on the allowlist |
| Which permitted action to propose | Whether the remediation budget allows it |
| The wording of the summary posted to Teams | Whether a human approved it |
| | Whether a duplicate incident already exists |
| | Whether data is stale, or a row count collapsed |
| | Whether the claimed outcome matches the evidence |

If a model claim and a measurement disagree, the measurement wins and the
disagreement is recorded on the incident.

## What data does it process?

- Subject and body of messages in the monitored mailbox that match the sender
  and subject filter. Everything else is counted and ignored.
- Power BI refresh history, refresh schedules, and scalar measurements — a
  maximum date, a row count, named measure totals. It does not read detail rows.
- Optionally, a local CSV extract for duplicate detection in the demo path.

## Where is data stored, and for how long?

Incidents, approvals, processed-message identifiers, deferred retries and health
baselines are written to Azure Table Storage in your own subscription, with no
automatic expiry. You control retention.

Error text and diagnostic strings are redacted at the storage boundary — not at
call sites, which can be forgotten — against patterns covering connection
strings, keys, tokens, SAS URLs and passwords. Traces carry metadata only, never
prompt or completion content.

## When is a human involved?

Always, for any action whose consequence extends beyond the failing dataset.
Today that is a gateway rebind and a schedule re-enable.

An approval is honoured only if it is explicit, matches a fingerprint of the
exact action *and its arguments*, has not expired, and has not already been
used. A timeout, a transport error, a malformed reply, a mismatched fingerprint,
or no approval channel configured at all are all treated the same way: not
approved. Silence never reads as consent.

## What are the known limitations?

- **Failure classes are finite.** The system recognises nine documented Power BI
  failure modes and can act on three. Anything else is escalated with evidence.
- **The mailbox filter is a security control.** An agent that acts on every
  message is steerable by anyone who can email it. The filter fails closed. Do
  not widen it to make a demo find something.
- **Model output can be wrong.** That is why remediation is allowlisted,
  budgeted, approval-gated and validated against measurements rather than
  trusted.
- **The silent-failure detector needs configuration.** "Fresh" is a business
  question that differs per model. An unconfigured detector watches nothing,
  deliberately.
- **App-only `executeQueries` does not support datasets with row-level security
  or single sign-on.** Those are reported as detector faults, never as data
  findings.
- **Foundry hosted agents and routines are preview features.** The scheduled
  routine has been observed to register and enable without ever firing; the
  system is driven by an external scheduler instead.

## How was it evaluated?

An offline test suite runs the whole loop against scripted providers and mock
tools with no network access. Nine scenarios assert the tool sequence and
terminal outcome, including the refusals: an unlisted action, an over-budget
remediation, a denied approval and a suppressed duplicate.

Several safety properties carry negative controls — a test that fails when the
control is removed — so a guard cannot be silently disabled.

## What should this not be used for?

- Any decision affecting safety, health, legal rights, credit, employment or
  similar consequential outcomes.
- Autonomous change in an environment where you have not reviewed and bounded
  the action allowlist.
- A substitute for fixing the underlying data platform. It closes the gap
  between a failure and a competent person looking at it; it does not repair
  pipelines.

## Who is accountable?

Every agent has its own Entra identity with its own permissions and its own
audit trail. Identity claims in this repository are read live from the
directory rather than configured, because a hardcoded claim about security
posture eventually becomes false without anyone noticing.

Run `triage-demo identity --check-scope` to see who the agents are and what
they can reach.
