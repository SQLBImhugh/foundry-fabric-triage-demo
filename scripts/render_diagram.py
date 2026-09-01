"""Render the README architecture diagram from SVG to PNG.

GitHub renders inline SVG in markdown inconsistently -- it strips some
attributes and refuses others outright -- so the README references a PNG. The
SVG is the source of truth; re-run this after editing it.
"""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "docs" / "images" / "readme" / "solution-architecture.svg"
PNG = SVG.with_suffix(".png")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1240, "height": 750},
                                device_scale_factor=2)
        page.goto(SVG.as_uri())
        page.wait_for_timeout(400)
        page.screenshot(path=str(PNG), clip={"x": 0, "y": 0, "width": 1240, "height": 750})
        browser.close()
    print(f"wrote {PNG.relative_to(ROOT)} ({PNG.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
