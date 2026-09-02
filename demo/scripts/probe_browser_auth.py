"""Feasibility probe: can browser automation reach Microsoft 365 signed in?

The interesting question is not whether Playwright can click buttons -- it can.
It is whether a fresh browser profile on this machine arrives at Power Automate
already authenticated via Windows SSO, or stops at a sign-in prompt. The first
means click-through automation is possible unattended; the second means it
needs a human at the keyboard once, and pretending otherwise wastes time.

Result when this was last run (2026-08-28)
------------------------------------------
Windows SSO **works**: a fresh Edge profile reached Power Automate fully
rendered, no prompt, no credentials. But it signed in to the **corporate**
tenant -- the environment header read "Default Environment - Approved Use Only"
and Teams redirected with ``loginHint=<the signed-in work account>``.

The demo lives in a different tenant. A webhook created by automation here
would be created in the wrong place, and would look like it had worked. So
click-through creation for this demo needs one interactive sign-in to the demo
tenant first; after that the persistent profile carries the session and
automation can proceed unattended.

A note on method: the first version of this probe reported "SIGNED IN" from the
URL alone, while the page was still a loading spinner. A URL is not evidence.
It now requires rendered content before believing anything.

Reports where it landed and what it saw. Changes nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PROFILE = Path.home() / ".cache" / "bitriage-probe-profile"
SHOTS = Path(__file__).resolve().parents[2] / "demo" / "walkthrough" / "shots" / "_probe"

TARGETS = {
    "power-automate": "https://make.powerautomate.com/",
    "teams": "https://teams.microsoft.com/",
}


def classify(url: str, title: str, body: str) -> str:
    lowered = f"{url} {title} {body}".lower()
    if "login.microsoftonline.com" in url or "sign in to your account" in lowered:
        return "SIGN-IN REQUIRED"
    if "pick an account" in lowered or "choose an account" in lowered:
        return "ACCOUNT PICKER (SSO present, needs one click)"
    # A URL is not evidence of being signed in. The first version of this probe
    # reported "SIGNED IN" from the URL alone while the page was still a
    # loading spinner -- the app had not rendered and auth had not resolved.
    # Require actual rendered content before believing it.
    if len(body.strip()) < 200:
        return "INCONCLUSIVE (page had not rendered)"
    if "make.powerautomate.com" in url or "teams." in url:
        return "SIGNED IN (content rendered)"
    return "UNKNOWN"


def main() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    headless = "--headed" not in sys.argv

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            channel="msedge",
            headless=headless,
            args=["--auth-server-allowlist=*.microsoft.com,*.microsoftonline.com"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        for name, url in TARGETS.items():
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                # These are heavy SPAs. Give them time to resolve auth and
                # render before judging what we are looking at.
                try:
                    page.wait_for_load_state("networkidle", timeout=45_000)
                except Exception:
                    pass
                page.wait_for_timeout(10_000)
                body = page.inner_text("body")[:6000]
                verdict = classify(page.url, page.title(), body)
                page.screenshot(path=str(SHOTS / f"probe-{name}.png"), full_page=False)
                print(f"  {name:<16} {verdict}")
                print(f"    landed: {page.url[:110]}")
                print(f"    body:   {len(body.strip())} chars  |  {body.strip()[:90]!r}")
            except Exception as exc:
                print(f"  {name:<16} ERROR {type(exc).__name__}: {str(exc)[:120]}")

        context.close()

    print(f"\nscreenshots: {SHOTS}")
    print(f"profile:     {PROFILE}  (delete to reset the experiment)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
