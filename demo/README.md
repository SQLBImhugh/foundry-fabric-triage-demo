# Demo material

Everything here exists to **present** the accelerator. None of it is needed to
deploy or adapt it — if you are adopting this into your own tenant, start at the
[README](../README.md) and [`docs/`](../docs/).

It is kept separate for one reason: a person evaluating the accelerator should
not have to work out which files are the product and which are the show.

## What is here

| Path | What it is |
|---|---|
| [`run-sheet.md`](run-sheet.md) | The live demo script, timed, with recovery steps |
| [`faq.md`](faq.md) | Question prep — what gets asked in the room, and the answers |
| [`model-selection.md`](model-selection.md) | Which model the demo uses, measured rather than assumed |
| [`handoff.md`](handoff.md) | What to give an audience afterwards, and what must not go in it |
| [`walkthrough/`](walkthrough/) | Two self-contained HTML pages: annotated runs, and the same runs from the user's point of view |
| [`scripts/`](scripts/) | Capture, rehearsal, and demo-tenant setup |

## The offline path is the demo

Every scenario runs with mock providers and mock tools — no tenant, no
credentials, no network. That is deliberate: it is the rehearsal path and the
demo-day fallback when a tenant misbehaves in front of an audience.

```powershell
triage-demo list
triage-demo run scenario1-transient
```

The scenarios themselves live in [`../scenarios/`](../scenarios/) and the seeded
data in [`../mock/`](../mock/), because they are **also the test suite**. They
are not demo-only material and are not stored here.

## Regenerating the walkthrough

The pages embed their stylesheet and every screenshot, because SharePoint and
Teams render an `.html` through a preview host that drops external assets
without reporting it — a page with linked files arrives unstyled with empty
figures.

```powershell
.\.venv\Scripts\python.exe demo\scripts\capture_walkthrough.py
.\.venv\Scripts\python.exe demo\scripts\inline_assets.py   # re-embed the captures
.\.venv\Scripts\python.exe demo\scripts\preview_check.py   # render under the preview policy
```

Skipping the middle step leaves the shared copy showing the previous
screenshots. `tests/test_walkthrough_sandbox.py` fails if that happens.

## Demo-tenant setup

`scripts/` here also contains the one-off tooling that built the demo tenant:
personas, a Teams channel and webhook, a semantic model whose refresh genuinely
fails, and an alert injector. They read their tenant values from `.env` via
`_tenant.py` rather than hardcoding them, so they work in any tenant — but they
are not part of the accelerator and an adopter never needs them.
