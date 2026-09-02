"""Screenshot the walkthrough pages so layout problems are visible.

HTML that parses is not HTML that looks right. Undefined styles, a missing
wrapper or a broken image renders as something nobody would put in front of a
customer, and none of that shows up in a link check.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / ".browser-debug"
PAGES = {
    "personas": REPO / "demo" / "walkthrough" / "PERSONAS.html",
    "walkthrough": REPO / "demo" / "walkthrough" / "WALKTHROUGH.html",
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    failures = 0

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})

        # Registered once, against one list that is cleared per page. Attaching
        # handlers inside the loop leaks them: each page adds another listener,
        # and they all append to whichever list the closure happened to capture.
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("requestfailed", lambda r: errors.append(f"failed: {r.url[-70:]}"))

        for name, path in PAGES.items():
            errors.clear()

            page.goto(path.as_uri(), wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(2500)

            # Catch images that resolved but rendered at zero size.
            broken = page.evaluate(
                "Array.from(document.images)"
                ".filter(i => !i.complete || i.naturalWidth === 0)"
                ".map(i => i.getAttribute('src'))"
            )
            body_width = page.evaluate("document.body.scrollWidth")

            page.screenshot(path=str(OUT / f"page-{name}-top.png"))
            page.screenshot(path=str(OUT / f"page-{name}-full.png"), full_page=True)

            print(f"  {name:<12} images_broken={len(broken)} width={body_width} errors={len(errors)}")
            if broken:
                print(f"    broken: {broken[:4]}")
                failures += 1
            if errors:
                print(f"    console: {errors[:3]}")

        browser.close()

    print(f"\nscreenshots in {OUT}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
