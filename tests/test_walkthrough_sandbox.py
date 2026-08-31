"""Tests that the walkthrough pages survive the SharePoint and Teams preview.

These documents are handed to customers by sharing a link. SharePoint and Teams
do not serve an .html as a web page -- they render it through a preview host
that applies a restrictive Content-Security-Policy and drops anything external
*silently*. There is no error, no warning and nothing the viewer can act on.
Verified locally: before the assets were inlined, the preview policy refused 32
requests and the pages arrived unstyled with every figure empty.

That is a failure mode nobody notices until it is on a customer's screen, so it
is pinned here rather than left to a manual check before each share.

The browser-based proof lives in ``scripts/preview_check.py``, which serves the
pages under the real policy. These tests stay text-only so the suite keeps
running offline and without a browser.
"""

from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

inline_assets = pytest.importorskip("inline_assets")

WALKTHROUGH = REPO_ROOT / "walkthrough"
PAGES = ("WALKTHROUGH.html", "PERSONAS.html")


def _read(name: str) -> str:
    return (WALKTHROUGH / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", PAGES)
def test_page_is_sandbox_safe(name: str) -> None:
    """No external reference the preview host would silently refuse."""
    problems = inline_assets.check(_read(name))
    assert not problems, f"{name} would not preview correctly: {problems}"


@pytest.mark.parametrize("name", PAGES)
def test_every_image_is_inlined_and_traceable(name: str) -> None:
    """Images are embedded, and each records the file it came from.

    The provenance matters as much as the embedding: without ``data-src`` the
    payload becomes unattributable base64 that can never be refreshed when a
    screenshot is recaptured.
    """
    tags = re.findall(r"<img\b[^>]*>", _read(name))
    assert tags, f"{name} has no figures, which is not a state it should reach"

    for tag in tags:
        source = re.search(r'data-src="([^"]+)"', tag)
        assert source is not None, f"figure without provenance in {name}: {tag[:80]}"
        assert (WALKTHROUGH / source.group(1)).exists(), (
            f"{name} embeds {source.group(1)}, which no longer exists on disk"
        )
        assert inline_assets._SRC_ATTR.search(tag).group(1).startswith("data:")


@pytest.mark.parametrize("name", PAGES)
def test_pages_are_not_stale(name: str) -> None:
    """Re-inlining changes nothing, so the embedded copies match the sources.

    This is what stops a recaptured screenshot or an edited stylesheet from
    living on disk while the shared document still shows the old one.
    """
    current = _read(name)
    html, _ = inline_assets.inline_css(current, WALKTHROUGH)
    html, _ = inline_assets.inline_images(html, WALKTHROUGH)
    html, _ = inline_assets.flatten_doc_links(html)

    assert html == current, (
        f"{name} is out of date with walkthrough/ -- run scripts/inline_assets.py"
    )


@pytest.mark.parametrize("name", PAGES)
def test_embedded_svgs_fetch_no_fonts(name: str) -> None:
    """Rich writes its terminal SVGs with @font-face rules pointing at a CDN.

    The fetch never succeeded -- an SVG loaded through <img> cannot load
    external resources -- but leaving it in means shipping a document that
    asks a customer's browser for a third-party font.
    """
    payloads = re.findall(r'src="data:image/svg\+xml;base64,([^"]+)"', _read(name))
    assert payloads, f"{name} embeds no SVG figures"

    for payload in payloads:
        svg = base64.b64decode(payload).decode("utf-8")
        assert "cdnjs" not in svg
        assert not re.search(r"url\(\s*['\"]?https?:", svg)


@pytest.mark.parametrize("name", PAGES)
def test_no_dead_links_between_the_documents(name: str) -> None:
    """The sandbox drops outbound navigation, so a cross-link is a dead control.

    Both documents name the other file as text instead, which a reader can act
    on -- they sit in the same folder.
    """
    html = _read(name)
    assert not re.search(r'<a\b[^>]*href="[^"]*\.html', html, re.IGNORECASE)

    other = next(page for page in PAGES if page != name)
    assert other in html, f"{name} should still tell the reader about {other}"
