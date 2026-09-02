# Silent failure detector design scope

## Scope and recommendation

Add a scheduled silent-failure sweep that inspects configured Power BI semantic models even when no refresh-failure email exists. The sweep should be a deterministic scanner owned by the controller, not a third prompt agent and not an extension of the current Data Quality agent. It should create a synthetic incident only when deterministic evidence crosses a configured threshold and survives a confirmation pass.

The first useful increment should cover one configured import semantic model with one date watermark, one row-count probe, and one schema fingerprint. It should report only; no automatic remediation should be added in the first phase.

## Repository evidence checked

I read `AGENTS.md`; `docs/architecture.md`, `docs/hosted-architecture.md`, and `docs/plan.md`; `src/triage_demo/tools/powerbi.py`; `src/triage_demo/tools/registry.py`; `src/triage_demo/policy.py`; `src/triage_demo/agents/data_quality_agent.py`; `src/triage_demo/knowledge/playbooks.py`; the durable store modules in `src/triage_demo/store/`; and all current scenario YAML files.

Important code constraints:

- Policy and allowlists are enforced in the controller, not in prompts.
- Every new tool name must be in `REMEDIATION_ACTIONS`, `REPORTING_ACTIONS`, or `DIAGNOSTIC_ACTIONS` before dispatch.
- Deterministic evidence outranks model output.
- State that must survive hosted-agent invocations must live in a store, not on an object.
- Current scenarios are alert-driven. Every scenario starts with a mock email.
- Current Power BI live code has refresh history and refresh trigger only. It does not yet query semantic model data, refresh schedules, or model metadata.
- Current Data Quality agent scans local CSV fixtures deterministically and then asks a model to write the explanatory sentence. That split should be preserved.

The graph coverage check reported `metadata_changed` for the files above, so I relied on direct source reads rather than graph-only evidence.

## Microsoft documentation checked

Claims below are grounded in these Microsoft Learn pages:

- Execute DAX queries in a workspace: https://learn.microsoft.com/rest/api/power-bi/datasets/execute-queries-in-group
- Execute DAX queries outside a group: https://learn.microsoft.com/rest/api/power-bi/datasets/execute-queries
- Get refresh history in a workspace: https://learn.microsoft.com/rest/api/power-bi/datasets/get-refresh-history-in-group
- Get refresh schedule in a workspace: https://learn.microsoft.com/rest/api/power-bi/datasets/get-refresh-schedule-in-group
- Trigger semantic model refresh: https://learn.microsoft.com/rest/api/power-bi/datasets/refresh-dataset
- Power BI data refresh concepts and limits: https://learn.microsoft.com/power-bi/connect-data/refresh-data
- XMLA endpoint connectivity: https://learn.microsoft.com/power-bi/enterprise/service-premium-connect-tools
- Service principals with Premium semantic models and XMLA: https://learn.microsoft.com/power-bi/enterprise/service-premium-service-principal
- Analysis Services DMVs, including Fabric/Power BI Premium applicability: https://learn.microsoft.com/analysis-services/instances/use-dynamic-management-views-dmvs-to-monitor-analysis-services
- Service principal setup and tenant settings: https://learn.microsoft.com/power-bi/developer/embedded/embed-service-principal

One uncertainty remains: Microsoft Learn documents service principals and app identities, but I did not find a Microsoft Learn page that specifically names Foundry Entra agent identities as Power BI callers. This repository has live-tenant evidence in `docs/hosted-architecture.md` that Power BI accepts the controller's Entra agent identity as a workspace principal. Treat that as project evidence, not a public documentation claim.

## Power BI and Fabric API facts that shape the design

### REST `executeQueries`

Use the Power BI REST Execute Queries endpoint for the first implementation because the detector only needs scalar DAX results:

`POST https://api.powerbi.com/v1.0/myorg/groups/{groupId}/datasets/{datasetId}/executeQueries`

The request body contains one DAX query. A health probe should return one row:

```DAX
EVALUATE
ROW(
    "MaxBusinessDate", MAX('FactSales'[BusinessDate]),
    "FactSalesRows", COUNTROWS('FactSales'),
    "NetSales", [Net Sales]
)
```

Real limits from Microsoft Learn:

- The tenant setting **Dataset Execute Queries REST API** must be enabled.
- The caller needs workspace access plus dataset read and build permissions.
- Required scope is `Dataset.Read.All` or `Dataset.ReadWrite.All`.
- One query per API call.
- One result table per query.
- Maximum 100,000 rows or 1,000,000 values per query.
- Maximum 15 MB per query.
- Limit is 120 query requests per minute per user, regardless of dataset.
- Service principals require **Allow service principals to use Power BI APIs**.
- Service principals are not supported for datasets with RLS or SSO enabled.
- Only DAX queries are supported. MDX, INFO functions, and DMV queries are not supported through this REST endpoint.

The detector should therefore never fetch detail rows for normal operation. It should issue small `ROW(...)` queries that return scalar counts, dates, and totals.

### REST refresh history and schedule

Use refresh history to decide whether a semantic model recently reported success, not to maintain baselines:

`GET https://api.powerbi.com/v1.0/myorg/groups/{groupId}/datasets/{datasetId}/refreshes?$top=N`

Real limits from Microsoft Learn:

- Required scope is `Dataset.Read.All` or `Dataset.ReadWrite.All`.
- OneDrive refresh history is not returned.
- In-group documentation states the caller must have Write permissions on the dataset.
- Only 20-60 refresh history entries are available depending on refreshes in the last three days. Entries older than three days are deleted when there are more than 20 entries.

Use refresh schedule to derive cadence when the model has a Power BI schedule:

`GET https://api.powerbi.com/v1.0/myorg/groups/{groupId}/datasets/{datasetId}/refreshSchedule`

The response includes `days`, `times`, `enabled`, `localTimeZoneId`, and `notifyOption`. The schedule object is for imported models. Service principals only support `NoNotification` for the schedule notification option.

Power BI data-refresh documentation adds:

- Import models need data refresh because they import source data.
- DirectQuery, Direct Lake, live connection, and push models have different refresh behavior and should not be treated as the same stale-data problem.
- Shared capacity allows eight scheduled refreshes per day. Premium, PPU, or Fabric capacity allows up to 48 scheduled refreshes per day in settings.
- On-demand and programmatic refreshes do not trigger email notifications.
- The service sends email for refresh failures and schedule pauses, but that does not cover successful refreshes with stale or collapsed data.

### XMLA endpoint

Use XMLA for model metadata and schema-drift detection when available. Do not require it for the first freshness and row-count implementation.

Real limits from Microsoft Learn:

- XMLA endpoint connectivity is for Power BI Premium, Premium Per User, and Power BI Embedded workspaces.
- Read-only XMLA connectivity is enabled by default for the semantic models workload in a capacity.
- Read-write XMLA must be explicitly enabled for write operations.
- XMLA access also depends on the tenant integration setting **Allow XMLA endpoints and Analyze in Excel with on-premises semantic models**.
- A single-user app identity needs a valid PPU license unless the semantic model resides on Premium capacity.
- Build permission is required for read access through XMLA.
- Service principals can be used with XMLA in new workspaces when service principal access is enabled and the principal is assigned workspace access.
- Service principals do not work with RLS and OLS and cannot be model role members.
- DMVs are available through XMLA for Fabric/Power BI Premium, but Power BI Premium semantic models are limited to DMVs that require database-admin permissions. DMV query syntax is not full SQL; JOIN, GROUP BY, LIKE, CAST, and CONVERT are not supported.
- Relevant schema rowsets include `TMSCHEMA_TABLES`, `TMSCHEMA_COLUMNS`, `TMSCHEMA_MEASURES`, `TMSCHEMA_PARTITIONS`, and `DISCOVER_CALC_DEPENDENCY`.

XMLA is the right surface for schema fingerprints because REST `executeQueries` explicitly does not support DMV queries or INFO functions.

## Detection mechanisms

### Case 1: stale-but-successful

Definition: Power BI says the semantic model refresh completed, but a configured business-date watermark inside the model did not advance when it should have.

Query sequence:

1. Read refresh schedule with `GET /groups/{workspaceId}/datasets/{datasetId}/refreshSchedule`.
2. Read recent refresh history with `GET /groups/{workspaceId}/datasets/{datasetId}/refreshes?$top=20`.
3. Execute a configured DAX scalar probe through `executeQueries`.
4. Compare with stored baseline and cadence.

Example probe configuration:

```yaml
silent_health:
  probes:
    - name: sales-freshness
      workspace_id: <guid>
      dataset_id: <guid>
      table: FactSales
      date_column: BusinessDate
      row_count_table: FactSales
      control_measures: [Net Sales]
      expected_lag: P1D
      grace_minutes: 90
      confirmation_scans: 2
```

Computable stale rule:

A model is stale only when all of these are true:

- The model is in a supported storage mode for the configured probe. Phase 1 should target Import semantic models only.
- The latest relevant refresh has status `Completed`.
- The latest completed refresh ended after the expected source-arrival time plus grace.
- The DAX probe completed successfully.
- `current_max_date < expected_watermark`.
- The same condition appears in `confirmation_scans` consecutive sweeps, or a second immediate confirmation scan after a jittered delay.

`expected_watermark` must not be a constant. Derive it in this order:

1. Explicit probe configuration. This is required for models whose business date does not match the refresh schedule, such as T+1 finance data, weekend-skipping operational data, and month-end close models.
2. Power BI refresh schedule. Convert `days` and `times` in `localTimeZoneId` to expected refresh windows and subtract `expected_lag`.
3. Learned baseline. After at least N healthy observations, compute the median interval between increases in `max(date)`, segmented by weekday if enough observations exist. Use this only as a fallback and mark the probe as `learned_cadence` in evidence.

Recommended default for an unconfigured daily import model: alert only if the watermark is older than two expected refresh windows plus grace. That is conservative, but false positives are worse than silence for this detector.

### Case 2: partial load or row-count collapse

Definition: Power BI says refresh completed, but one or more monitored tables now have far fewer rows or much lower control totals than their own recent baseline.

Query sequence:

1. Read latest completed refresh as above.
2. Execute DAX scalar probes:

```DAX
EVALUATE
ROW(
    "FactSalesRows", COUNTROWS('FactSales'),
    "MaxBusinessDate", MAX('FactSales'[BusinessDate]),
    "NetSales", [Net Sales]
)
```

3. Compare each metric to a rolling baseline stored by probe.

Computable collapse rule:

- `current_row_count < baseline_row_count * (1 - max_drop_percent)`; default `max_drop_percent = 0.30`.
- `baseline_row_count - current_row_count >= min_absolute_drop`; default must be configured per probe or set high enough for demo data.
- The comparison uses a like-for-like baseline bucket: same table, same probe, same day-of-week or same period type where configured.
- Optional control totals must agree with the row-count direction before severity is high. If rows drop but totals do not, report medium confidence and require confirmation.
- The finding persists across a confirmation scan.

The baseline should keep rolling windows, not one prior value. Store count, median, median absolute deviation, min, max, and last healthy values. For business data with natural seasonality, allow a `bucket_key` expression such as weekday, calendar day, or month-end period. Do not learn seasonality from fewer than four comparable observations.

### Case 3: schema drift

Definition: a table, column, relationship, or measure contract changed so dependent measures or visuals can break or go blank without a refresh-failure email.

Use two layers.

Layer 1, metadata fingerprint through XMLA:

- Connect to `powerbi://api.powerbi.com/v1.0/myorg/<workspace>` with an app identity that has workspace access and Build permission.
- Query DMVs such as:

```sql
SELECT * FROM $System.TMSCHEMA_TABLES
SELECT * FROM $System.TMSCHEMA_COLUMNS
SELECT * FROM $System.TMSCHEMA_MEASURES
SELECT * FROM $System.TMSCHEMA_RELATIONSHIPS
SELECT * FROM $System.DISCOVER_CALC_DEPENDENCY
```

- Build a stable fingerprint from names, data types, hidden flags for monitored objects, measure expressions, and dependency rows.
- Compare to the last accepted baseline.

Layer 2, contract DAX through REST `executeQueries`:

- For each monitored measure or critical column, run a scalar query that references it.
- Treat DAX query failure as a deterministic schema or calculation contract failure.
- Treat blank critical measures as suspect only when configured as non-null and confirmed by a second scan. Blank can be legitimate.

If XMLA is unavailable, the detector can still run Layer 2. It must then report `schema_metadata_available=false` and cannot claim exhaustive schema-drift coverage. REST `executeQueries` cannot run DMV or INFO queries, so it cannot replace XMLA metadata inspection.

Visual-level blank detection is out of scope for Phase 1. The Power BI REST surfaces above can prove that model objects changed or measures return blank; they do not by themselves prove that a particular visual rendered blank. Add report-definition inspection later only if the accelerator standardizes on PBIP/PBIR or a supported report metadata API surface for the target tenant.

## Trigger model

This is a poll/schedule path, not a mailbox path.

Recommended trigger:

- Add a new controller sentinel, `silent-sweep`, parallel to the current `sweep` mailbox sentinel.
- Run it from an external scheduler that is already trusted in the deployment environment, such as an Azure Function timer, Logic App recurrence, Container Apps job, or enterprise scheduler.
- Keep the code path idempotent with durable scan state and a per-probe lock.
- Keep the existing five-minute Foundry routine only as a preview/manual path, not as the production trigger.

Repository evidence says the current Foundry routine is registered and enabled but does not fire. `docs/hosted-architecture.md` also records that `azd ai routine list` can say no routines exist when `routine show` finds one. The microsoft-foundry routine skill confirms routines are preview and managed through `azd ai routine` or `host: azure.ai.routine` services.

A deploy can re-enable a disabled routine. Therefore the disable switch for this detector must be in controller configuration, not routine state. Add a setting such as `SILENT_SWEEP_ENABLED=false` and have the hosted controller return a no-op result before any Power BI call when disabled.

Recommended cadence:

- Default external poll every 15 minutes for the scanner itself.
- Per-probe evaluation only after the next expected refresh window plus grace.
- Add jitter per model to avoid calling all semantic models at the top of the hour.
- Respect the Execute Queries 120 requests/minute/user limit by budgeting queries per sweep. With three probes per model, keep the first demo under ten models or add a queue.

## Architectural placement

Place this as a deterministic scanner in the hosted controller, exposed to the orchestrator as read-only diagnostic evidence only when it emits an incident.

Do not make it a third prompt agent in Phase 1:

- The core task is measurement: max date, row count, totals, schema fingerprint, and deltas. These are deterministic facts.
- Invariant 6 says deterministic evidence outranks model output.
- A prompt agent deciding that a row-count collapse is acceptable would weaken the safety story.
- A third prompt agent increases token and turn budgets without adding value to the first detection loop.

Do not fold it into the existing Data Quality agent:

- The Data Quality agent is scoped to data-quality interpretation after a BI request exists.
- Silent detection is an ingestion path. It creates the request equivalent.
- The DQ agent currently scans local dataset fixtures. This new scanner queries Power BI/Fabric service surfaces and stores baselines.

Proposed flow:

```text
External scheduler
  -> hosted controller "silent-sweep"
      -> SilentFailureScanner, deterministic
          -> Power BI REST schedule/history
          -> Power BI REST executeQueries
          -> XMLA metadata when configured and available
          -> SemanticHealthStore baseline comparison
      -> if healthy: update baseline and exit
      -> if suspect once: record suspect state, no notification
      -> if confirmed: create silent-failure incident and notify once
      -> optional: hand a synthetic BIRequest to TriageAgent for explanation/escalation only
```

The scanner should own the evidence and classification. The Triage agent can write the human-readable explanation and choose existing reporting tools, but it must not be allowed to override measured status.

## New tool surface

Keep the model-facing tool surface small. Do not expose arbitrary DAX to the prompt agent. The controller should construct DAX only from stored probe configuration.

### `scan_semantic_model_health`

Allowlist: `DIAGNOSTIC_ACTIONS`.

Reason: read-only. It queries Power BI and compares against stored baseline but does not mutate the BI environment. If implementation records suspect state as part of the scan, keep that write inside the scanner store and audit it; it is not a remediation.

Schema:

```json
{
  "type": "function",
  "function": {
    "name": "scan_semantic_model_health",
    "description": "Run configured silent-failure probes for a semantic model and return deterministic freshness, row-count, and schema findings. The controller builds the queries; the model cannot supply DAX.",
    "parameters": {
      "type": "object",
      "properties": {
        "workspace_id": {"type": "string", "description": "Power BI workspace id."},
        "dataset_id": {"type": "string", "description": "Power BI semantic model id."},
        "probe_names": {
          "type": "array",
          "items": {"type": "string"},
          "description": "Optional subset of configured probes. Empty means all probes for the semantic model."
        },
        "reason": {"type": "string", "description": "Why the scan is being run."}
      },
      "required": ["workspace_id", "dataset_id"]
    }
  }
}
```

### `record_semantic_health_baseline`

Allowlist: `REPORTING_ACTIONS`.

Reason: writes detector state only. It does not change Power BI, source data, or Fabric artifacts. It must not consume remediation budget.

Schema:

```json
{
  "type": "function",
  "function": {
    "name": "record_semantic_health_baseline",
    "description": "Persist an accepted health observation as the new baseline after a scan is healthy or a human marks a suspect as expected.",
    "parameters": {
      "type": "object",
      "properties": {
        "workspace_id": {"type": "string"},
        "dataset_id": {"type": "string"},
        "probe_name": {"type": "string"},
        "observation_id": {"type": "string"},
        "accepted_by": {"type": "string", "description": "system or human identifier; no personal data required in tests."},
        "reason": {"type": "string"}
      },
      "required": ["workspace_id", "dataset_id", "probe_name", "observation_id", "reason"]
    }
  }
}
```

### Existing reporting

Use the existing `notify_teams` reporting tool. Do not create a second notifier for silent failures. Deduplication already suppresses repeat announcements, which is essential for a detector that polls.

### No new remediation action in Phase 1

Do not add `refresh_stale_dataset` or `repair_schema_drift` initially. A stale detector with false positives can create alert fatigue; a stale detector with auto-remediation can create churn and mask source failures. The first release should detect, persist, and notify.

## State required

Add a store module following the existing `store/` patterns, for example `src/triage_demo/store/semantic_health.py`.

Interfaces:

```python
class SemanticHealthStore(Protocol):
    def get_probe_state(self, workspace_id: str, dataset_id: str, probe_name: str) -> SemanticProbeState | None: ...
    def record_observation(self, observation: SemanticHealthObservation) -> None: ...
    def accept_baseline(self, observation_id: str, *, reason: str, accepted_by: str = "system") -> None: ...
    def record_suspect(self, finding: SilentFailureFinding) -> int: ...
    def reset(self) -> None: ...
```

Backends:

- `InMemorySemanticHealthStore` for unit tests.
- `JsonFileSemanticHealthStore` for offline rehearsal.
- `AzureTableSemanticHealthStore` for hosted deployment.

Azure Table design:

- Table name: `semantichealth` or `semanticbaselines`.
- Partition key: stable hash or safe key of `{workspace_id}:{dataset_id}`.
- Row key: `{probe_name}` for current probe state and `{probe_name}:{observation_id}` for observations if observations are kept separately.
- Payload JSON remains authoritative, with promoted columns for `status`, `last_observed_at`, `last_max_date`, `last_row_count`, `schema_fingerprint`, `suspect_count`, and `last_alert_signature`.
- Use Entra authentication through the controller's credential chain, not storage account keys.
- Redaction should live at the store boundary if any source error text is persisted.

State fields per probe:

- Workspace id and dataset id.
- Probe name and version.
- Query template version or generated-query hash.
- Date table/column and row-count table.
- Expected lag, grace, confirmation count, and configured cadence.
- Last accepted max date.
- Rolling row-count and control-total statistics by bucket.
- Last schema fingerprint and metadata availability.
- Last completed refresh id and time seen.
- Consecutive suspect count and first suspect time.
- Last emitted incident signature.
- Last scan error and detector-health status.

Baselines must be updated only from healthy observations or explicit acceptance. Never update the baseline from a suspect scan, because that trains the detector to accept the failure.

## Offline testability

The test suite must stay fully offline. Add no default test path that calls Power BI, XMLA, Azure Storage, Foundry, or Graph.

Implementation pattern:

- Extend `PowerBIClient` with read-only methods, or introduce a separate `SemanticModelHealthClient` protocol so the refresh client stays small.
- Add mock methods that return scripted refresh schedules, refresh histories, DAX rows, query errors, and XMLA schema rows.
- Add scenario YAML blocks under `silent_health:` with expected findings.
- Keep all calculations in pure Python and freeze time in tests.
- Tests should assert tool sequence, emitted finding, baseline update behavior, dedup, and that no remediation occurred.
- Add policy tests proving new tool names are allowlisted in the correct category and unknown scanner actions are refused.
- Add store tests for JSON and in-memory backends; mock Azure Table rather than reaching Azure.
- Keep DAX strings generated deterministically from configuration and assert on the generated string in unit tests.

Minimum offline scenarios:

1. Healthy daily model: completed refresh, max date advanced, row count normal, schema unchanged; baseline updated, no notification.
2. Stale but successful: completed refresh, max date unchanged beyond grace; first scan suspect only, second scan emits one incident.
3. Row-count collapse: completed refresh, count drops 60 percent and control total drops; emits confirmed finding.
4. Small table noise: count drops from 7 to 4 but below absolute threshold; no finding.
5. Schema drift with XMLA: column missing or measure expression changed; schema fingerprint differs and contract DAX fails.
6. XMLA unavailable: schema metadata marked unavailable; freshness and row-count probes still run; no exhaustive schema claim.
7. ExecuteQueries RLS/SSO unsupported for service principal: detector-health finding, not a data-quality incident.
8. Duplicate poll: same confirmed finding does not post a second Teams card because the incident signature is open and already notified.

## Detector failure modes and bounding rules

False positives are worse than silence for this feature. Bound them explicitly:

- Require per-probe configuration for business calendars, expected lag, and criticality. Do not infer that every model should be current today.
- Require confirmation before notification. One anomalous scan records suspect state only.
- Use both relative and absolute row-count thresholds.
- Compare against like-for-like baseline buckets, not a single prior run, for weekly or month-end seasonality.
- Do not alert while a refresh is in progress or the latest refresh status is `Unknown`.
- Do not alert if the detector cannot query the model. Record detector health and notify only after repeated detector failures on a separate, lower-severity channel.
- Do not update baselines from suspect data.
- Do not run arbitrary DAX supplied by the model. Queries are generated from configuration.
- Rate-limit per workspace and per semantic model to stay under Execute Queries throttling and avoid capacity noise.
- Mark unsupported combinations explicitly: service principal plus RLS or SSO, XMLA disabled, XMLA unavailable on non-Premium/PPU/Embedded capacity, DirectQuery/Direct Lake models without configured query semantics.
- Include quiet-hours or maintenance-window configuration.
- Emit one incident per stable signature and rely on existing `notified_count` suppression for repeats.
- Store detector-health failures separately from business-data failures. A broken detector is not evidence that the report is stale.

## MCAPS and identity constraints

Use managed identity or the Foundry agent identity wherever Power BI and Azure accept it. Do not design around long-lived client secrets. Repository evidence says the controller's Foundry agent identity can call Power BI and Azure Table, while Exchange mail still needed a conventional app registration.

For storage:

- Use Azure Table with Entra auth.
- Do not use account keys or shared-key connection strings.
- Expect `allowSharedKeyAccess=false`.
- Expect public network access to be forced private. The hosted controller or scheduler must run from an allowed network path or use a supported exemption tag with justification for a demo environment.

For Power BI:

- Enable the tenant setting for service principals to use Power BI/Fabric APIs, ideally scoped to a security group.
- Enable Execute Queries REST API tenant setting.
- Add the controller identity or its containing security group to the workspace with the required semantic model permissions.
- Enable XMLA tenant and capacity settings before relying on metadata fingerprints.
- Treat RLS/SSO datasets as unsupported for app-only Execute Queries unless a delegated design is explicitly chosen. A delegated design is not recommended for this unattended detector.

## Build status

Phases 1 to 5 are built. What was delivered differs from this plan in four
places, each for a reason worth keeping:

| Phase | State | Deviation from the plan below |
|---|---|---|
| 1 | Done | Built as a deterministic detector, **not** as the `scan_semantic_model_health` and `record_semantic_health_baseline` agent tools. A model asked whether a 60% row drop is acceptable will sometimes say yes; measured evidence has to outrank model output |
| 2 | Done | — |
| 3 | Done | Uses DAX `INFO.VIEW` over the existing read-only endpoint instead of XMLA. XMLA needs ADOMD or TOM, so in practice a .NET dependency and a Windows host; the controller runs in a Linux container. Only **removals** are reported, because a detector that fires on ordinary model development gets switched off |
| 4 | Done | Probe configuration is an environment value rather than a file or table, so it deploys with the agent. Maintenance windows are expressed as `load_weekdays` rather than arbitrary calendars |
| 5 | Done | Sweeps are paced and sequential rather than a concurrent queue: `executeQueries` is throttled per user across all datasets, so concurrency would make the detector the incident it watches for. Preflight validates configuration; live tenant checks are the sweep's own job |

Two behaviours were added that this scope did not anticipate, both because they
were needed in practice:

- **A circuit breaker.** A probe against a Direct Lake model reached twelve
  consecutive 401s, re-querying a model app-only callers are never permitted to
  read. It is now parked after repeated faults and retried on a cooldown.
- **A `date_table` option.** A star-schema fact table holds a date *key* and no
  date, so freshness could not be expressed for it at all. The obvious
  workaround — probe the date dimension — returned `2030-12-31` on a real model
  whose data stopped in 2024, which would have reported a stale model as fresh
  for ever.

## Phased build order and rough effort

### Phase 1: deterministic health scanner, one model

Effort: 1.5-2 engineer-days.

Deliverables:

- `SemanticModelHealthClient` protocol with mock implementation.
- DAX scalar probe generation for max date, row count, and optional control totals.
- Refresh schedule and refresh history reads in the live client.
- `SemanticHealthStore` in memory and JSON file.
- `silent-sweep` controller path.
- Offline scenarios for healthy, stale, row collapse, and duplicate notification suppression.
- No XMLA and no remediation.

Exit: a daily import model can be detected as stale or collapsed without mailbox input, fully offline in tests.

### Phase 2: hosted durable state and external schedule

Effort: 1-1.5 engineer-days.

Deliverables:

- Azure Table backend for semantic health state.
- External scheduler wiring for `silent-sweep`.
- `SILENT_SWEEP_ENABLED` fail-closed kill switch.
- Per-probe locking or lease to prevent overlapping sweeps.
- App Insights metadata-only spans for probe results and detector health.

Exit: hosted controller can run silent sweeps unattended without depending on the preview Foundry routine.

### Phase 3: XMLA schema fingerprint

Effort: 2-3 engineer-days.

Deliverables:

- XMLA client path using ADOMD/TOM-compatible library or a small command wrapper selected for Python/Windows/Linux support.
- DMV extraction for tables, columns, measures, relationships, and calculation dependencies.
- Stable schema fingerprint and baseline comparison.
- Contract DAX fallback when XMLA is unavailable.
- Offline XMLA row fixtures.

Exit: a renamed or removed monitored column produces a deterministic schema-drift finding, with clear `metadata_available` evidence.

### Phase 4: configuration and acceptance workflow

Effort: 1-2 engineer-days.

Deliverables:

- Probe configuration file or table schema.
- CLI commands to list probes, show baseline, accept a new baseline, and mark a finding as expected.
- Maintenance windows and business calendars.
- Documentation for onboarding a new model.

Exit: users can add a model without changing code, and they can safely accept planned changes without teaching the detector bad data.

### Phase 5: solution accelerator hardening

Effort: 3-5 engineer-days.

Deliverables:

- Multi-workspace scan queue with throttling.
- Circuit breakers for repeated detector failures.
- Tenant preflight checks for service principal settings, Execute Queries, XMLA, workspace access, RLS/SSO unsupported cases, and capacity type.
- Security review and least-privilege deployment guidance.
- Expanded docs and demo walkthrough updates.

Exit: suitable as a reusable accelerator pattern, not only a single demo path.

## Single biggest risk

The biggest risk is not the DAX or the scheduler. It is baseline quality. A stale detector needs to know what “fresh” means for each model, and that is business-specific. If cadence, lag, holidays, and expected row-volume variance are guessed, the detector will generate false positives and be ignored. Make probe configuration and conservative confirmation rules part of the first build, not polish for later.
