# Foundry-native architecture — spike findings

**Spiked 2026-08-27** against a live project (`denverdata-foundry-cus/denver`,
`gpt-4o`). Everything below marked *verified* was executed, not read.

Reproduce with `scripts\spike_a2a_ledger.py`.

---

## The question the spike answered

Does using Foundry's agent-to-agent capability mean giving up controller-enforced
policy? If Foundry routes the handoff server-side, `PolicyLedger` is no longer in
the middle and the budget and allowlist become prompt wording again.

**Answer: no.** Verified — a separate Foundry agent was invoked and every turn,
token and tool call was charged against the ledger.

```
responded: bi-data-quality:1  (a different agent, own identity and version)
tokens charged to the shared ledger: 368
refused (policy_blocked): Action 'delete_dataset' is not on the allowlist
{ "llm_turns": 1, "tool_calls": 1, "attempted_actions": 2,
  "tokens_used": 368, "blocked_attempts": ["delete_dataset"] }
```

---

## Verified findings

### 1. `a2a_preview` cannot call a prompt agent

The obvious wiring fails:

```
Failed to fetch agent card: 401 (PermissionDenied)
```

`a2a_preview` targets an **A2A-protocol** endpoint and fetches an agent card
first. A Foundry *prompt* agent publishes `protocols: ["responses"]` and no
agent card — probing `/.well-known/agent-card.json` returns 400, not 401.

To use `a2a_preview`, the callee must be a **hosted** agent declaring
`container_protocol_versions: [{"protocol": "a2a"}]`.

Separately, even against a valid A2A endpoint, the agent-card fetch goes out
anonymously by default; a protected card path needs
`send_credentials_for_agent_card: true` plus a project connection.

### 2. Agent-to-agent over the responses API works, and the ledger charges it

```http
POST {project}/openai/v1/responses
{ "agent_reference": {"type":"agent_reference","name":"bi-data-quality","version":"1"},
  "input": "..." }
```

This is a real call to a separate, independently versioned Foundry agent with
its own managed identity (`principal_id` issued at creation). Because *our* code
makes the call, the dispatcher and ledger sit in front of it.

Two API gotchas, both discovered by probing rather than reading:
- `?api-version=v1` is rejected on this route — *"Use /v1 path instead."*
- the `agent` property is **deprecated**; use `agent_reference`.

That churn is the argument for talking to Foundry over REST from a pinned
client rather than through a preview SDK.

### 3. Guardrails are content filters, and XPIA detection is already on

Every response carries filter results inline:

```json
"jailbreak":       {"filtered": false, "detected": false},
"indirect_attack": {"filtered": false, "detected": false}
```

`indirect_attack` is **indirect prompt-injection (XPIA) detection, on by default
and reported per response.** That matters here more than in most demos: this
agent ingests email, which is attacker-influenceable text.

But guardrails are RAI content filters — Hate, Violence, Jailbreak, XPIA. They
are **not** action authorisation. Nothing native enforces "one remediation per
run" or an action allowlist. `PolicyLedger` stays.

### 4. Toolboxes do not enforce approval — by design

> "If MCP tools have `require_approval: "always"` … **the toolbox endpoint does
> not enforce this — your agent code is responsible.**"
> — `use-toolbox-in-hosted-agent.md`

A first-party statement that action enforcement belongs in the controller. This
is the citation to have in the room.

### 5. Connected Agents (classic) is the wrong feature

Docs are labelled *(classic)*, the pane is gone from the new Foundry experience,
and the documented limitation stands: *"Connected agents cannot call local
functions using the function calling tool."*

Microsoft's **workflow-oriented multi-agent** guidance names *"incident triage
and remediation"* as a target scenario and prescribes *"explicit sequencing and
guards, including clear preconditions, post-conditions, and numerical
thresholds."* That is a description of the controller loop and the ledger — so
the design is the recommended pattern, not a workaround.

---

## Fabric IQ — cut from the triage path

**Recommendation: do not use Fabric IQ in the triage agent.** Not because it is
hard to provision, but because it is architecturally wrong here.

Fabric IQ answers questions *"under the signed-in user's Fabric permissions."*
The triage agent is **unattended** — a routine fires when an email arrives.
There is no signed-in user. Making it work would mean parking a refresh token
for a service account, which is exactly the fragile pattern that fails ~30 days
after go-live under secret-expiry policy.

Secondary constraints, any one of which could sink it on demo day:

| Constraint | Impact |
|---|---|
| BYO Entra app, delegated `Item.Execute.All` + `Item.Read.All`, **tenant admin consent** | Lead time, and not ours to grant |
| Fabric licence for the developer **and every calling end-user** | Cost and eligibility |
| Item must be **published** | Setup step |
| **VNet integration not supported** | Conflicts with a governed-tenant posture |

*Verified:* Power BI is reachable as the signed-in user (79 workspaces), which
confirms the delegated model works — and confirms it is the wrong model for an
unattended agent.

**Where it does belong:** a separate, interactive *"ask your BI data a
question"* agent that analysts invoke with their own identity. That is precisely
what Fabric IQ is for, it is a strong second demo for a BI audience, and it
costs the triage architecture nothing. Offer it as an adjacent scenario.

---

## Trigger + notification — work-tenant spike (2026-08-27)

The remaining unknown was whether the inbox and Teams paths work on a **work**
tenant, since the connector sample Microsoft verified used a consumer identity.

### The connector you want is not the one that was verified

Three distinct Logic Apps connectors, and the distinction is invisible until you
query them:

| Connector | `identityProvider` | Tenant type | Notes |
|---|---|---|---|
| `outlook` | `oauth2generic` | Consumer / MSA | **What the verified sample used** |
| `office365` | `aadcertificate` | **Work / school** | "Office 365 Outlook"; reads mail via Graph |
| `teams` | `aadcertificate` | Work / school | Also needs a `token:TenantId` parameter |

The operations exist — `office365 / GetEmailsV3`, `teams / PostCardToConversation`
— so on paper the work-tenant path is there.

**But the catalog is empty in this tenant.** The asset-gallery returns zero
results for every query, including unfiltered ones (and a 502 on the private
registry). Without a catalog entry there is no `toolEntityId`, and the
gateway-connector flow cannot be started.

### The deeper reason not to use it anyway

Gateway connectors authenticate with **per-user OAuth consent** — a browser
popup, per caller, with tokens stored by the gateway. The triage agent is
**unattended**: a routine fires when mail arrives. There is no caller to consent.

This is the same architectural mismatch that rules out Fabric IQ, and it is
worth naming as a pattern: *anything built around per-user delegated consent is
the wrong shape for an unattended agent, however convenient it looks in a demo.*

### What does work — verified live

App-only Microsoft Graph, no user in the loop:

```
token roles : Mail.Read
token has NO user claim (app-only): upn=True
SUCCESS - read 3 message(s) with no user in the loop
```

`Mail.Read` as an **application** permission, admin-consented. No signed-in user,
no consent popup, no stored refresh token to rot. This is the correct trigger
path for an unattended agent, and it is what the demo should use.

In production, hold that identity as a **managed identity or federated
credential** rather than a client secret, so there is nothing to rotate.

### The finding worth putting in front of a security reviewer

App-only `Mail.Read` is **tenant-wide**. Verified: the spike app registration —
created solely to read one demo mailbox — successfully read the *global
administrator's* inbox.

```
target mailbox: admin@<tenant>.onmicrosoft.com
  AUTHORIZED (200) - app can read a SECOND mailbox
```

Scope it with an Exchange **`ApplicationAccessPolicy`** restricting the app to a
single mailbox. Without that, "an agent that reads the BI alerts inbox" is in
fact an agent that can read every mailbox in the company. That is normally
discovered in a security review months later, not at design time.

*(The spike app registration was deleted immediately and its removal confirmed.)*

### Teams is deliberately asymmetric — read is easy, write is governed

Graph permissions for sending a channel message:

| Permission type | Channel message send |
|---|---|
| Delegated (work or school) | `ChannelMessage.Send` |
| Delegated (personal MSA) | Not supported |
| **Application** | **`Teamwork.Migrate.All`** — migration/import only |

**There is no app-only path for normal channel posting.** Application
permissions are restricted to migration scenarios. So an unattended agent cannot
post to a channel as itself via Graph.

Remaining options, in order of preference for this demo:

1. **Power Automate Workflows webhook** — works unattended, no user, and is the
   supported replacement for the Office 365 connector webhooks retired
   2026-05-22. Already implemented (`WorkflowsWebhookTeamsNotifier`).
2. **A Teams bot** — the production answer; posts under its own identity.
3. Delegated + a parked refresh token — the fragile pattern; avoid.

Worth saying out loud rather than hiding: Microsoft deliberately makes it harder
for an application to *speak into a human conversation* than to read a mailbox.
That asymmetry is a reasonable design position, and it shapes the architecture.

### Consent is not the blocker in this tenant

The spike account holds **Global Administrator**, so admin consent, app
registration and Graph application permissions are all available. Where a path
was rejected above it was rejected on architecture — the unattended/delegated
mismatch — not on whether consent could be obtained.

That distinction matters for the customer conversation: in *their* tenant,
consent may well be the binding constraint, and it should be raised early
regardless.

---


| Component | Mechanism | Status |
|---|---|---|
| Trigger | **Routine** (`azd ai routine`) — schedule or custom event | Not yet built |
| Inbox read | **Graph `Mail.Read` (application)** — no user, no stored refresh token | **Verified live** |
| Mailbox scoping | Exchange **`ApplicationAccessPolicy`** — restrict to one mailbox | **Required**, not yet applied |
| Orchestrator | **Hosted agent** — controller loop, containerised | Not yet built |
| Triage → DQ handoff | `responses` + `agent_reference` | **Verified** |
| Data Quality agent | **Prompt agent**, own identity + version | **Verified** |
| Duplicate evidence | Deterministic scan in the orchestrator | **Verified** |
| Power BI refresh | **`openapi`** tool w/ `managed_identity` auth | Not yet built |
| Teams notify | **Workflows webhook** (no app-only channel post exists) | Built |
| Content safety | **Guardrails** — jailbreak + XPIA | **Verified on by default** |
| Policy / budgets | `PolicyLedger` — stays custom | **Verified enforced** |
| Tracing | Auto-injected when project linked to App Insights | Not yet wired |
| Regression | pytest (122) + Foundry evaluations | Partly built |

### Rejected, and why

| Option | Reason |
|---|---|
| `a2a_preview` → prompt agent | Prompt agents speak `responses` and publish no agent card |
| Connected Agents (classic) | Cannot call local functions; superseded |
| Gateway connectors (`office365` / `teams`) | Catalog empty in this tenant, **and** per-user OAuth is wrong for an unattended agent |
| Fabric IQ in the triage path | Delegated per-user model vs. unattended agent |
| Graph app-only Teams post | Application permission is `Teamwork.Migrate.All` — migration only |


### Why the DQ agent stays a prompt agent

Making it a hosted A2A agent would unlock the `a2a_preview` checkbox, at the cost
of a container, a registry and a deploy pipeline for what is currently a prompt
and one tool. The handoff is already genuinely agent-to-agent: two separate
agents, separate identities, separate versions, visible as separate spans.

Revisit if the customer specifically wants `a2a_preview` on screen — it is a
half-day once the orchestrator is already hosted, since the same container
pattern applies.

---

## Open risks

1. **Preview surface.** `a2a_preview`, routines, `fabric_iq_preview`,
   `toolbox_search_preview` are all preview. Fine for a demo — say so rather
   than letting the customer assume GA.
2. **API churn.** Two breaking details (`api-version` rejection, `agent` →
   `agent_reference`) surfaced in a single afternoon. Pin and re-verify before
   the demo.
3. **Connector catalog availability.** The asset-gallery returned nothing in
   this tenant. If a future design depends on gateway connectors, confirm the
   catalog is populated in the *target* tenant first — it is not a given.
4. **Mailbox scoping is a hard requirement, not a nicety.** Ship the
   `ApplicationAccessPolicy` at the same time as the app registration. An
   unscoped `Mail.Read` grant is a tenant-wide mailbox read.
5. **Consent in the customer's tenant.** The spike ran as Global Administrator,
   so consent was never the constraint here. In the customer's tenant it may be
   the longest pole — raise it in week one.

