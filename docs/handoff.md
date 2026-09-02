# Handoff

What to share after the demo, and what each thing is for. The intent is that
someone who was not in the room can pick this up and understand both what it
does and why it is built the way it is.

## Generate the bundle

```powershell
python scripts\build_handoff.py
```

Writes to `handoff/`, which is gitignored — it is generated, not source.

## What goes in it

| Item | Source | Why they want it |
|---|---|---|
| Agent definitions | `register_foundry_agents.py --print-definitions` | The exact registered shape, including tool schemas and model |
| Prompts | `src/triage_demo/agents/prompts/` | What the agents were actually told |
| Tool schemas | `triage-demo tools` | The complete action surface, and therefore the blast radius |
| Adaptive Card payload | `triage-demo teams-preview --json` | The exact payload posted to Teams, without needing the webhook |
| Config template | `.env.example` | Every knob, with none of the values |
| Architecture | `docs/hosted-architecture.md` | What runs where and which identity does it |
| Model comparison | `docs/model-selection.md` | Why this model, and the fallback |
| Walkthrough | `walkthrough/WALKTHROUGH.html` | The annotated version for people who were not present |
| Persona scenario | `walkthrough/PERSONAS.html` | The same runs from the user's side |

Both walkthrough pages are single self-contained files — the stylesheet and
every screenshot are embedded, so nothing else from `walkthrough/` goes with
them.

Verified in the demo tenant: uploaded to a Teams channel's Files tab, both the
file itself and a **Share** link render in the browser with all figures intact.
Pasting the raw file path downloads the file instead, because SharePoint serves
`.html` that way unless custom scripting is enabled on the site. Send the link
the Share button produces, not the one in the address bar.

## What must not go in it

- Any filled-in `.env`, the Graph client secret, or a Teams webhook URL. The
  webhook URL is a bearer credential: anyone holding it can post to the channel.
- Tenant-specific ids where a placeholder will do.
- Anything from the internal knowledge base. The playbooks in
  `src/triage_demo/knowledge/` are sourced from public Microsoft Learn
  documentation only, and two tests enforce that. Keep it that way.

## The honest framing

Two things are worth saying out loud when handing this over, because they will
otherwise be discovered later and look like omissions.

**This is a demonstration asset, not a product.** It is deliberately small
enough to read. The parts that would need real work before production are listed
in `docs/plan.md` under "Production-grade equivalent", and that list is not a
formality — policy, dedup and incident storage all need to grow considerably.

**Teams delivery is wired.** The agent posts a real Adaptive Card to
**Data Platform Operations → BI Alerts** over a Power Automate Workflows
webhook. Do **not** put `TEAMS_WEBHOOK_URL` in the bundle — the URL is a bearer
credential and anyone holding it can post to that channel. The recipient
creates their own.
