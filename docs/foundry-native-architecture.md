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

## Revised architecture

| Component | Mechanism | Status |
|---|---|---|
| Trigger | **Routine** (`azd ai routine`) — schedule or custom event | Not yet built |
| Inbox read | **Outlook connector** (`GetEmailsV2`) in a toolbox | Verified by MS 2026-05-22 on MSA; **work tenant unverified** |
| Orchestrator | **Hosted agent** — controller loop, containerised | Not yet built |
| Triage → DQ handoff | `responses` + `agent_reference` | **Verified** |
| Data Quality agent | **Prompt agent**, own identity + version | **Verified** |
| Duplicate evidence | Deterministic scan in the orchestrator | **Verified** |
| Power BI refresh | **`openapi`** tool w/ `managed_identity` auth | Not yet built |
| Content safety | **Guardrails** — jailbreak + XPIA | **Verified on by default** |
| Policy / budgets | `PolicyLedger` — stays custom | **Verified enforced** |
| Tracing | Auto-injected when project linked to App Insights | Not yet wired |
| Regression | pytest (122) + Foundry evaluations | Partly built |

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

1. **Outlook connector on a work tenant.** The verified sample used
   `oauth2generic` (MSA/consumers). Work-tenant auth is a different path.
   Spike before committing to it as the trigger.
2. **Preview surface.** `a2a_preview`, routines, `fabric_iq_preview`,
   `toolbox_search_preview` are all preview. Fine for a demo — say so rather
   than letting the customer assume GA.
3. **API churn.** Two breaking details (`api-version` rejection, `agent` →
   `agent_reference`) surfaced in a single afternoon. Pin and re-verify before
   the demo.
