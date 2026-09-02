"""Assemble the post-demo handoff bundle.

Generates rather than curates, so the bundle cannot drift from the code. Every
artefact is produced by running the thing that produces it, which also means a
broken bundle is a signal that something is broken.

Writes to ``handoff/`` (gitignored). Deliberately refuses to include anything
holding a credential -- see ``docs/handoff.md``.
"""

from __future__ import annotations

import argparse
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
    (client[_-]?secret | api[_-]?key | password | webhook[_-]?url | callback[_-]?url
     | connection[_-]?string)
    # Horizontal whitespace only. \s* spans newlines, which made an *empty*
    # "GRAPH_CLIENT_SECRET=" swallow the following line and report the next
    # setting as its value -- flagging a blank template as a leak.
    [ \t]* [=:] [ \t]*
    ["']?
    (?P<value> [^\s"'<>${},]{12,} )      # 12+ chars, not a placeholder
    """,
)
# A Workflows webhook URL is itself the credential. Two formats in the wild:
# the older logic.azure.com one, and the newer Power Platform environment host.
# The new format carries the same 'sig' bearer parameter and would have sailed
# past a check that only knew about the old one. The approval callback is the
# same shape again -- anyone holding that link can answer an approval.
WEBHOOK_URL = re.compile(
    r"https://[^\s\"']*(?:logic\.azure\.com|powerplatform\.com)[^\s\"']*sig=[^\s\"'&]+",
    re.I,
)
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


#: A value that is obviously code rather than a credential. ``client_secret=
#: self._client_secret`` is a parameter being passed and ``password=
#: load_agent_identity(`` is a function call, not a secret being written down.
#: A scanner that cannot tell the difference produces pages of noise, which is
#: how it ends up ignored or switched off.
CODE_LIKE = re.compile(r"^[A-Za-z_][\w.]*(\[[^\]]*\])?[(),]?$")

#: Hosts reserved by RFC 2606 and RFC 6761 for documentation and testing. A URL
#: pointing at one cannot be a live credential, because the domain cannot exist.
RESERVED_HOST = re.compile(
    r"^https?://(localhost|127\.0\.0\.1|\[::1\]|[\w.-]*example\.(com|net|org|invalid|test))",
    re.I,
)


def scan_tree(root: Path) -> list[str]:
    """Look for credentials in the files that would actually be published.

    Scans **git-tracked files only**, because that is the question being asked:
    would this leak if the repository were pushed? A local ``.env`` or
    ``.azure/`` holds real secrets by design and is gitignored; flagging them
    every run trains people to ignore the output.

    Shared with the bundle builder so CI and the handoff step cannot drift into
    checking different things. Skips the files whose whole purpose is to contain
    realistic fake secrets -- the scanner's own patterns, its tests, and the
    redaction corpus. A check that flags its own test fixtures is one somebody
    turns off.
    """
    skip_files = {"build_handoff.py", "test_handoff_scanner.py", "test_redaction.py"}

    try:
        tracked = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=str(root), capture_output=True, text=True, check=True,
        ).stdout.split("\0")
    except (subprocess.CalledProcessError, FileNotFoundError):
        # No git available: fall back to walking the tree. Better to over-report
        # than to report "clean" because the enumeration failed.
        tracked = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()]

    leaks: list[str] = []
    for rel in tracked:
        if not rel or Path(rel).name in skip_files or Path(rel).suffix in {".png", ".svg"}:
            continue
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for match in SECRET_ASSIGNMENT.finditer(text):
            value = match.group("value")
            if (
                PLACEHOLDER.match(value)
                or CODE_LIKE.match(value)
                or RESERVED_HOST.match(value)
            ):
                continue
            leaks.append(f"{rel}: {match.group(1)} has a value")
        for pattern, label in ((WEBHOOK_URL, "webhook URL"), (BEARER_TOKEN, "bearer token")):
            if pattern.search(text):
                leaks.append(f"{rel}: {label}")

    return list(dict.fromkeys(leaks))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Scan the working tree for credentials and exit. Used by CI.",
    )
    args = parser.parse_args()

    if args.scan_only:
        leaks = scan_tree(REPO)
        if leaks:
            print("Possible credentials found:")
            for leak in leaks:
                print(f"  {leak}")
            return 1
        print("credential scan clean.")
        return 0

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

    # Explicit paths, and a hard failure if one is missing. These used to be
    # looked up under docs/ with an `if exists` guard, so when three of them
    # moved to demo/ the bundle would have quietly shipped without them --
    # smaller, still exit 0, and nobody the wiser until a customer opened it.
    for doc in (
        REPO / "docs" / "hosted-architecture.md",
        REPO / "docs" / "architecture.md",
        REPO / "docs" / "provisioning.md",
        REPO / "demo" / "model-selection.md",
        REPO / "demo" / "handoff.md",
        REPO / "demo" / "faq.md",
    ):
        if not doc.exists():
            raise SystemExit(
                f"Handoff bundle is missing {doc.relative_to(REPO).as_posix()}. "
                "It was probably moved; update this list rather than shipping "
                "an incomplete bundle."
            )
        shutil.copy2(doc, OUT / doc.name)
        print(f"  {doc.name}")

    shutil.copy2(REPO / ".env.example", OUT / ".env.example")
    print("  .env.example")

    walkthrough = OUT / "walkthrough"
    shutil.copytree(REPO / "demo" / "walkthrough", walkthrough)
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
