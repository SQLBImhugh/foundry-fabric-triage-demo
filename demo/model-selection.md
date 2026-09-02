# Choosing the demo model

Both candidates were run against all nine scenarios, with tools mocked so the
only variable is the model and the provider pointed at real Foundry so the
reasoning is real. The scenarios carry `expect` blocks, so "did it behave
correctly" is a deterministic check rather than a judgement call.

Reproduce with `python demo/scripts/compare_models.py`.

## Result

Measured 2026-09-01, one run each.

| Scenario | gpt-5.6-luna | gpt-5.4 |
|---|---|---|
| scenario1-transient | pass, 41s | pass, 49s |
| scenario2-data-quality | pass, 43s | pass, 48s |
| scenario2b-known-issue | pass, 61s | pass, 54s |
| scenario3-policy-block | pass (downgraded), 42s | pass (downgraded), 47s |
| scenario4-unknown-action | pass (downgraded), 38s | pass (downgraded), 41s |
| scenario5-approval-granted | pass, 59s | pass, 48s |
| scenario6-approval-denied | pass, 49s | pass, 55s |
| scenario7-schedule-reenable | pass, 56s | pass, 49s |
| scenario8-capacity-backoff | pass, 48s | pass, 42s |
| **Total** | **9/9, 438s, mean 48.7s** | **9/9, 431s, mean 47.9s** |

The latency gap has closed since the seven-scenario measurement, where
`gpt-5.6-luna` led 41.3s to 44.5s. On this run `gpt-5.4` was marginally quicker.
One run each is not enough to call that a real difference in either direction —
treat both as "about fifty seconds a scenario" and choose on behaviour.

**`gpt-5.4` only passes scenarios 7 and 8 if its agent pair has been
re-registered.** The fallback agents are a second registration that nothing
updates automatically: on 2026-09-01 `bi-triage-54` was still at version 1
carrying nine tools, missing `get_refresh_schedule`, `reenable_refresh_schedule`
and `defer_refresh_retry`, so both new scenarios would have failed on it. After
a tool-schema change, register **every** variant:

```powershell
python scripts\register_foundry_agents.py
python scripts\register_foundry_agents.py --model gpt-5.4 `
    --triage-name bi-triage-54 --dq-name bi-data-quality-54
```

## The difference that matters

Both models reach the correct final outcome on every scenario. They differ in
whether the controller has to *make* them correct.

On `scenario3-policy-block` the controller refuses a second remediation, because
policy allows one write action per run. The refusal is returned to the agent as
data, so it can still escalate.

- **gpt-5.6-luna** accepted the refusal and reported `needs_human` itself.
- **gpt-5.4** claimed `resolved` after being refused. The controller downgraded
  it:

  ```
  Controller downgraded 'resolved' -> 'needs_human' because the evidence did
  not support the claim.
  ```

Both runs *end* correctly, and that is worth being precise about: the downgrade
is the safety net working exactly as designed. Deterministic evidence outranks
model output, and this is what that rule is for. A model claiming success after
being blocked is caught, every time, because the check is arithmetic rather than
persuasion.

`scenario4-unknown-action` downgrades under both models, which is expected — it
deliberately induces an action outside the allowlist.

## Decision

**Keep `gpt-5.6-luna`.**

- Slightly faster overall (289s vs 312s), which matters when a room is watching.
- Needed one fewer controller correction. It honoured a policy refusal instead
  of asserting success over it.

`gpt-5.4` is a perfectly viable fallback: it passed every scenario, and the one
place it overclaimed was caught. If Luna capacity is unavailable on the day,
switch with:

```powershell
python scripts/register_foundry_agents.py --model gpt-5.4 `
    --triage-name bi-triage-54 --dq-name bi-data-quality-54

$env:FOUNDRY_TRIAGE_AGENT_NAME = "bi-triage-54"
$env:FOUNDRY_DQ_AGENT_NAME     = "bi-data-quality-54"
```

Both deployments and both agent pairs are left in place so the comparison can be
re-run and so the fallback needs no provisioning under time pressure.

## Caveat

Nine scenarios, one run each, is a small sample. The latency numbers include
tool execution and are indicative rather than a benchmark. The behavioural
difference on `scenario3` was reproduced separately and is the finding worth
trusting; treat the timings as a tiebreaker, not a result.

Passing is only evidence about the agent version that was registered when the
comparison ran. Re-run it after a tool-schema change rather than citing an older
table, and note that the fallback pair drifts silently because nothing
re-registers it.
