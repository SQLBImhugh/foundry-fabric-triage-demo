"""Capture the Teams channel showing cards the agent actually posted.

Replaces the rendered-from-payload placeholder in the walkthroughs with the
real thing: an Adaptive Card delivered to a real channel by the deployed agent.

Uses the browser profile established by setup_teams_webhook.py --login, which
is signed in to the demo tenant. Teams is a heavy SPA, so this waits for the
message list to actually contain content rather than screenshotting a skeleton.
"""

from __future__ import annotations

import sys
from pathlib import Path

from _tenant import required
from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
PROFILE = REPO / ".browser-profile"
OUT = REPO / "demo" / "walkthrough" / "shots"
DEBUG = REPO / ".browser-debug"

TENANT = required("GRAPH_TENANT_ID", "the tenant whose Teams channel is captured")
TEAMS_URL = f"https://teams.microsoft.com/v2/?tenantId={TENANT}"


def _cloned_profile_dir() -> str:
    if not PROFILE.exists():
        return ""
    for child in PROFILE.iterdir():
        if child.is_dir() and child.name.startswith("Profile "):
            return child.name
    return ""


def main() -> int:
    if not PROFILE.exists():
        print("No browser session. Run: setup_teams_webhook.py --login")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    DEBUG.mkdir(parents=True, exist_ok=True)

    args = ["--start-maximized"]
    named = _cloned_profile_dir()
    if named:
        args.append(f"--profile-directory={named}")

    with sync_playwright() as playwright:
        # Headed: Entra does not complete a session in headless Chromium, which
        # was established the hard way earlier in this project.
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=False,
            viewport={"width": 1500, "height": 1000}, args=args,
        )
        page = context.pages[0] if context.pages else context.new_page()

        print("  opening Teams...")
        page.goto(TEAMS_URL, wait_until="domcontentloaded", timeout=120_000)

        # Teams takes a long time to render, and longer to load a channel.
        for attempt in range(24):
            page.wait_for_timeout(5000)
            try:
                text = page.inner_text("body")
            except Exception:
                continue
            if "Data Platform Operations" in text or "BI Alerts" in text:
                print(f"  channel visible after ~{(attempt + 1) * 5}s")
                break
            if attempt % 4 == 3:
                print(f"    still loading ({len(text.strip())} chars)")
        else:
            page.screenshot(path=str(DEBUG / "teams-capture-timeout.png"))
            print("  Teams did not reach the channel in time.")
            print(f"  screenshot: {DEBUG / 'teams-capture-timeout.png'}")
            context.close()
            return 1

        # Click into the alerts channel if it is not already open.
        for label in ("BI Alerts", "Data Platform Operations"):
            try:
                target = page.get_by_text(label, exact=False).first
                if target.is_visible(timeout=4000):
                    target.click()
                    page.wait_for_timeout(6000)
            except Exception:
                continue

        page.wait_for_timeout(8000)

        # Park the pointer somewhere harmless first. Clicking the channel in the
        # tree leaves a hover tooltip sitting over the message, which covered
        # the "Action taken" row in the first capture.
        try:
            page.mouse.move(1400, 950)
            page.keyboard.press("Escape")
            page.wait_for_timeout(2500)
        except Exception:
            pass

        page.screenshot(path=str(OUT / "shot-teams-live.png"))
        page.screenshot(path=str(DEBUG / "teams-live-full.png"), full_page=True)
        print(f"  captured: {OUT / 'shot-teams-live.png'}")

        context.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
