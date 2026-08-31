"""Render the walkthrough pages under SharePoint's content policy.

`render_check.py` proves the pages look right on disk. That is not the question
being asked here. SharePoint and Teams render an .html through a preview host
that applies a restrictive Content-Security-Policy and then *fails silently*:
a blocked stylesheet or image produces no error, no warning and no clue for the
viewer -- just a page that looks broken for no visible reason.

The only honest way to test for a silent failure is to reproduce the conditions
that cause it. This serves the two pages over loopback with the policy the
preview host applies, loads them, and fails if anything is refused or if any
figure comes back empty.

Loopback only. Nothing here touches the network.
"""

from __future__ import annotations

import functools
import http.server
import socketserver
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
WALKTHROUGH = REPO / "walkthrough"
PAGES = ("WALKTHROUGH.html", "PERSONAS.html")

# The policy the OneDrive/SharePoint HTML preview applies. Anything the page
# needs that is not permitted here is dropped without comment.
CSP = "; ".join(
    (
        "default-src 'none'",
        "script-src 'unsafe-inline' 'unsafe-eval'",
        "style-src 'unsafe-inline'",
        "img-src data: blob:",
        "font-src data: blob:",
        "connect-src 'none'",
        "worker-src 'none'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
    )
)

EXPECTED_BG = "rgb(13, 17, 23)"


class _Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Content-Security-Policy", CSP)
        super().end_headers()

    def log_message(self, *args: object) -> None:
        """Quiet: one line per request would bury the actual result."""


def main() -> int:
    handler = functools.partial(_Handler, directory=str(WALKTHROUGH))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as server:
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        print(f"serving {WALKTHROUGH.name} on 127.0.0.1:{port} under the preview policy\n")
        failures = 0

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 1000})

            refused: list[str] = []
            page.on(
                "console",
                lambda m: refused.append(m.text) if "Content Security Policy" in m.text else None,
            )
            page.on("requestfailed", lambda r: refused.append(f"blocked: {r.url[:70]}"))

            for name in PAGES:
                refused.clear()
                page.goto(f"http://127.0.0.1:{port}/{name}", wait_until="load", timeout=60_000)
                page.wait_for_timeout(2000)

                images = page.evaluate("document.images.length")
                broken = page.evaluate(
                    "Array.from(document.images)"
                    ".filter(i => !i.complete || i.naturalWidth === 0).length"
                )
                # If the inlined <style> were refused the page would still show
                # text, just unstyled -- exactly the failure that reaches a
                # customer unnoticed. The background colour is the tell.
                background = page.evaluate("getComputedStyle(document.body).backgroundColor")
                styled = background == EXPECTED_BG

                ok = not refused and broken == 0 and styled
                print(
                    f"  {name:<18} images={images} broken={broken} "
                    f"styled={str(styled).lower()} refused={len(refused)}  "
                    f"{'ok' if ok else 'FAIL'}"
                )
                if not styled:
                    print(f"    body background is {background}, expected {EXPECTED_BG}")
                for item in refused[:5]:
                    print(f"    {item[:110]}")
                if not ok:
                    failures += 1

            browser.close()

        server.shutdown()

    if failures:
        print(f"\n{failures} page(s) would not preview correctly in SharePoint or Teams")
        return 1

    print("\nboth pages render intact under the SharePoint preview policy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
