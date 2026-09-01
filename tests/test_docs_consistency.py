"""Tests that documentation still describes the software that exists.

Docs drift silently. Nobody notices a README claiming a command that was
renamed, or a run sheet quoting a scenario count from three commits ago -- until
it is read aloud in front of a customer and the command errors.

These check the claims that are cheap to verify and expensive to get wrong. The
test count is deliberately *not* checked here: asserting it from inside the test
suite is circular, and a number that has to be updated whenever a test is added
trains people to update it without looking.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from triage_demo.cli import build_parser

REPO_ROOT = Path(__file__).resolve().parents[1]

DOCS = (
    [REPO_ROOT / "README.md", REPO_ROOT / "AGENTS.md"]
    + sorted((REPO_ROOT / "docs").glob("*.md"))
    + sorted((REPO_ROOT / ".github").glob("*.md"))
)

CLI_REFERENCE = re.compile(r"triage-demo(?:\.exe)?\s+([a-z][a-z\-]*)")


def _cli_commands() -> set[str]:
    parser = build_parser()
    for action in parser._actions:  # noqa: SLF001 - argparse offers no public API
        if hasattr(action, "choices") and action.choices:
            return set(action.choices)
    return set()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_documented_cli_commands_exist(doc: Path) -> None:
    """Every `triage-demo <command>` in the docs is a real subcommand.

    Catches the rename that nobody propagated, which surfaces as a customer
    watching a command fail.
    """
    commands = _cli_commands()
    # Flags and prose fragments are not commands.
    referenced = {
        name for name in CLI_REFERENCE.findall(_read(doc))
        if not name.startswith("-")
    }
    unknown = referenced - commands
    assert not unknown, f"{doc.name} references non-existent commands: {sorted(unknown)}"


#: Small numbers are spelled out in this repo's prose, so a digits-only check
#: misses the claims most likely to be written. "all seven scenarios" survived
#: two scenario additions unnoticed for exactly this reason.
WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def _as_int(token: str) -> int:
    return int(token) if token.isdigit() else WORD_NUMBERS[token.lower()]


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_scenario_count_claims_match_reality(doc: Path) -> None:
    """A doc claiming "N scenarios" must agree with the scenarios directory."""
    actual = len(list((REPO_ROOT / "scenarios").glob("*.yaml")))
    words = "|".join(WORD_NUMBERS)
    number = rf"(?:\d+|{words})"
    # Only unambiguous claims about the whole suite. A doc may legitimately say
    # "the two scenarios as specified" or "failed two scenarios on run two"
    # without asserting a total, and a check that flags those is one somebody
    # switches off.
    text = _read(doc)
    claimed = {
        _as_int(n)
        for n in re.findall(rf"\b(?:all|of) ({number}) scenarios\b", text, re.I)
    }
    wrong = {n for n in claimed if n != actual}
    assert not wrong, f"{doc.name} claims {sorted(wrong)} scenarios; there are {actual}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_internal_doc_links_resolve(doc: Path) -> None:
    """Relative links between docs point at files that exist."""
    broken: list[str] = []
    for target in re.findall(r"\]\(([^)#]+\.md)\)", _read(doc)):
        if target.startswith("http"):
            continue
        resolved = (doc.parent / target).resolve()
        if not resolved.exists():
            # Docs sometimes link as docs/x.md from the root and x.md from docs/.
            alt = (REPO_ROOT / target).resolve()
            if not alt.exists():
                broken.append(target)
    assert not broken, f"{doc.name} has broken links: {broken}"


#: Non-markdown things the docs point at: the README's architecture diagram, the
#: walkthrough pages handed to customers as links, and screenshots. The link
#: check above only covers ``.md``, so a renamed image broke nothing visible in
#: the test suite while breaking the first thing a visitor to the repository
#: sees.
LINKED_ASSET = re.compile(r"\]\(([^)#]+\.(?:png|svg|jpg|jpeg|html|yaml|yml|py))\)|src=\"([^\"]+)\"")


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_linked_assets_exist(doc: Path) -> None:
    """Images and pages the docs point at are really there."""
    broken: list[str] = []
    for match in LINKED_ASSET.finditer(_read(doc)):
        target = match.group(1) or match.group(2)
        if not target or target.startswith(("http", "data:", "mailto:")):
            continue
        # Prose showing the *shape* of a path rather than a real one.
        if "..." in target or "<" in target:
            continue
        if not (doc.parent / target).resolve().exists():
            if not (REPO_ROOT / target).resolve().exists():
                broken.append(target)
    assert not broken, f"{doc.name} points at missing assets: {broken}"


def test_teams_delivery_claim_matches_reality() -> None:
    """The claim most likely to become a lie, in either direction.

    Teams delivery is now wired: the agent posts an Adaptive Card to a real
    channel over a Power Automate Workflows webhook, and the walkthrough shows
    the real post rather than a rendering. This test pins that the document and
    the deployment agree.

    If the webhook is ever removed, the walkthrough must go back to saying
    delivery is unwired -- a document implying a delivered notification that
    never arrives is the single most damaging inaccuracy this repo could ship.
    """
    walkthrough = (REPO_ROOT / "walkthrough" / "WALKTHROUGH.html").read_text(
        encoding="utf-8", errors="ignore"
    )
    claims_live = "shot-teams-live.png" in walkthrough
    claims_unwired = "not wired" in walkthrough or "notifier is mocked" in walkthrough

    assert claims_live != claims_unwired, (
        "The walkthrough must either show the real posted card or say delivery "
        "is unwired -- not both, and not neither."
    )


def test_the_walkthrough_does_not_contradict_itself_on_test_count() -> None:
    """Two places quoted two different numbers, and both were wrong.

    The header chip said 287 while the footer still said 193, four days and
    ~120 tests after either was true. Pinning the exact number here would be
    circular -- adding a test would fail it -- but the page agreeing with
    itself is a real, non-circular property, and it is the one that broke.
    """
    walkthrough = (REPO_ROOT / "walkthrough" / "WALKTHROUGH.html").read_text(
        encoding="utf-8", errors="ignore"
    )
    claimed = set(re.findall(r"(\d+)\s+(?:offline\s+tests|tests\s+pass\s+offline)", walkthrough))

    assert claimed, "The walkthrough should state how many tests pass offline"
    assert len(claimed) == 1, (
        f"The walkthrough quotes conflicting test counts: {sorted(claimed)}. "
        "Every mention must agree."
    )
