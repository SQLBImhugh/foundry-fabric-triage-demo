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


def _context(playwright, headless: bool, use_edge: bool = False):
    """Launch a browser with our own profile.

    Two details, both learned the hard way:

    **Plain Chromium, not Edge.** Edge performs Windows single sign-on: it
    silently signs in as whatever account Windows is joined to, marks it
    "Connected to Windows", and never offers the picker. Here that is the
    corporate account, not the tenant the demo lives in -- so the automation
    authenticated to the wrong place twice while looking perfectly correct.
    Bundled Chromium has no Windows account integration.

    **Keep the profile's directory name.** Chromium maps profile directories to
    accounts through ``Local State``. Flattening ``Profile 4`` into ``Default``
    breaks that association and the cloned session is ignored; the directory
    name has to survive the copy and be named on the command line.
    """
    PROFILE.mkdir(parents=True, exist_ok=True)
    args = ["--start-maximized"]

    # If the clone kept a named profile directory, tell Chromium to use it.
    named = _cloned_profile_dir()
    if named:
        args.append(f"--profile-directory={named}")

    kwargs = {
        "user_data_dir": str(PROFILE),
        "headless": headless,
        "viewport": {"width": 1500, "height": 950},
        "args": args,
    }
    if use_edge:
        kwargs["channel"] = "msedge"
    return playwright.chromium.launch_persistent_context(**kwargs)


def _cloned_profile_dir() -> str:
    """Name of the cloned profile directory, if the clone kept one."""
    if not PROFILE.exists():
        return ""
    for child in PROFILE.iterdir():
        if child.is_dir() and child.name.startswith("Profile "):
            return child.name
    return ""


DEMO_TENANT_DOMAIN = "mngenvmcap777813"
DEMO_TENANT_ID = "edf144d9-f468-4b8e-8443-f51dadfbc4f9"


EDGE_USER_DATA = Path.home() / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data"

# The parts of an Edge profile that actually carry a signed-in session.
# Copying the whole profile means ~750MB and a lot of irrelevant state; these
# are the pieces M365 web apps keep auth in. IndexedDB and Local Storage matter
# as much as cookies -- MSAL keeps tokens there, and a cookies-only copy lands
# you back at a sign-in prompt.
SESSION_PARTS = [
    "Preferences",
    "Network",
    "Local Storage",
    "Session Storage",
    "IndexedDB",
    "Login Data",
    "Web Data",
]


def find_edge_profile(display_name: str) -> tuple[str, str] | None:
    """Map a profile's display name to its directory and account."""
    import json as _json

    if not EDGE_USER_DATA.exists():
        return None
    for directory in sorted(EDGE_USER_DATA.iterdir()):
        if not directory.is_dir():
            continue
        if directory.name != "Default" and not directory.name.startswith("Profile "):
            continue
        prefs = directory / "Preferences"
        if not prefs.exists():
            continue
        try:
            data = _json.loads(prefs.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        name = str((data.get("profile") or {}).get("name", ""))
        accounts = data.get("account_info") or [{}]
        email = str(accounts[0].get("email", ""))
        if name.lower() == display_name.lower():
            return directory.name, email
    return None


def cmd_use_edge_profile(display_name: str) -> int:
    """Seed our profile from an existing Edge profile that is already signed in.

    Copies rather than using the live profile directly: Edge holds a lock on a
    running profile, and pointing automation at someone's real browser state is
    an unpleasant thing to do to them. This takes a snapshot of the session and
    leaves the original untouched.
    """
    import shutil as _shutil

    found = find_edge_profile(display_name)
    if found is None:
        print(f"No Edge profile named {display_name!r}.")
        return 1

    directory, email = found
    print(f"Found Edge profile {display_name!r} -> {directory}  ({email})")

    if DEMO_TENANT_DOMAIN not in email.lower():
        print(f"\nRefusing: {email} is not a {DEMO_TENANT_DOMAIN} account.")
        print("That is the check this tool exists for -- a flow created from the")
        print("wrong tenant looks correct and is useless.")
        return 1

    source = EDGE_USER_DATA / directory
    # Keep the directory name. Chromium maps profiles to accounts via
    # Local State; renaming this to "Default" silently discards the session.
    target = PROFILE / directory
    if PROFILE.exists():
        _shutil.rmtree(PROFILE, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)

    # Local State holds the key that decrypts the cookie store. Same Windows
    # user, so DPAPI unwraps it fine; without it the cookies are unreadable.
    try:
        _shutil.copy2(EDGE_USER_DATA / "Local State", PROFILE / "Local State")
    except Exception as exc:
        print(f"  could not copy Local State ({type(exc).__name__}) - cookies may not decrypt")

    copied, skipped = 0, 0
    for part in SESSION_PARTS:
        src = source / part
        if not src.exists():
            continue
        try:
            if src.is_dir():
                _shutil.copytree(src, target / part, dirs_exist_ok=True)
            else:
                _shutil.copy2(src, target / part)
            copied += 1
        except Exception:
            # Edge is running and holds locks on some of these. A partial copy
            # is usually still enough; report rather than pretend.
            skipped += 1

    size = sum(f.stat().st_size for f in PROFILE.rglob("*") if f.is_file())
    print(f"  copied {copied} part(s), {skipped} locked/skipped, {size // (1024 * 1024)} MB")
    print(f"  session snapshot at {PROFILE}")
    print("\nVerify it worked:  python scripts\\setup_teams_webhook.py --check")
    return 0


def _tenant_evidence(page: Page) -> tuple[bool, str]:
    """Is this session in the demo tenant, or the corporate one?

    Rendered content alone is not enough. Windows SSO overrides ``login_hint``
    and signs straight in to the corporate tenant, which renders perfectly and
    reports "signed in" -- and the first version of this tool accepted that,
    then offered to create a flow there. Landing in the wrong tenant while
    looking correct is the specific failure this whole two-step design exists
    to prevent, so it has to be checked rather than assumed.
    """
    try:
        # The URL matters as much as the body: Power Automate encodes the
        # tenant in the environment id (".../environments/Default-<tenantId>"),
        # and checking only page content missed a session that was already
        # correctly in the demo tenant.
        haystack = (
            page.url
            + page.content()[:400_000]
            + page.inner_text("body")[:20_000]
        ).lower()
    except Exception:
        return False, "could not read the page"

    if DEMO_TENANT_DOMAIN in haystack or DEMO_TENANT_ID in haystack:
        return True, "demo tenant"

    # Name the wrong tenant explicitly; "not the demo tenant" is less useful
    # than "you are in the corporate one".
    if "msdefault" in haystack or "approved use only" in haystack:
        return False, "corporate tenant (Default Environment - Approved Use Only)"
    return False, "unknown tenant - no demo-tenant marker found"


def _looks_signed_in(page: Page) -> bool:
    """Rendered content, not just a URL.

    An earlier probe reported success from the URL alone while the page was
    still a loading spinner. A URL is not evidence of a session.
    """
    if "login.microsoftonline.com" in page.url:
        return False
    try:
        return len(page.inner_text("body").strip()) > 400
    except Exception:
        return False


def _any_signed_in_page(context) -> tuple[bool, str, object]:
    """Look across every tab, not just the first.

    Sign-in frequently completes in a new tab, or the app re-opens itself in
    one. Polling only ``pages[0]`` watches a stale tab sitting on the login URL
    and reports "waiting" forever while the user is, in fact, signed in two tabs
    over -- which is exactly what happened.
    """
    for page in list(context.pages):
        try:
            if not _looks_signed_in(page):
                continue
            in_demo, which = _tenant_evidence(page)
            if in_demo:
                return True, which, page
        except Exception:
            continue

    # Nothing signed in: report the most informative tab for the progress line.
    for page in list(context.pages):
        try:
            return False, page.url[:58], page
        except Exception:
            continue
    return False, "no pages", None


def cmd_login(account: str, timeout_seconds: int) -> int:
    """Open a real browser window so a human can sign in to the demo tenant.

    Polls for completion rather than waiting on ``input()``. The original
    version blocked on a keypress, which meant it could only ever be run by a
    human sitting at a terminal -- useless when the rest of the workflow is
    automated, and the first thing that broke when it was.
    """
    import time

    print("Opening a plain Chromium window (not Edge -- Edge force-signs-in as")
    print("the Windows account, which is the corporate one).")
    print(f"\n  Sign in as: {account}")
    print("  If a picker appears offering a 'Connected to Windows' account, that")
    print("  is the corporate one. Choose 'Use another account' instead.")
    print("\n  A flow created from the corporate tenant looks correct and is useless,")
    print("  which is why this waits for demo-tenant evidence, not just a session.")
    print(f"\nWaiting up to {timeout_seconds // 60} minutes. Detecting automatically.\n")

    with sync_playwright() as playwright:
        context = _context(playwright, headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            # Tenant-scoped entry point: this forces the account picker rather
            # than silently completing SSO as whoever Windows is signed in as.
            page.goto(
                f"https://make.powerautomate.com/?tenantId={DEMO_TENANT_ID}"
                f"&login_hint={account}",
                wait_until="domcontentloaded", timeout=60_000,
            )
        except Exception as exc:
            print(f"  could not open Power Automate: {type(exc).__name__}")

        deadline = time.time() + timeout_seconds
        ok = False
        last = ""
        found_page = None
        while time.time() < deadline:
            time.sleep(5)
            signed, detail, candidate = _any_signed_in_page(context)
            state = f"signed in - {detail}" if signed else f"waiting ({detail})"
            if state != last:
                print(f"  {state}", flush=True)
                last = state
            if signed:
                time.sleep(8)
                signed, detail, candidate = _any_signed_in_page(context)
                if signed:
                    ok, found_page = True, candidate
                    break

        if ok and found_page is not None:
            _shot(found_page, "after-login")
            try:
                print(f"\n  page shows: {found_page.inner_text('body').strip().splitlines()[:4]}")
            except Exception:
                pass
        # Close cleanly. Killing the process leaves Chromium's profile
        # unflushed, so the session that was just established is lost -- which
        # is a miserable way to make someone sign in twice.
        context.close()

    if not ok:
        print("\nTimed out without reaching the demo tenant.")
        print("If it signed you in as the corporate account, sign out of that")
        print("account in the window and choose 'Use another account'.")
        return 1

    print(f"\nSession saved to {PROFILE}")
    print("Verify:  python scripts\\setup_teams_webhook.py --check")
    return 0


def cmd_check(headless: bool = True) -> int:
    """Is the saved session still usable?"""
    if not PROFILE.exists():
        print("No saved session. Run --login first.")
        return 1

    with sync_playwright() as playwright:
        context = _context(playwright, headless=headless)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(POWER_AUTOMATE, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_load_state("networkidle", timeout=45_000)
        except PWTimeout:
            pass
        page.wait_for_timeout(8000)
        body = page.inner_text("body")[:4000]
        url = page.url
        in_demo, which = _tenant_evidence(page)
        _shot(page, "check")
        context.close()

    # Rendered content, not just a URL -- a URL is not evidence of a session.
    if "login.microsoftonline.com" in url or len(body.strip()) < 200:
        print("Session expired or not signed in. Run --login again.")
        return 1

    header = body.strip().splitlines()[:4]
    print(f"  signed in, page shows: {header}")
    print(f"  tenant: {which}")

    if not in_demo:
        print("\nRefusing to continue: this session is not in the demo tenant.")
        print("Anything created here would land in the wrong place and still")
        print("look like it worked. Seed the session from the right Edge profile:")
        print("  python scripts\\setup_teams_webhook.py --use-edge-profile MorkNet")
        return 1

    print("\nSession is good and in the demo tenant.")
    return 0


def _wait_for_results(page: Page, timeout_seconds: int = 60) -> bool:
    """Wait until search results are real text rather than skeleton bars.

    Power Automate renders grey placeholder rows while it fetches. They are
    present in the DOM and clickable-looking, so an immediate click times out
    against an element that never had a label -- which reads as "template not
    found" when the template is fine and the page was simply not ready.
    """
    import time

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            text = page.inner_text("body")
            # Real results carry the words from the query back to us.
            if TEMPLATE_QUERY.split()[0].lower() in text.lower() and len(text) > 1200:
                return True
        except Exception:
            pass
        page.wait_for_timeout(3000)
    return False


def cmd_create(headless: bool) -> int:
    """Create the Workflows flow and capture its webhook URL.

    **This route does not work, and the failure is informative.** Driving the
    Power Automate template gallery was the obvious approach and it is a dead
    end: searching it for "Post to a channel when a webhook request is
    received" returns *"We couldn't find any templates that matched your
    search."* The template is surfaced by Teams, from the channel's Workflows
    menu, and is not in the general gallery at all.

    Automating the Teams client instead is possible but considerably more
    fragile -- a heavier SPA, an iframe-hosted flow designer, and a UI that
    changes more often than the two minutes it takes to click through by hand.

    So this stops and points at the manual path plus ``--set-url``. Left in
    place rather than deleted because "we tried the gallery, it does not carry
    this template" is worth knowing before someone tries it again.
    """
    print("The Power Automate template gallery does not carry this template.")
    print("Verified: searching it returns \"We couldn't find any templates that")
    print("matched your search.\" It is surfaced by Teams, not the gallery.\n")
    print("Do it from the channel instead -- about two minutes:")
    print("  1. Channel name > ... > Workflows")
    print("  2. 'Post to a channel when a webhook request is received'")
    print("  3. Next, pick the team and channel, Add workflow")
    print("  4. Copy the HTTP POST URL\n")
    print("Then store it without it touching the repo or this console:")
    print("  python scripts\\setup_teams_webhook.py --set-url \"<url>\"\n")
    print("--check confirms the browser session is still valid and in the demo")
    print("tenant, which is the part that was genuinely worth automating.")
    return 2


def _create_via_gallery_unused(headless: bool) -> int:
    """Retained for reference: the gallery walk that proved the template absent."""
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

        # Narrow to templates. The "All" tab mixes documentation, community
        # posts and connectors, and the first text match is often a blog.
        try:
            tab = page.get_by_role("tab", name="Templates").or_(
                page.get_by_text("Templates", exact=True))
            if tab.first.is_visible(timeout=8000):
                tab.first.click()
                page.wait_for_timeout(5000)
        except Exception:
            pass

        # Results render as skeleton placeholders first. Clicking during that
        # window times out against an element that never had text -- which is
        # exactly how this failed the first time. Wait for real content.
        if not _wait_for_results(page):
            code = _fail(page, "template-results-never-rendered",
                         "Search results stayed as skeleton placeholders.")
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
    # shell=False deliberately. Webhook URLs always contain '&', and on Windows
    # shell=True joins the argument list into one string, where '&' becomes a
    # command separator -- the URL is silently truncated and the rest is
    # executed as commands. That failed loudly here; it could just as easily
    # have stored half a URL.
    proc = subprocess.run(
        ["azd", "env", "set", "TEAMS_WEBHOOK_URL", url],
        capture_output=True, text=True, cwd=str(REPO), shell=False,
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
    group.add_argument(
        "--use-edge-profile", metavar="NAME",
        help="Seed the session from an existing Edge profile (e.g. MorkNet)",
    )
    parser.add_argument(
        "--account", default="mhugh@MngEnvMCAP777813.onmicrosoft.com",
        help="Demo-tenant account to pre-fill at sign-in",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run --create/--check without a window. Usually fails: Entra does "
             "not complete the session in headless Chromium, so the default is "
             "a visible window.",
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="(default) Show the browser window.",
    )
    parser.add_argument(
        "--timeout", type=int, default=600,
        help="Seconds to wait for sign-in during --login (default 600)",
    )
    args = parser.parse_args()

    if args.login:
        return cmd_login(args.account, args.timeout)
    if args.use_edge_profile:
        return cmd_use_edge_profile(args.use_edge_profile)
    if args.check:
        return cmd_check(headless=args.headless)
    if args.set_url:
        return _store(args.set_url)
    return cmd_create(headless=args.headless)


if __name__ == "__main__":
    sys.exit(main())
