# Design scope: Power BI playbook remediation tools

## Purpose

This document scopes remediation and diagnostic tooling for the seven Power BI refresh failure modes not already being implemented as schedule re-enable or capacity backoff/deferred retry. The repo is a customer-facing, offline-first demo. The controller must enforce every safety precondition. Prompt wording is not a control.

Current repository shape:

- Playbooks live in `src\triage_demo\knowledge\playbooks.py` and recognize nine failure modes.
- The controller allowlists actions in `src\triage_demo\policy.py` as `REMEDIATION_ACTIONS`, `REPORTING_ACTIONS`, and `DIAGNOSTIC_ACTIONS`.
- Tool schemas and dispatch live in `src\triage_demo\tools\registry.py`.
- Power BI live and mock implementations live in `src\triage_demo\tools\powerbi.py`.
- Human approval is explicit, fingerprint-matched, unexpired, and single-use in `src\triage_demo\approvals.py`.
- Every new live operation needs a deterministic mock path in `MockPowerBIClient`. Tests and scenarios must stay offline.

Microsoft Learn pages verified for this scope:

- Refresh dataset in group: `https://learn.microsoft.com/rest/api/power-bi/datasets/refresh-dataset-in-group`
- Get refresh history in group: `https://learn.microsoft.com/rest/api/power-bi/datasets/get-refresh-history-in-group`
- Get dataset data sources in group: `https://learn.microsoft.com/rest/api/power-bi/datasets/get-datasources-in-group`
- Get gateway: `https://learn.microsoft.com/rest/api/power-bi/gateways/get-gateway`
- Get gateway data source: `https://learn.microsoft.com/rest/api/power-bi/gateways/get-datasource`
- Bind dataset to gateway in group: `https://learn.microsoft.com/rest/api/power-bi/datasets/bind-to-gateway-in-group`
- Add gateway data source user: `https://learn.microsoft.com/rest/api/power-bi/gateways/add-datasource-user`
- Update gateway data source credentials: `https://learn.microsoft.com/rest/api/power-bi/gateways/update-datasource`
- Execute queries in group: `https://learn.microsoft.com/rest/api/power-bi/datasets/execute-queries-in-group`
- Fabric update connection: `https://learn.microsoft.com/rest/api/fabric/core/connections/update-connection`
- Public troubleshooting source used by existing playbooks: `https://learn.microsoft.com/power-bi/connect-data/refresh-troubleshooting-refresh-scenarios`

## Tiering rule used here

- Tier 1 auto-remediate: transient, idempotent, low blast radius, and safe to repeat once under the existing write budget.
- Tier 2 deterministic fix behind human approval: a bounded live change exists, but it can affect availability, security, capacity, or other datasets.
- Tier 3 never automate: no safe deterministic action exists for the agent identity, or the action is a data/security/capacity design decision. The agent should collect evidence and escalate.

## 1. Scheduled refresh timeout

### Automation tier

Tier 2 deterministic fix behind human approval.

An isolated timeout can already use the existing `refresh_powerbi_dataset` Tier 1 path. A new tool should not be another blind retry. The only materially different API operation is an enhanced refresh with a bounded timeout, retry count, and possibly lower `maxParallelism`. That is not idempotent in the human sense: it can occupy capacity for hours and may delay other refreshes. It is safe only as a one-off approved mitigation when history shows the model usually completes or when a human accepts the capacity tradeoff.

Do not use this for shared capacity. Microsoft Learn states enhanced refresh is not supported for shared capacities and shared capacity has an eight-requests-per-day limit including scheduled refreshes.

### Concrete API

`POST https://api.powerbi.com/v1.0/myorg/groups/{groupId}/datasets/{datasetId}/refreshes`

Request body for this tool should use enhanced refresh fields, not only `notifyOption`:

```json
{
  "type": "full",
  "commitMode": "transactional",
  "timeout": "05:00:00",
  "retryCount": 0,
  "maxParallelism": 1
}
```

Verified permissions from Microsoft Learn:

- Required scope: `Dataset.ReadWrite.All`.
- Service principal profiles are supported. The repo also documents the tenant setting `Allow service principals to use Power BI APIs` and workspace membership requirement.
- Enhanced refresh is Premium-only. For shared capacity, only `notifyOption` can be specified.
- `notifyOption` is not applicable to enhanced refreshes or service principal operations.
- Total duration including retries cannot exceed 24 hours.

### Tool definition

Name: `trigger_powerbi_enhanced_refresh`

Allowlist: `REMEDIATION_ACTIONS`

Approval: add to `APPROVAL_REQUIRED_ACTIONS`

Schema:

```json
{
  "type": "function",
  "function": {
    "name": "trigger_powerbi_enhanced_refresh",
    "description": "REMEDIATION REQUIRING HUMAN APPROVAL. Trigger a bounded enhanced Power BI semantic model refresh for a scheduled-refresh timeout on Premium capacity. Use only when deterministic refresh history shows timeout is isolated or a human accepts the capacity tradeoff.",
    "parameters": {
      "type": "object",
      "properties": {
        "timeout": {"type": "string", "pattern": "^([0-1]?[0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$"},
        "retry_count": {"type": "integer", "minimum": 0, "maximum": 1, "default": 0},
        "max_parallelism": {"type": "integer", "minimum": 1, "maximum": 2, "default": 1},
        "justification": {"type": "string"}
      },
      "required": ["timeout", "justification"]
    }
  }
}
```

Controller caps should be stricter than schema if needed. Do not allow 24-hour requests in the demo. A five-hour maximum is enough to demonstrate the API without creating a capacity hostage.

### Blast radius for `_impact_of`

Runs a live refresh for this semantic model with a longer processing window. It can consume Premium capacity for up to the approved timeout and delay other refreshes on the same capacity. Transactional commit preserves the prior model if the refresh fails.

### Required evidence before dispatch

- `get_dataset_refresh_history` was called in this run.
- Latest failed refresh has timeout-shaped `serviceExceptionJson` or alert error code.
- No deterministic data-quality finding is positive.
- Dataset is known to be on Premium, PPU, or Fabric capacity. If the demo cannot prove capacity type offline, the mock scenario must carry `capacity_mode: premium` and the live client must refuse unknown capacity.
- No open known incident exists for the same signature.
- `timeout` is greater than the observed failed duration but below the controller maximum.
- `retry_count <= 1`, `max_parallelism <= 2`, and `commitMode` is forced to `transactional`.
- Approval fingerprint includes timeout, retry count, and max parallelism.
- Waiting on the approval must remain excluded from the run wall clock, and the approved refresh poll timeout must be separately bounded. Do not create equal nested timeouts.

### Scenario sketch

```yaml
name: scenario-timeout-enhanced-refresh-approved
title: "Scheduled timeout - approved bounded enhanced refresh"
description: >-
  A scheduled refresh times out on Premium capacity after normally completing.
  The agent proposes one bounded enhanced refresh. The controller asks for
  approval before occupying capacity for longer than the default window.
email: mock/emails/07-scheduled-timeout.json

datasets:
  - name: well_production
    path: mock/data/well_production_clean.csv
    key_columns: [well_id, production_date]

powerbi:
  workspace_id: b8e42d17-3f6a-4c9b-8d21-77e4a1c9f003
  dataset_id: 11111111-1111-4111-8111-111111111111
  capacity_mode: premium
  enhanced_refresh_result: Completed
  refresh_history:
    - {requestId: t1, status: Failed, refreshType: Scheduled, startTime: "2026-08-28T05:00:00Z", endTime: "2026-08-28T10:00:00Z", serviceExceptionJson: '{"errorCode":"ScheduledRefreshTimeout"}'}
    - {requestId: t0, status: Completed, refreshType: Scheduled, startTime: "2026-08-27T05:00:00Z", endTime: "2026-08-27T06:10:00Z"}

approval:
  mode: auto_approve
  approver: m.hughes@contoso.com

expect:
  outcome: resolved
  remediation_applied: true
  enhanced_refresh_triggered: true
  approval_requested: true
  approval_granted: true
  flags_written: 0
  blocked_attempts: 0
```

### What could go wrong

- A longer timeout hides a model growth problem and trains owners to depend on emergency refreshes.
- The refresh completes but starves other datasets on the same capacity.
- `partialBatch` would leave a model partially loaded; force `transactional`.
- A too-long poll could recreate the known timeout bug if human approval wait and run timeout are charged against the same clock.
- A successful on-demand enhanced refresh does not fix the next scheduled timeout.

## 2. Expired or changed data source credentials

### Automation tier

Tier 3 never automate. Escalate with evidence.

Credential repair is a secret-handling operation. In this environment, long-lived secrets are not allowed, Entra app secrets are purged, storage shared keys are disabled, and managed identity is preferred. The agent should not collect, store, generate, or rotate credentials for arbitrary data sources. Even where an API exists, the missing input is the new secret or token, and the safety decision belongs to the data source owner.

There is one narrow future exception: if the failing item uses a Fabric connection already designed for managed identity and the deterministic fix is to switch from a broken secret credential to a pre-approved managed identity connection, treat that as a separate Tier 2 connection-rebind design, not as credential rotation.

### Concrete API

Power BI gateway credential update exists:

`PATCH https://api.powerbi.com/v1.0/myorg/gateways/{gatewayId}/datasources/{datasourceId}`

Verified permissions and limitations:

- Required scope: `Dataset.ReadWrite.All`.
- On-premises gateway: caller must have gateway admin permissions.
- Cloud data sources: caller must be the data source owner.
- Service principal profiles are supported.
- On-premises credentials must be encrypted using the gateway public key.
- OAuth2 credentials set this way do not include a refresh token and are valid only while the provided token is valid, about one hour.
- SAS token credentials are supported only for Azure Blob Storage and Azure Data Lake Storage. This conflicts with the MCAPS preference to avoid SAS/shared-key patterns.
- VNet gateways are not supported.

Fabric connection update also exists:

`PATCH https://api.fabric.microsoft.com/v1/connections/{connectionId}`

Verified permissions and identities:

- Required delegated scope: `Connection.ReadWrite.All`.
- Caller must have permission for the connection or gateway admin permission.
- Microsoft Learn lists users, service principals, and managed identities as supported identities.

I searched Microsoft Learn for Power BI update datasource credentials, Fabric update connection, and credential configuration. I did not find a safe app-only API that can rotate an end-user OAuth refresh token without a human credential flow. The Power BI API explicitly says OAuth2 credentials supplied through the REST API are only valid while the access token is valid.

### Tool definition

Name: `collect_datasource_credential_evidence`

Allowlist: `DIAGNOSTIC_ACTIONS`

Approval: not applicable; it is read-only.

Schema:

```json
{
  "type": "function",
  "function": {
    "name": "collect_datasource_credential_evidence",
    "description": "DIAGNOSTIC. List the semantic model data sources and credential types so the owner can repair expired or changed credentials. Does not read, store, or update secrets.",
    "parameters": {
      "type": "object",
      "properties": {
        "include_gateway_details": {"type": "boolean", "default": true},
        "justification": {"type": "string"}
      },
      "required": []
    }
  }
}
```

Optional reporting companion:

Name: `report_credential_rotation_required`

Allowlist: `REPORTING_ACTIONS`

Approval: not applicable.

Schema:

```json
{
  "type": "function",
  "function": {
    "name": "report_credential_rotation_required",
    "description": "REPORTING. Record that credential repair is required and include data source metadata, without any credential value.",
    "parameters": {
      "type": "object",
      "properties": {
        "datasource_id": {"type": "string"},
        "gateway_id": {"type": "string"},
        "credential_type": {"type": "string"},
        "owner_hint": {"type": "string"},
        "detail": {"type": "string"}
      },
      "required": ["detail"]
    }
  }
}
```

Do not add `update_datasource_credentials` as a remediation tool for the accelerator until there is a product-specific managed identity connection contract and an offline mock that proves no secret crosses the tool boundary.

### Blast radius for `_impact_of`

No live environment change. The diagnostic lists data source IDs, gateway IDs, source type, connection details already exposed by Power BI metadata, and credential type. It must not include passwords, keys, SAS tokens, OAuth tokens, or connection strings.

If a future credential-update tool is proposed, the blast radius is: replaces the credential used by every semantic model or item that uses this connection or gateway data source. A wrong value can break all dependent refreshes and may grant access through the wrong identity.

### Required evidence before permitting it

For diagnostics:

- Latest failure error text matches credential, password, token, authentication, or `ModelRefreshFailed_CredentialsNotSpecified`.
- Call `get_dataset_refresh_history` and `get_datasources` first.
- Redact connection details using the store-boundary redaction patterns before persistence.
- Never accept credential material in arguments. Reject fields named password, secret, key, token, sas, privateKey, or connectionString.

For any future Tier 2 managed identity connection repair:

- Existing connection ID and target connection ID are both known.
- Target connection is pre-created, uses managed identity or workspace identity, and is approved for this data source.
- Data owner approval is explicit and fingerprinted.
- No ad hoc credential payload is accepted.

### Scenario sketch

```yaml
name: scenario-credentials-escalate-with-evidence
title: "Expired credentials - no secret automation"
description: >-
  A refresh fails because credentials changed. The agent lists the affected
  data source and reports the owner action. It never asks for, stores, or
  updates a secret.
email: mock/emails/08-expired-credentials.json

powerbi:
  workspace_id: b8e42d17-3f6a-4c9b-8d21-77e4a1c9f003
  dataset_id: 22222222-2222-4222-8222-222222222222
  datasources:
    - datasourceId: 33333333-3333-4333-8333-333333333333
      gatewayId: 44444444-4444-4444-8444-444444444444
      datasourceType: Sql
      credentialType: OAuth2
      connectionDetails: {server: sql-prod-01, database: SalesMart}
  refresh_history:
    - {requestId: c1, status: Failed, refreshType: Scheduled, serviceExceptionJson: '{"errorCode":"ModelRefreshFailed_CredentialsNotSpecified"}'}

expect:
  outcome: needs_human
  remediation_applied: false
  credential_evidence_collected: true
  credential_secret_seen: false
  flags_written: 0
  blocked_attempts: 0
```

### What could go wrong

- The agent accepts a secret in a prompt or tool argument and persists it.
- A REST credential update succeeds for one hour with OAuth2, then the scheduled refresh fails again after the access token expires.
- A Basic, Key, or SAS credential appears to fix the demo but violates tenant governance or creates a rotation liability.
- Updating a shared gateway data source breaks other datasets using the same data source.
- The agent takes ownership of a cloud data source to update credentials and changes operational ownership unexpectedly.

## 3. Gateway unreachable

### Automation tier

Tier 1 for a single transient retry using existing `refresh_powerbi_dataset`. Tier 2 behind approval for gateway rebind. Never auto-rebind.

A gateway restart or short network outage is often transient. The existing refresh tool is the right Tier 1 action when history shows a single failure after recent successes. If the same gateway fails repeatedly, another refresh is likely to reproduce the failure. The bounded fix is to bind the dataset to a known alternate gateway or data source IDs, but this affects routing and possibly other dependent refreshes. It must stay behind approval.

### Concrete API

Read evidence:

`GET https://api.powerbi.com/v1.0/myorg/groups/{groupId}/datasets/{datasetId}/datasources`

- Required scope: `Dataset.ReadWrite.All` or `Dataset.Read.All`.
- Service principal profiles are supported.
- Caller must have Write permissions on the dataset.

`GET https://api.powerbi.com/v1.0/myorg/gateways/{gatewayId}`

- Required scope: `Dataset.ReadWrite.All` or `Dataset.Read.All`.
- Caller must have gateway admin permissions.
- VNet gateways are not supported.
- Response includes `gatewayStatus` and public key.

Remediation:

`POST https://api.powerbi.com/v1.0/myorg/groups/{groupId}/datasets/{datasetId}/Default.BindToGateway`

- Required scope: `Dataset.ReadWrite.All`.
- Service principal profiles are supported.
- Only supports on-premises data gateway.
- Microsoft Learn says to add the API caller principal as a data source user on the gateway.
- Request body requires `gatewayObjectId` and optionally `datasourceObjectIds`. Supplying `datasourceObjectIds` is safer than relying on first matching data source.

Related permission helper:

`POST https://api.powerbi.com/v1.0/myorg/gateways/{gatewayId}/datasources/{datasourceId}/users`

- Required scope: `Dataset.ReadWrite.All`.
- Caller must have gateway admin permissions.
- Can grant a service principal `Read` or `ReadOverrideEffectiveIdentity` on a gateway data source.
- Adding groups through the API is not supported.
- VNet gateways are not supported.

### Tool definition

Existing remediation name should stay: `rebind_dataset_gateway`

Allowlist: `REMEDIATION_ACTIONS`

Approval: already in `APPROVAL_REQUIRED_ACTIONS`

Recommended schema extension:

```json
{
  "type": "function",
  "function": {
    "name": "rebind_dataset_gateway",
    "description": "REMEDIATION REQUIRING HUMAN APPROVAL. Rebind the semantic model to a specified on-premises data gateway and, when known, exact data source IDs. Use only after deterministic evidence shows repeating failures on the current gateway and the target gateway/data sources are validated.",
    "parameters": {
      "type": "object",
      "properties": {
        "target_gateway": {"type": "string", "description": "Gateway object ID, not display name."},
        "datasource_object_ids": {"type": "array", "items": {"type": "string"}, "description": "Exact gateway data source IDs to bind. Required when multiple data sources match."},
        "justification": {"type": "string"}
      },
      "required": ["target_gateway", "justification"]
    }
  }
}
```

Diagnostic companion:

Name: `inspect_powerbi_gateway_binding`

Allowlist: `DIAGNOSTIC_ACTIONS`

Schema:

```json
{
  "type": "function",
  "function": {
    "name": "inspect_powerbi_gateway_binding",
    "description": "DIAGNOSTIC. Read dataset data sources and gateway status to decide whether a gateway failure is transient or a rebind candidate.",
    "parameters": {"type": "object", "properties": {}, "required": []}
  }
}
```

Do not make `add_datasource_user` an autonomous remediation. It is a permission grant. If needed as part of a rebind, make it a separate Tier 2 approval or require it as a pre-provisioned condition.

### Blast radius for `_impact_of`

Repoints this semantic model to the approved on-premises gateway and data source IDs. Refreshes for this model may fail if the target gateway cannot reach the same source or has different credentials. Other datasets sharing either gateway can be affected by added load, and in-flight refreshes may fail.

If a data source user grant is added: grants the agent identity permission to use the gateway data source. That can allow future datasets owned by that identity to use the source through the gateway.

### Required evidence before dispatch

- `get_dataset_refresh_history` shows repeated failures with gateway-shaped error text on the same dataset and no successful scheduled refresh since.
- `inspect_powerbi_gateway_binding` confirms current gateway ID and data source IDs.
- Target gateway ID is a GUID or validated from a registry. Do not accept free-form display names in live mode.
- Target gateway is on-premises gateway. Refuse VNet gateway because the Bind API does not support it.
- API caller is already a data source user on the target gateway data sources, or a separately approved permission step has completed.
- If multiple matching data sources exist, require `datasource_object_ids`. Do not let Power BI bind to the first match silently.
- Human approval includes current gateway, target gateway, data source IDs, and the repeated failure evidence.

### Scenario sketch

```yaml
name: scenario-gateway-rebind-with-datasources
title: "Gateway unreachable - approved exact rebind"
description: >-
  The same gateway fails three scheduled refreshes. The controller validates
  the target gateway and exact data source IDs before asking a human to approve
  the rebind.
email: mock/emails/05-gateway-repeating.json

powerbi:
  workspace_id: b8e42d17-3f6a-4c9b-8d21-77e4a1c9f003
  dataset_id: 4e6b1d90-3c72-4a58-8f19-b27ec5a40d83
  datasources:
    - datasourceId: aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa
      gatewayId: gw-onprem-01
      datasourceType: Sql
  gateways:
    gw-onprem-01: {gatewayStatus: Offline, type: Resource}
    gw-onprem-02: {gatewayStatus: Online, type: Resource}
  target_gateway: gw-onprem-02
  target_datasource_object_ids: [bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb]
  refresh_history:
    - {requestId: g3, status: Failed, refreshType: Scheduled, serviceExceptionJson: '{"errorCode":"GatewayNotReachable"}'}
    - {requestId: g2, status: Failed, refreshType: Scheduled, serviceExceptionJson: '{"errorCode":"GatewayNotReachable"}'}
    - {requestId: g1, status: Failed, refreshType: Scheduled, serviceExceptionJson: '{"errorCode":"GatewayNotReachable"}'}

approval:
  mode: auto_approve
  approver: m.hughes@contoso.com

expect:
  outcome: resolved
  remediation_applied: true
  gateway_binding_inspected: true
  gateway_rebound_to: gw-onprem-02
  approval_requested: true
  approval_granted: true
  blocked_attempts: 0
```

### What could go wrong

- The target gateway has a matching data source name but points at a different server or database.
- Rebinding to a busy gateway fixes this model but creates capacity or gateway pressure elsewhere.
- The agent is not a data source user, so the rebind succeeds but the next refresh cannot use the source.
- The source requires a network path or credential that only the old gateway had.
- Free-form target names bind to the wrong gateway in live mode.

## 4. Out-of-memory during refresh

### Automation tier

Tier 2 only for a constrained mitigation; Tier 3 for real remediation.

The durable fixes are model reduction, partitioning, incremental refresh changes, or capacity/SKU changes. Those are not safe autonomous actions for this demo. A narrow mitigative tool can try a transactional enhanced refresh with lower `maxParallelism` to reduce concurrent memory pressure. That is not guaranteed to work and can lengthen refresh time. It should require approval and clear wording that it is a one-off mitigation, not a model fix.

Do not automate capacity scale-up here. In MCAPS, cost automation may shrink or stop resources overnight unless exempted, and capacity decisions need cost and governance context.

### Concrete API

Same enhanced refresh endpoint as the timeout case:

`POST https://api.powerbi.com/v1.0/myorg/groups/{groupId}/datasets/{datasetId}/refreshes`

Use:

```json
{
  "type": "full",
  "commitMode": "transactional",
  "maxParallelism": 1,
  "retryCount": 0
}
```

Verified from Microsoft Learn:

- Required scope: `Dataset.ReadWrite.All`.
- Service principal profiles are supported.
- Enhanced refresh is not supported for shared capacities.
- `maxParallelism` controls maximum processing threads.
- `commitMode=transactional` preserves previous data if the operation fails.
- `partialBatch` can leave only a subset of data loaded or a table empty; do not use it.

I did not find a Power BI REST API that automatically optimizes model memory, reduces columns, changes compression, or safely changes semantic model design. Those are model-authoring tasks, not incident remediations.

### Tool definition

Reuse `trigger_powerbi_enhanced_refresh`, but controller should record reason `oom_low_parallelism` and force `max_parallelism: 1`.

Allowlist: `REMEDIATION_ACTIONS`

Approval: `APPROVAL_REQUIRED_ACTIONS`

Schema can be the same as the timeout tool, but with a controller-side preset:

```json
{
  "type": "function",
  "function": {
    "name": "trigger_powerbi_enhanced_refresh",
    "description": "REMEDIATION REQUIRING HUMAN APPROVAL. Trigger a bounded transactional enhanced refresh. For out-of-memory failures the controller forces max_parallelism=1 and retry_count=0.",
    "parameters": {
      "type": "object",
      "properties": {
        "mode": {"type": "string", "enum": ["timeout_extension", "oom_low_parallelism"]},
        "timeout": {"type": "string"},
        "justification": {"type": "string"}
      },
      "required": ["mode", "justification"]
    }
  }
}
```

If you want separate audit vocabulary, name it `trigger_low_parallelism_refresh`, but implement it through the same Power BI client method to avoid two ways to call the same endpoint.

### Blast radius for `_impact_of`

Runs this semantic model refresh serially or near-serially to reduce memory pressure. It can occupy capacity for longer than a normal refresh. If it fails, transactional commit should preserve the previous loaded state.

### Required evidence before dispatch

- Latest failure error text matches out-of-memory, resource governing, memory limit, or the OOM playbook.
- No duplicate-key or credential playbook also matches more specifically.
- Capacity mode supports enhanced refresh.
- Controller forces `maxParallelism=1`, `retryCount=0`, and `commitMode=transactional`.
- Approval says this is a one-off mitigation and the model still needs owner review.
- Do not run if a prior low-parallelism attempt for the same signature already failed.

### Scenario sketch

```yaml
name: scenario-oom-low-parallelism-approved
title: "Out of memory - approved low-parallelism mitigation"
description: >-
  A refresh fails with a memory-governing error. The agent proposes one
  transactional enhanced refresh with maxParallelism=1 and records that model
  optimization is still required.
email: mock/emails/09-refresh-oom.json

powerbi:
  workspace_id: b8e42d17-3f6a-4c9b-8d21-77e4a1c9f003
  dataset_id: 55555555-5555-4555-8555-555555555555
  capacity_mode: premium
  enhanced_refresh_result: Completed
  refresh_history:
    - {requestId: oom1, status: Failed, refreshType: Scheduled, serviceExceptionJson: '{"errorCode":"OutOfMemoryException"}'}

approval:
  mode: auto_approve
  approver: m.hughes@contoso.com

expect:
  outcome: resolved
  remediation_applied: true
  enhanced_refresh_triggered: true
  enhanced_refresh_mode: oom_low_parallelism
  max_parallelism: 1
  approval_requested: true
  flags_written: 0
  blocked_attempts: 0
```

### What could go wrong

- The refresh still fails because the model exceeds the SKU even serially.
- It succeeds once but the next scheduled refresh fails again.
- Serial refresh runs so long that downstream reports remain stale or other jobs are delayed.
- An operator reads `resolved` as permanent. The final summary must say `mitigated` or include a follow-up detail even if the outcome enum remains `resolved`.
- A capacity scale-up would fix it faster but create cost/governance drift; do not hide that decision in an incident tool.

## 5. Access forbidden to the data source

### Automation tier

Tier 3 never automate, except for read-only evidence collection.

A 403 or access denied from the underlying source is an authorization decision. Automatically granting the agent, dataset owner, or gateway data source access is unsafe because the controller cannot know the data classification, business owner, least-privilege scope, or whether the identity is supposed to read the source. A permission write may fix refresh by expanding access incorrectly.

Do not confuse two cases:

- Power BI gateway data source use permission: `Add Datasource User` exists but is still a permission grant.
- Underlying source permission, for example SQL, ADLS, SharePoint, OneLake: Power BI cannot repair this generically. Graph or Azure RBAC writes would be source-specific and unsafe in a BI incident loop.

### Concrete API

Evidence APIs:

`GET https://api.powerbi.com/v1.0/myorg/groups/{groupId}/datasets/{datasetId}/datasources`

`GET https://api.powerbi.com/v1.0/myorg/gateways/{gatewayId}/datasources/{datasourceId}`

Potential but unsafe permission API:

`POST https://api.powerbi.com/v1.0/myorg/gateways/{gatewayId}/datasources/{datasourceId}/users`

Verified permissions and limitations:

- Required scope: `Dataset.ReadWrite.All`.
- Caller must have gateway admin permissions.
- The API grants or updates permissions required to use the specified gateway data source.
- Supports principal type `App` and rights such as `Read`.
- Adding groups through the API is not supported.
- VNet gateways are not supported.

I searched Microsoft Learn for generic Power BI/Fabric REST APIs to grant the refresh identity access to arbitrary data sources. I found gateway data source user grants, but no safe generic API that can infer or apply least-privilege access to the underlying SQL, storage, SharePoint, OneLake, or SaaS source.

### Tool definition

Name: `collect_datasource_access_evidence`

Allowlist: `DIAGNOSTIC_ACTIONS`

Approval: not applicable for the diagnostic.

Schema:

```json
{
  "type": "function",
  "function": {
    "name": "collect_datasource_access_evidence",
    "description": "DIAGNOSTIC. Collect the affected semantic model data source, gateway binding, credential type, and error evidence for an access-forbidden refresh failure. Does not grant access.",
    "parameters": {
      "type": "object",
      "properties": {
        "justification": {"type": "string"}
      },
      "required": []
    }
  }
}
```

Reporting companion:

Name: `report_datasource_access_required`

Allowlist: `REPORTING_ACTIONS`

Schema:

```json
{
  "type": "function",
  "function": {
    "name": "report_datasource_access_required",
    "description": "REPORTING. Record that the source owner must review access for the semantic model refresh identity.",
    "parameters": {
      "type": "object",
      "properties": {
        "datasource_id": {"type": "string"},
        "gateway_id": {"type": "string"},
        "source_type": {"type": "string"},
        "detail": {"type": "string"}
      },
      "required": ["detail"]
    }
  }
}
```

Do not add a generic `grant_datasource_access` remediation. If a customer wants a product-specific grant, make it a separate design with explicit source type, scope, approval, and least-privilege tests.

### Blast radius for `_impact_of`

No live environment change for the scoped tools. They collect source metadata and report that an owner must review permissions.

For the unsafe grant not recommended: grants an identity permission to use or read a data source. That can expose data beyond the failed report and can authorize future refreshes through the same connection.

### Required evidence before permitting it

- Latest refresh failure contains 403, forbidden, access denied, or equivalent source error.
- Data source metadata is available and redacted.
- Tool refuses to accept target principals or access rights; it is not a grant path.
- If the error is actually expired credentials, route to credential evidence instead.
- If source type is known and customer later adds a source-specific grant tool, require data classification, owner approval, exact principal object ID, exact scope, and deny wildcard scopes.

### Scenario sketch

```yaml
name: scenario-access-forbidden-escalate
title: "Access forbidden - evidence only"
description: >-
  A refresh identity receives 403 from the source. The controller collects the
  affected data source and reports the owner action. It refuses permission
  grants in the BI triage loop.
email: mock/emails/10-access-forbidden.json

powerbi:
  workspace_id: b8e42d17-3f6a-4c9b-8d21-77e4a1c9f003
  dataset_id: 66666666-6666-4666-8666-666666666666
  datasources:
    - datasourceId: 77777777-7777-4777-8777-777777777777
      gatewayId: 88888888-8888-4888-8888-888888888888
      datasourceType: AzureDataLakeStorage
      connectionDetails: {account: lakeprod, domain: dfs.core.windows.net}
  refresh_history:
    - {requestId: f1, status: Failed, refreshType: Scheduled, serviceExceptionJson: '{"errorCode":"403","message":"Access to the resource is forbidden"}'}

expect:
  outcome: needs_human
  remediation_applied: false
  datasource_access_evidence_collected: true
  access_grant_attempted: false
  flags_written: 0
  blocked_attempts: 0
```

### What could go wrong

- A permission grant fixes the alert but violates least privilege or data policy.
- The wrong principal is granted access because the refresh identity is ambiguous.
- Granting gateway data source use does not fix underlying source RBAC, causing repeated failures after a risky change.
- A generic Graph or Azure RBAC tool drifts into production data authorization.
- The agent reports too much connection detail. Redaction must run before persistence.

## 6. Duplicate key breaks a model relationship

### Automation tier

Tier 3 never automate the data fix. Reporting and deterministic diagnostics are safe.

The safe action is to identify the affected table/key and write a data-quality flag. Automatically deleting, merging, filtering, or changing relationship cardinality can corrupt business meaning. This repo already demonstrates the correct pattern in `scenario2-data-quality`: deterministic scan, flag, notify, no remediation.

The accelerator can improve this playbook by adding semantic-model-aware duplicate diagnostics, but it must stop at evidence. If the source table is registered in scenario config, use the existing CSV duplicate scan. If not, use Power BI Execute Queries only for bounded counts and sample keys, never row contents.

### Concrete API

Optional diagnostic:

`POST https://api.powerbi.com/v1.0/myorg/groups/{groupId}/datasets/{datasetId}/executeQueries`

Verified permissions and limitations:

- Required scope: `Dataset.ReadWrite.All` or `Dataset.Read.All`.
- Tenant setting `Dataset Execute Queries REST API` under Integration settings must be enabled.
- Caller needs workspace access and dataset read/build permissions.
- Service principals require `Allow service principals to use Power BI APIs` under Developer settings.
- Service principals are not supported for datasets with RLS or SSO enabled.
- One DAX query per API call.
- One table per query.
- Maximum 100,000 rows or 1,000,000 values, and maximum 15 MB data per query.
- Limit is 120 query requests per minute per user.
- Only DAX queries are supported. MDX, INFO functions, and DMV queries are not supported.

No Microsoft Learn API was found that safely repairs duplicate keys in a source system or automatically changes model relationships in the Power BI service. XMLA/TOM could alter a model in Premium, but changing a relationship to make refresh pass is a semantic model design change and should not be an incident remediation.

### Tool definition

Name: `check_model_duplicate_keys`

Allowlist: `DIAGNOSTIC_ACTIONS`

Approval: not applicable; read-only.

Schema:

```json
{
  "type": "function",
  "function": {
    "name": "check_model_duplicate_keys",
    "description": "DIAGNOSTIC. For a duplicate-key relationship refresh failure, run a bounded deterministic duplicate-key check against a registered source table or a DAX query. Returns counts and sample key values only, not full rows.",
    "parameters": {
      "type": "object",
      "properties": {
        "table": {"type": "string"},
        "key_columns": {"type": "array", "items": {"type": "string"}},
        "max_sample_keys": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
        "justification": {"type": "string"}
      },
      "required": ["table", "key_columns"]
    }
  }
}
```

Existing reporting tool `write_data_quality_flag` remains in `REPORTING_ACTIONS`. Consider expanding its detail model rather than adding a second flag writer.

### Blast radius for `_impact_of`

No live model or source change. The diagnostic reads bounded aggregate duplicate evidence and sample key values. It does not return source row contents and does not change relationships or data.

### Required evidence before permitting it

- Error matches duplicate key, one-side relationship, or duplicate values playbook.
- Table and key columns come from scenario config, parsed error text, or a known relationship map. The model must not invent them.
- If using Execute Queries, refuse datasets with RLS or SSO when using service principal auth.
- Query must be generated from safe identifiers, not raw prompt text.
- Return counts and sample key values only.
- If duplicate count is zero, refuse `write_data_quality_flag` as the current code already does.

### Scenario sketch

```yaml
name: scenario-duplicate-key-model-evidence
title: "Duplicate key relationship - deterministic evidence"
description: >-
  A relationship refresh fails because the one side contains duplicates. The
  agent identifies duplicate keys and writes one data-quality flag. No model or
  source data is changed.
email: mock/emails/02-data-quality-failure.json

datasets:
  - name: well_production
    path: mock/data/well_production.csv
    key_columns: [well_id, production_date]

powerbi:
  workspace_id: b8e42d17-3f6a-4c9b-8d21-77e4a1c9f003
  dataset_id: 6f1c9b52-8a4d-4e7f-9c31-2b5a7d0e4411
  refresh_history:
    - {requestId: d1, status: Failed, refreshType: Scheduled, serviceExceptionJson: '{"errorCode":"DuplicateKeyInRelationship"}'}

expect:
  outcome: flagged_data_quality
  remediation_applied: false
  duplicate_key_check_ran: true
  dq_has_issue: true
  flags_written: 1
  blocked_attempts: 0
```

### What could go wrong

- The DAX query returns too many values or leaks row-level data.
- The agent checks the wrong table/key and writes a false flag.
- A tempting model change, such as changing relationship cardinality or filtering duplicates, makes refresh pass while changing report results.
- If service principal auth is used against an RLS or SSO dataset, Execute Queries is unsupported and may fail in ways the model could misread.
- Duplicate keys might be valid in the source and only invalid for this model relationship. The owner needs context.

## 7. Uncompressed data limit exceeded

### Automation tier

Tier 3 never automate. Escalate with evidence.

This is a hard size/SKU/model-design limit, not a transient refresh error. The possible fixes are reducing imported columns or rows, incremental refresh, aggregations, Direct Lake/DirectQuery design, or moving to a different capacity/SKU. The agent should not make model-shaping or capacity-cost decisions autonomously.

### Concrete API

No safe remediation API found.

Relevant APIs searched and rejected:

- `POST /groups/{groupId}/datasets/{datasetId}/refreshes`: triggering another refresh or enhanced refresh does not remove the hard uncompressed size limit.
- `executeQueries`: can collect bounded counts but cannot measure or reduce VertiPaq uncompressed model size generically.
- Power BI/Fabric connection and gateway APIs: not relevant to model size.
- Capacity/workspace assignment or Azure capacity scaling APIs may exist in other surfaces, but they are cost/governance operations and not safe incident remediations.

### Tool definition

Name: `collect_model_size_limit_evidence`

Allowlist: `DIAGNOSTIC_ACTIONS`

Approval: not applicable.

Schema:

```json
{
  "type": "function",
  "function": {
    "name": "collect_model_size_limit_evidence",
    "description": "DIAGNOSTIC. Collect refresh history, capacity mode if known, and source/model metadata for an uncompressed data limit failure. Does not retry, resize capacity, or change the model.",
    "parameters": {
      "type": "object",
      "properties": {
        "include_datasources": {"type": "boolean", "default": true},
        "justification": {"type": "string"}
      },
      "required": []
    }
  }
}
```

Reporting companion:

Name: `report_model_size_limit_exceeded`

Allowlist: `REPORTING_ACTIONS`

Schema:

```json
{
  "type": "function",
  "function": {
    "name": "report_model_size_limit_exceeded",
    "description": "REPORTING. Record that the semantic model exceeded a size limit and needs model or capacity owner action.",
    "parameters": {
      "type": "object",
      "properties": {
        "detail": {"type": "string"},
        "capacity_mode": {"type": "string"},
        "latest_request_id": {"type": "string"}
      },
      "required": ["detail"]
    }
  }
}
```

### Blast radius for `_impact_of`

No live environment change. The diagnostic records that the model hit a hard size limit and includes the failed request ID, error code, data source types, and capacity mode if known.

### Required evidence before permitting it

- Latest refresh failure matches uncompressed data limit or size limit playbook.
- `get_dataset_refresh_history` was called and the request ID is captured.
- If source metadata is collected, redact paths and connection details as needed.
- Controller must block `refresh_powerbi_dataset` and enhanced refresh for this signature unless a human explicitly changes the scenario; another refresh will not change the limit.
- Do not route to capacity scale-up automation.

### Scenario sketch

```yaml
name: scenario-uncompressed-limit-escalate
title: "Uncompressed data limit - no retry"
description: >-
  A refresh fails because the semantic model exceeds the uncompressed data
  limit. The controller gathers evidence and escalates without retrying or
  changing capacity.
email: mock/emails/11-uncompressed-limit.json

powerbi:
  workspace_id: b8e42d17-3f6a-4c9b-8d21-77e4a1c9f003
  dataset_id: 99999999-9999-4999-8999-999999999999
  capacity_mode: shared
  refresh_history:
    - {requestId: s1, status: Failed, refreshType: Scheduled, serviceExceptionJson: '{"errorCode":"UncompressedDataLimitExceeded"}'}

expect:
  outcome: needs_human
  remediation_applied: false
  size_limit_evidence_collected: true
  refresh_attempted: false
  flags_written: 0
  blocked_attempts: 0
```

### What could go wrong

- Retrying burns one of the daily shared-capacity refreshes and fails again.
- A capacity move or SKU increase fixes the alert but creates unmanaged cost or violates governance.
- Dropping columns or changing storage mode to pass refresh changes report semantics.
- The final message underplays urgency: reports are stale until a model or capacity owner acts.

## Prioritised build order

1. `inspect_powerbi_gateway_binding` and harden `rebind_dataset_gateway` with exact gateway/data-source IDs. Effort: 2-3 days. Customer value is high because gateway failures are common and the API already exists. It builds directly on the existing approval path.
2. `collect_datasource_credential_evidence` plus `report_credential_rotation_required`. Effort: 1-2 days. High value and low risk. It turns an unactionable credential alert into the exact owner task without handling secrets.
3. `collect_datasource_access_evidence` plus `report_datasource_access_required`. Effort: 1-2 days. Similar implementation to credential evidence. Keeps the agent away from unsafe permission grants.
4. `check_model_duplicate_keys` improvement. Effort: 2-4 days depending on whether DAX Execute Queries is added in addition to the existing CSV scan. Strong demo value because deterministic evidence outranks the model.
5. `trigger_powerbi_enhanced_refresh` for scheduled timeout, approval-gated. Effort: 3-5 days. Useful but needs capacity-mode evidence, polling, and careful timeout handling.
6. `trigger_powerbi_enhanced_refresh` OOM mode with forced `maxParallelism=1`, approval-gated. Effort: 2-3 days after item 5. Lower confidence as a remediation; position as mitigation only.
7. `collect_model_size_limit_evidence` plus `report_model_size_limit_exceeded`. Effort: 1 day. Important safety gap, but low remediation value because the correct outcome is escalation.

Dependencies:

- Items 2, 3, and 7 can share a `get_dataset_datasources` live/mock method.
- Items 1, 2, and 3 can share gateway metadata methods.
- Items 5 and 6 should share one enhanced refresh client method and one approval impact path.
- All remediation items need matching `MockPowerBIClient` calls and scenario `expect` fields before live code is added.

## Unsafe to automate

Genuinely unsafe as autonomous remediations:

- Expired or changed credentials.
- Access forbidden to the data source.
- Duplicate key relationship repair.
- Uncompressed data limit repair.
- Capacity scale-up or workspace movement as a response to OOM or size-limit failures.
- Gateway data source user grants unless designed as a separate approval-gated permission workflow.

Conditionally safe only behind human approval:

- Gateway rebind.
- Enhanced refresh for timeout.
- Low-parallelism enhanced refresh for OOM mitigation.

Safe Tier 1 using existing tooling only when evidence says transient:

- A single gateway unreachable failure after recent success.
- An isolated timeout where a normal retry is justified by history.
