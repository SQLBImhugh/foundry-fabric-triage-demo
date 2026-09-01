# Security

## Reporting a vulnerability

Do not report security vulnerabilities through public GitHub issues.

Report them privately through [GitHub private vulnerability
reporting](https://docs.github.com/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
on this repository, or to the Microsoft Security Response Center at
<https://msrc.microsoft.com/create-report> if the issue affects a Microsoft
product or service rather than this sample.

Include what you would need yourself: the type of issue, the file and line, the
configuration required to reproduce it, and what an attacker gains. Redact any
credential before sending it.

## What this accelerator assumes

It is a sample. Review these before running it against anything you care about:

- **The mailbox filter is a security control.** An agent that acts on every
  message it receives is steerable by anyone who can email it. The filter fails
  closed, including when its own pattern is invalid, and counts what it ignored
  rather than dropping it silently. Do not widen it to make a demo find
  something; send a matching message instead.
- **App-only `Mail.Read` is tenant-wide by default.** Scope it to the alerts
  mailbox with an Exchange `ApplicationAccessPolicy`. The controller verifies
  this at startup against a canary mailbox and refuses to ingest if it can read
  one it should not.
- **Action allowlists bound the blast radius.** Anything not in
  `REMEDIATION_ACTIONS`, `REPORTING_ACTIONS` or `DIAGNOSTIC_ACTIONS` is refused
  before dispatch, whatever the model asked for.
- **The Teams webhook URL and the approval callback URL are bearer
  credentials.** Anyone holding either can post to the channel or answer an
  approval. Keep them in the `azd` environment, never in the repository, and
  regenerate them after sharing a demo.
- **Secrets expire.** Entra application secrets are commonly purged on a
  schedule. Prefer managed identity or a federated credential; a design that
  depends on a long-lived secret fails about a month after go-live.

## What is deliberately not a secret

Prompts, tool schemas, action allowlists and policy limits are all readable.
The security properties do not depend on any of them being hidden.
