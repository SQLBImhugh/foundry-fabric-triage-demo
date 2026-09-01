# Support

## How to get help

Open a GitHub issue on this repository. Use the bug report template for
something broken and the feature request template for something missing.

## Support boundary

This is a solution accelerator: sample code intended to be read, adapted and
deployed into your own subscription. It is provided as-is under the
[MIT License](LICENSE), and is **not** a supported Microsoft product or service.

Issues are handled on a best-effort basis by the maintainers. There is no
service level agreement, and no commitment to fix, respond within a period, or
maintain compatibility across versions.

For problems with the underlying Azure services — Azure AI Foundry, Power BI,
Microsoft Fabric, Microsoft Graph, Teams — raise a support request through the
Azure portal or the Microsoft 365 admin centre. Those have real support paths;
this repository does not.

## Before opening an issue

Most failures here are configuration rather than code:

```powershell
.\.venv\Scripts\triage-demo.exe preflight          # what is configured, what is missing
.\.venv\Scripts\triage-demo.exe identity --check-scope
.\.venv\Scripts\python.exe -m pytest -q            # does it work offline?
```

If the offline suite passes and a live run does not, the difference is your
tenant configuration. `docs/provisioning.md` lists what must exist, and
`docs/run-sheet.md` has a recovery table for the common failures.
