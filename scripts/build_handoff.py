"""Assemble the post-demo handoff bundle.

Generates rather than curates, so the bundle cannot drift from the code. Every
artefact is produced by running the thing that produces it, which also means a
broken bundle is a signal that something is broken.

Writes to ``handoff/`` (gitignored). Deliberately refuses to include anything
holding a credential -- see ``docs/handoff.md``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXE = REPO / ".venv" / "Scripts" / "triage-demo.exe"
PY = REPO / ".venv" / "Scripts" / "python.exe"
OUT = REPO / "handoff"
ANSI = re.compile(r"\x1b\[[0-9;]*m")

# Offline everywhere: the bundle must be reproducible without a tenant, and a
# generator that needs credentials is a generator that will fail for whoever
# receives it.
ENV = dict(os.environ) | {
    "TRIAGE_PROVIDER_MODE": "mock",
    "TRIAGE_TOOL_MODE": "mock",
    "INCIDENT_TABLE_ENDPOINT": "",
    "PYTHONIOENCODING": "utf-8",
}

# A credential is a secret-ish key with a real value after it, not the mention
# of one. The docs legitimately discuss client secrets and say
# "passwordCredentials=0", and a scanner that cannot tell those apart gets
# switched off by the first person in a hurry -- which is exactly when it is
# needed.
SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)
    # No leading \b: real names are prefixed, e.g. GRAPH_CLIENT_SECRET. A word
    # boundary before "client_secret" never matches there, because the
    # preceding underscore is itself a word character. That bug made this
    # scanner report "clean" while being incapable of detecting the single most
    # likely leak, which is worse than having no scanner at all.
    (client[_-]?secret | api[_-]?key | password | webhook[_-]?url | connection[_-]?string)
    # Horizontal whitespace only. \s* spans newlines, which made an *empty*
    # "GRAPH_CLIENT_SECRET=" swallow the following line and report the next
    # setting as its value -- flagging a blank template as a leak.
    [ \t]* [=:] [ \t]*
    ["']?
    (?P<value> [^\s"'<>${},]{12,} )      # 12+ chars, not a placeholder
    """,
)
# A Workflows webhook URL is itself the credential.
WEBHOOK_URL = re.compile(r"https://[^\s\"']*logic\.azure\.com[^\s\"']*sig=[^\s\"'&]+", re.I)
BEARER_TOKEN = re.compile(r"\bBearer\s+ey[A-Za-z0-9._-]{20,}")
# Placeholders that look like values but are not.
PLACEHOLDER = re.compile(
    r"^(your|placeholder|example|changeme|xxx+|\.\.\.|<.*>|\$\{.*\}|none|null|n/?a)$", re.I
)


def _run(args: list[str]) -> str:
    proc = subprocess.run(
        args, capture_output=True, text=True, cwd=str(REPO), env=ENV,
        encoding="utf-8", errors="replace",
    )
    return ANSI.sub("", (proc.stdout or "") + (proc.stderr or ""))


def _write(name: str, text: str) -> None:
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"  {name}")


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    print(f"building handoff bundle in {OUT}\n")

    _write("agent-definitions.txt",
           _run([str(PY), "scripts/register_foundry_agents.py", "--print-definitions"]))
    _write("tool-schemas.txt", _run([str(EXE), "tools"]))
    _write("teams-card.json", _run([str(EXE), "teams-preview", "--json"]))

    prompts = OUT / "prompts"
    prompts.mkdir()
    for prompt in (REPO / "src" / "triage_demo" / "agents" / "prompts").glob("*.md"):
        shutil.copy2(prompt, prompts / prompt.name)
        print(f"  prompts/{prompt.name}")

    for doc in ("hosted-architecture.md", "model-selection.md", "handoff.md",
                "architecture.md", "faq.md", "provisioning.md"):
        source = REPO / "docs" / doc
        if source.exists():
            shutil.copy2(source, OUT / doc)
            print(f"  {doc}")

    shutil.copy2(REPO / ".env.example", OUT / ".env.example")
    print("  .env.example")

    walkthrough = OUT / "walkthrough"
    shutil.copytree(REPO / "walkthrough", walkthrough)
    print(f"  walkthrough/ ({len(list(walkthrough.rglob('*')))} files)")

    # Refuse to ship a credential. This is the last line of defence, not the
    # first -- but the first ones can be edited by someone in a hurry.
    leaks: list[str] = []
    for path in OUT.rglob("*"):
        if not path.is_file() or path.suffix in {".png", ".svg"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        rel = path.relative_to(OUT)
        for match in SECRET_ASSIGNMENT.finditer(text):
            value = match.group("value")
            if PLACEHOLDER.match(value):
                continue
            leaks.append(f"{rel}: {match.group(1)} has a value")
        for pattern, label in ((WEBHOOK_URL, "webhook URL"), (BEARER_TOKEN, "bearer token")):
            if pattern.search(text):
                leaks.append(f"{rel}: {label}")

    print()
    if leaks:
        print("REFUSING to ship the bundle -- possible credentials found:")
        for leak in dict.fromkeys(leaks):
            print(f"  {leak}")
        return 1

    manifest = {
        "files": sorted(str(p.relative_to(OUT)) for p in OUT.rglob("*") if p.is_file()),
        "credential_scan": "clean",
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"credential scan clean. {len(manifest['files'])} file(s) in the bundle.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
