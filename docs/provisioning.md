# Provisioning

What must exist in the demo tenant before the live scenarios will run. Ordered
by lead time, not by importance — **item 1 is the one that blocks demos.**

Verify progress at any point with:

```powershell
.\.venv\Scripts\triage-demo.exe preflight
```

---

## 1. Power BI service principal access ← start here

Dataset refresh needs a token the *dataset* accepts. That means a service
principal, and two separate approvals.

**A managed identity cannot be added to a Power BI workspace today.** MI is not
an option for this specific call. Do not design around one.

### Tenant setting (tenant admin)

Power BI admin portal → Tenant settings → Developer settings →
**Allow service principals to use Power BI APIs** → Enabled, scoped to a
security group containing the SP.

This is the item that has blocked more demos than everything else combined. It is
a tenant admin action, and until it is on, refresh returns 401 with a message
that does not mention it.

### Workspace membership

Add the SP to the workspace as **Member** (Contributor cannot trigger refresh on
all dataset types; Member avoids the ambiguity).

### Verify

```powershell
$body = @{
  grant_type="client_credentials"; client_id=$env:POWERBI_CLIENT_ID
  client_secret=$env:POWERBI_CLIENT_SECRET
  scope="https://analysis.windows.net/powerbi/api/.default"
}
$tok = (Invoke-RestMethod -Method Post -Body $body `
  "https://login.microsoftonline.com/$env:POWERBI_TENANT_ID/oauth2/v2.0/token").access_token

Invoke-RestMethod -Headers @{Authorization="Bearer $tok"} `
  "https://api.powerbi.com/v1.0/myorg/groups/$env:POWERBI_WORKSPACE_ID/datasets"
```

Datasets listed = both approvals landed. 401 = tenant setting. 403 = workspace
membership.

---

## 2. App registration for Microsoft Graph

For inbox polling.

- Permission: **`Mail.Read`** — *Application*, not Delegated
- **Admin consent granted** (the demo runs with no signed-in user)
- Client secret recorded, with its expiry noted

> Note the expiry. Entra app secrets expire, and a demo environment rebuilt in
> three months fails at exactly that point. For anything long-lived, use
> federated credentials instead.

Optional narrowing: `ApplicationAccessPolicy` restricts the app to a single
mailbox, which is worth doing if anyone asks about scope.

### Verify

```powershell
$body = @{
  grant_type="client_credentials"; client_id=$env:GRAPH_CLIENT_ID
  client_secret=$env:GRAPH_CLIENT_SECRET; scope="https://graph.microsoft.com/.default"
}
$tok = (Invoke-RestMethod -Method Post -Body $body `
  "https://login.microsoftonline.com/$env:GRAPH_TENANT_ID/oauth2/v2.0/token").access_token

Invoke-RestMethod -Headers @{Authorization="Bearer $tok"} `
  "https://graph.microsoft.com/v1.0/users/$env:GRAPH_MAILBOX/mailFolders/inbox/messages?`$top=1"
```

---

## 3. Monitored mailbox

A shared mailbox or licensed user, e.g. `bi-alerts@<tenant>.onmicrosoft.com`.

The demo **does not mark messages as read** — rehearsals must be repeatable, and
dedup is by message id held in memory. Confirm nothing else is auto-processing
the mailbox.

Send the two demo emails from `mock/emails/` into it ahead of time, or send them
live during the session for effect. Live is better; have the pre-sent ones as
backup.

---

## 4. Azure AI Foundry project

- A Foundry project; note the endpoint
- A model deployment (`gpt-4o` or similar) — set `FOUNDRY_AGENT_MODEL`
- Your identity needs project-level rights to create agent versions

```powershell
az login
python scripts\register_foundry_agents.py --dry-run
python scripts\register_foundry_agents.py --handoff client
```

**Re-register after any prompt or tool change.** A Foundry-registered agent does
not pick up local edits — it keeps running the previous definition, silently. Put
this on the pre-demo checklist, not in someone's memory.

---

## 5. Power BI workspace + semantic model

- A workspace, with the SP as Member (item 1)
- A semantic model over the well production table, so the report is real
- A report — optional for the flow, but it makes the failure concrete on screen

Seed the model from `mock/data/well_production.csv` (with duplicates) or
`well_production_clean.csv`, depending on which scenario you are staging.

---

## 6. Teams channel + notification path

**Demo path** — incoming webhook. Channel → Connectors → Incoming Webhook. Copy
the URL to `TEAMS_WEBHOOK_URL`.

Treat the URL as a secret: anyone holding it can post to the channel. It is fine
for a demo tenant; say so out loud rather than letting someone assume otherwise.

**Production path** — Graph `/teams/{id}/channels/{id}/messages` with an app
registration, so posts are attributable to an identity.

---

## 7. Data quality flag table

Demo default is `runs/dq_flags.csv` — visible, diffable, openable in Excel, and
easy to show before and after.

For a Fabric or SQL table instead, replace `DataQualityFlagTable` with three
methods (`read_all`, `append`, `reset`) against the real table.

Keep the CSV available regardless. Showing a spreadsheet gain a row is a better
demo beat than showing a query result change.

---

## 8. Application Insights (optional)

Set `APPLICATIONINSIGHTS_CONNECTION_STRING` and install the extra:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[azure]"
```

Spans appear within roughly five minutes. Rehearse the **failed-run** view, not
just the healthy one — that is the question that gets asked.

Without the connection string every telemetry helper is a no-op. Telemetry is
never a hard dependency of the demo running.

---

## Final check

```powershell
.\.venv\Scripts\triage-demo.exe preflight
.\.venv\Scripts\triage-demo.exe reset

# Real side effects, deterministic reasoning - isolates tool problems from model problems
$env:TRIAGE_PROVIDER_MODE="mock"; $env:TRIAGE_TOOL_MODE="live"
.\.venv\Scripts\triage-demo.exe run scenario1-transient

# Then the real thing
$env:TRIAGE_PROVIDER_MODE="foundry"
.\.venv\Scripts\triage-demo.exe run scenario1-transient
.\.venv\Scripts\triage-demo.exe run scenario2-data-quality --show-data
```

Run the mixed mode first. When something breaks, it tells you immediately
whether the problem is the tools or the model — otherwise the two present
identically and you lose twenty minutes.

---

## Security notes

- **Never commit a filled-in `.env`.** `.gitignore` covers it; check anyway.
- Secrets are redacted at the persistence boundary (`redaction.py`, 11 patterns)
  before anything reaches an incident, a Teams message or a trace attribute.
- Traces carry metadata only — no prompt or completion content.
- The Teams webhook URL is a bearer credential in URL form. Rotate it after the
  demo if the recording is shared.
