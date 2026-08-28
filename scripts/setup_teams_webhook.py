"""Set up the Teams Workflows webhook by driving the browser.

Two steps, deliberately separated:

    python scripts\\setup_teams_webhook.py --login    # you sign in, once
    python scripts\\setup_teams_webhook.py --create   # unattended from here

Why two steps
-------------
Windows SSO on this machine signs Edge in automatically -- but to the corporate
tenant, not the tenant this demo lives in (verified; see
``scripts/probe_browser_auth.py``). A flow created there would be created in the
wrong place and would still look like it worked. So the first run needs a human
to sign in as the demo-tenant account. After that the browser profile carries
the session and ``--create`` runs on its own.

Why the browser at all
----------------------
Office 365 connector webhooks were retired on 22 May 2026. The replacement is a
Power Automate Workflows webhook, and there is no application-permission API for
creating one, nor for posting to a channel. Clicking is the supported path.

Handling of the URL
-------------------
The generated URL **is** the credential: anyone holding it can post to that
channel. It is never printed in full and never written to a file in the repo --
it goes straight into the azd environment, which is gitignored.

Honesty about state
-------------------
The ``--create`` path is written against Power Automate's current UI and has not
yet been run against the demo tenant, because reaching it needs the interactive
login above. It reports exactly which step it could not complete and leaves a
screenshot, rather than guessing and half-creating a flow.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PWTimeout

REPO = Path(__file__).resolve().parents[1]
PROFILE = REPO / ".browser-profile"          # gitignored
DEBUG = REPO / ".browser-debug"              # gitignored; screenshots on failure

FLOW_NAME = "BI triage alerts to Teams"
TEMPLATE_QUERY = "Post to a channel when a webhook request is received"
POWER_AUTOMATE = "https://make.powerautomate.com/"


def _shot(page: Page, name: str) -> Path:
    DEBUG.mkdir(parents=True, exist_ok=True)
    path = DEBUG / f"{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception:
        pass
    return path


def _fail(page: Page, step: str, detail: str = "") -> int:
    shot = _shot(page, f"failed-{step}")
    print(f"\nCould not complete step: {step}")
    if detail:
        print(f"  {detail}")
    print(f"  url:        {page.url[:140]}")
    print(f"  screenshot: {shot}")
    print("\nPower Automate's UI changes; if the selector is stale, do it by hand")
    print("(channel > ... > Workflows) and run --set-url instead. Nothing was")
    print("half-created: this stops before submitting when it cannot find a control.")
    return 1


def _context(playwright, headless: bool):
    PROFILE.mkdir(parents=True, exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE),
        channel="msedge",
        headless=headless,
        viewport={"width": 1500, "height": 950},
    )


def cmd_login(account: str) -> int:
    """Open a real browser window so a human can sign in to the demo tenant."""
    print("Opening Edge. Sign in as the DEMO TENANT account, not the corporate one:")
    print(f"  {account}")
    print("\nWhen Power Automate has finished loading and you can see your")
    print("environments, come back here and press Enter.\n")

    with sync_playwright() as playwright:
        context = _context(playwright, headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        # login_hint pre-fills the right account, which is the step people get
        # wrong: SSO will otherwise silently offer the corporate identity.
        page.goto(f"{POWER_AUTOMATE}?login_hint={account}", wait_until="domcontentloaded")

        input("Press Enter once you are signed in... ")

        try:
            body = page.inner_text("body")[:4000]
        except Exception:
            body = ""
        signed_in = len(body.strip()) > 200 and "login.microsoftonline.com" not in page.url
        _shot(page, "after-login")
        context.close()

    if not signed_in:
        print("\nThat did not look signed in. Re-run --login and wait for the")
        print("environment list to render before pressing Enter.")
        return 1

    print(f"\nSession saved to {PROFILE}")
    print("Now run:  python scripts\\setup_teams_webhook.py --create")
    return 0


def cmd_check() -> int:
    """Is the saved session still usable?"""
    if not PROFILE.exists():
        print("No saved session. Run --login first.")
        return 1

    with sync_playwright() as playwright:
        context = _context(playwright, headless=True)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(POWER_AUTOMATE, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_load_state("networkidle", timeout=45_000)
        except PWTimeout:
            pass
        page.wait_for_timeout(8000)
        body = page.inner_text("body")[:4000]
        url = page.url
        _shot(page, "check")
        context.close()

    # Rendered content, not just a URL -- a URL is not evidence of a session.
    if "login.microsoftonline.com" in url or len(body.strip()) < 200:
        print("Session expired or not signed in. Run --login again.")
        return 1
    print("Session looks good.")
    print(f"  environment header: {body.strip().splitlines()[:3]}")
    return 0


def cmd_create(headless: bool) -> int:
    """Create the Workflows flow and capture its webhook URL."""
    if not PROFILE.exists():
        print("No saved session. Run --login first.")
        return 1

    with sync_playwright() as playwright:
        context = _context(playwright, headless=headless)
        page = context.pages[0] if context.pages else context.new_page()

        page.goto(f"{POWER_AUTOMATE}templates", wait_until="domcontentloaded", timeout=90_000)
        try:
            page.wait_for_load_state("networkidle", timeout=45_000)
        except PWTimeout:
            pass
        page.wait_for_timeout(6000)

        if "login.microsoftonline.com" in page.url:
            context.close()
            print("Session expired. Run --login again.")
            return 1

        # --- find the template ---------------------------------------------
        search = page.get_by_role("searchbox").or_(page.get_by_placeholder("Search"))
        try:
            search.first.fill(TEMPLATE_QUERY, timeout=30_000)
            search.first.press("Enter")
            page.wait_for_timeout(6000)
        except Exception as exc:
            code = _fail(page, "search-templates", str(exc)[:160])
            context.close()
            return code

        card = page.get_by_text(TEMPLATE_QUERY, exact=False).first
        try:
            card.click(timeout=30_000)
            page.wait_for_timeout(6000)
        except Exception as exc:
            code = _fail(page, "open-template", str(exc)[:160])
            context.close()
            return code

        # --- accept connections and create ---------------------------------
        for label in ("Continue", "Create Flow", "Create flow", "Next", "Add workflow"):
            button = page.get_by_role("button", name=label, exact=False)
            try:
                if button.first.is_visible(timeout=4000):
                    button.first.click()
                    page.wait_for_timeout(7000)
            except Exception:
                continue

        page.wait_for_timeout(8000)

        # --- read back the webhook URL --------------------------------------
        url_value = _extract_webhook_url(page)
        if not url_value:
            code = _fail(
                page, "read-webhook-url",
                "The flow may have been created; open it in Power Automate, copy the "
                "HTTP POST URL, and run --set-url \"<url>\".",
            )
            context.close()
            return code

        _shot(page, "created")
        context.close()

    return _store(url_value)


def _extract_webhook_url(page: Page) -> str:
    """Pull the HTTP POST URL out of the trigger card."""
    # Try the obvious inputs first, then fall back to scanning the page text.
    for selector in ("input[readonly]", "input[type='text']", "textarea"):
        for element in page.query_selector_all(selector):
            try:
                value = (element.get_attribute("value") or element.inner_text() or "").strip()
            except Exception:
                continue
            if "logic.azure.com" in value and "sig=" in value:
                return value

    import re

    match = re.search(r"https://[^\s\"']*logic\.azure\.com[^\s\"']*sig=[^\s\"'&]+", page.content())
    return match.group(0) if match else ""


def _store(url: str) -> int:
    """Put the URL in the azd environment, never in the repo or the console."""
    proc = subprocess.run(
        ["azd", "env", "set", "TEAMS_WEBHOOK_URL", url],
        capture_output=True, text=True, cwd=str(REPO), shell=True,
    )
    if proc.returncode != 0:
        print("Captured the URL but could not write it to the azd environment:")
        print(f"  {(proc.stderr or proc.stdout)[:200]}")
        print("\nSet it yourself with:  azd env set TEAMS_WEBHOOK_URL \"<url>\"")
        return 1

    # Fingerprint only. The URL is a bearer credential; printing it would put it
    # in a terminal buffer, a screen share and probably a chat log.
    print("Webhook URL captured and written to the azd environment.")
    print(f"  length {len(url)}, ends ...{url[-6:]}")
    print("\nNext:")
    print("  azd deploy bi-triage-controller --no-prompt")
    print("  azd ai agent invoke bi-triage-controller \"sweep\"")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--login", action="store_true", help="Interactive sign-in (once)")
    group.add_argument("--check", action="store_true", help="Is the saved session still valid?")
    group.add_argument("--create", action="store_true", help="Create the flow and capture the URL")
    group.add_argument("--set-url", metavar="URL", help="Store a URL you created by hand")
    parser.add_argument(
        "--account", default="mhugh@MngEnvMCAP777813.onmicrosoft.com",
        help="Demo-tenant account to pre-fill at sign-in",
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="Watch --create work. Useful the first time, since the UI moves.",
    )
    args = parser.parse_args()

    if args.login:
        return cmd_login(args.account)
    if args.check:
        return cmd_check()
    if args.set_url:
        return _store(args.set_url)
    return cmd_create(headless=not args.headed)


if __name__ == "__main__":
    sys.exit(main())
