"""Fold the walkthrough's CSS and images into the HTML so it previews anywhere.

An .html opened from SharePoint or Teams is not served as a web page. It is
rendered inside a sandboxed iframe whose policy is roughly `default-src 'none'`
with `img-src data: blob:`, no network, no storage, no navigation. External
stylesheets, images and web fonts are dropped *silently* -- no console error and
nothing shown to the viewer. A deck that looks right on the presenter's laptop
arrives at the customer as unstyled text with empty figures, and the first
anyone hears about it is in the meeting.

So each asset is folded into the file itself. The original path is recorded in a
`data-src` attribute, which is what makes the operation repeatable: after a
screenshot is recaptured, re-running refreshes the payload from the recorded
path instead of needing the external reference put back by hand. It also keeps
the source filenames greppable, which several doc tests rely on.

Usage:
    python scripts/inline_assets.py           # inline, in place
    python scripts/inline_assets.py --check   # verify only, non-zero if unsafe
"""

from __future__ import annotations

import argparse
import base64
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger("triage.inline_assets")

REPO = Path(__file__).resolve().parents[1]
WALKTHROUGH = REPO / "walkthrough"
PAGES = ("WALKTHROUGH.html", "PERSONAS.html")

MIME = {
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# Rich exports its terminal SVGs with @font-face rules pointing at cdnjs. The
# fetch never succeeds anyway -- an SVG loaded through <img> renders in a
# restricted mode with external resource loading disabled -- so dropping the
# remote sources changes nothing visually while removing a policy violation.
# local() is deliberately kept: a machine with Fira Code installed still gets
# the exact glyphs, and everything else falls back to monospace as it does now.
_FONT_URL = re.compile(r",\s*url\([^)]*\)\s*format\([^)]*\)")

_IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
# A plain \bsrc=" also matches inside data-src=", because the hyphen counts as a
# word boundary. That silently rewrites the provenance attribute instead of the
# real one on the second run, so the boundary is spelled out explicitly.
_SRC_ATTR = re.compile(r'(?<![-\w])src="([^"]*)"')
_LINK_CSS = re.compile(r'<link\b[^>]*rel="stylesheet"[^>]*href="([^"]+)"[^>]*>', re.IGNORECASE)
_STYLE_BLOCK = re.compile(r'<style\b[^>]*\bdata-src="([^"]+)"[^>]*>.*?</style>', re.DOTALL | re.IGNORECASE)
_DOC_LINK = re.compile(r'<a\b[^>]*href="([^"]+\.html)(?:#[^"]*)?"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)


def _data_uri(path: Path) -> str:
    """Encode one asset as a data: URI, cleaning SVGs on the way through."""
    mime = MIME.get(path.suffix.lower())
    if mime is None:
        raise ValueError(f"no mime type registered for {path.suffix}")

    if path.suffix.lower() == ".svg":
        payload = _FONT_URL.sub("", path.read_text(encoding="utf-8")).encode("utf-8")
    else:
        payload = path.read_bytes()

    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


def inline_css(html: str, base: Path) -> tuple[str, int]:
    """Replace the stylesheet link -- or refresh an already-inlined block."""
    count = 0

    # Both patterns capture the stylesheet path in group 1: the <link> on a
    # first run, the recorded data-src on every run after that.
    def embed(match: re.Match[str]) -> str:
        nonlocal count
        rel = match.group(1)
        path = base / rel
        if not path.exists():
            # The embedded copy is generated output. Losing the source leaves a
            # few hundred lines of CSS duplicated across two files with nothing
            # to regenerate them from, and the pages keep rendering, so this has
            # to be loud or it is invisible.
            raise FileNotFoundError(
                f"{rel} is referenced by the walkthrough but does not exist; "
                "the inlined stylesheet cannot be maintained without it"
            )
        count += 1
        return f'<style data-src="{rel}">\n{path.read_text(encoding="utf-8").strip()}\n</style>'

    # Refresh already-inlined blocks first, then convert any remaining <link>.
    # The other order re-processes the block this call just created.
    html = _STYLE_BLOCK.sub(embed, html)
    html = _LINK_CSS.sub(embed, html)
    return html, count


def inline_images(html: str, base: Path) -> tuple[str, int]:
    """Rewrite every <img> to a data: URI, recording where it came from."""
    count = 0
    missing: list[str] = []

    def rewrite(match: re.Match[str]) -> str:
        nonlocal count
        tag = match.group(0)

        recorded = re.search(r'\bdata-src="([^"]+)"', tag)
        if recorded is not None:
            rel = recorded.group(1)
        else:
            current = _SRC_ATTR.search(tag)
            if current is None or current.group(1).startswith(("data:", "blob:")):
                return tag
            rel = current.group(1)

        path = base / rel
        if not path.exists():
            missing.append(rel)
            return tag

        uri = _data_uri(path)
        # A lambda replacement keeps base64 from being read as backreferences.
        tag = _SRC_ATTR.sub(lambda _: f'src="{uri}"', tag, count=1)
        if recorded is None:
            tag = tag.replace("<img", f'<img data-src="{rel}"', 1)
        count += 1
        return tag

    html = _IMG_TAG.sub(rewrite, html)
    if missing:
        raise FileNotFoundError(f"referenced images do not exist: {missing}")
    return html, count


def flatten_doc_links(html: str) -> tuple[str, int]:
    """Turn links between the pages into text naming the other file.

    The sandbox drops outbound navigation, so a cross-document link is a
    control that looks live and does nothing. Naming the file instead is at
    least actionable: both documents sit in the same folder.
    """
    count = 0

    def rewrite(match: re.Match[str]) -> str:
        nonlocal count
        target, label = match.group(1), match.group(2).strip()
        count += 1
        return f'<span class="docref">{label} <b>{target}</b></span>'

    return _DOC_LINK.sub(rewrite, html), count


def check(html: str) -> list[str]:
    """The OneDrive/SharePoint pre-save checklist, as assertions.

    Every item here is a silent-failure mode: the preview reports none of them.
    """
    problems: list[str] = []

    if not re.search(r"<meta[^>]+charset", html, re.IGNORECASE):
        problems.append("missing <meta charset>")

    if re.search(r"<script\b[^>]*\bsrc=", html, re.IGNORECASE):
        problems.append("external <script src>")
    if re.search(r'<link\b[^>]*rel="(stylesheet|preconnect|preload|dns-prefetch)"', html, re.IGNORECASE):
        problems.append("external <link>")

    for tag in _IMG_TAG.findall(html):
        src = _SRC_ATTR.search(tag)
        if src is not None and not src.group(1).startswith(("data:", "blob:")):
            problems.append(f"non-data <img src>: {src.group(1)[:60]}")

    # Only flags fetchable references. An xmlns is an identifier, not a request.
    for url in re.findall(r"url\(\s*['\"]?(?!data:|blob:|#)([^)'\"]+)", html):
        problems.append(f"external css url(): {url[:60]}")
    if re.search(r"@import\b", html):
        problems.append("@import in CSS")

    for tag in ("iframe", "object", "embed", "base", "frame"):
        if re.search(rf"<{tag}\b", html, re.IGNORECASE):
            problems.append(f"<{tag}> is blocked")
    if re.search(r'http-equiv="refresh"', html, re.IGNORECASE):
        problems.append("meta refresh is blocked")
    if re.search(r"<form\b[^>]*\baction=", html, re.IGNORECASE):
        problems.append("<form action> cannot submit")

    # Scoped to script bodies so ordinary prose cannot trip these.
    for body in re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.DOTALL | re.IGNORECASE):
        for pattern, label in (
            (r"\bfetch\s*\(", "fetch()"),
            (r"\bXMLHttpRequest\b", "XMLHttpRequest"),
            (r"\bWebSocket\b", "WebSocket"),
            (r"\bEventSource\b", "EventSource"),
            (r"\bnavigator\.sendBeacon\b", "sendBeacon"),
            (r"\b(local|session)Storage\b", "web storage"),
            (r"\bindexedDB\b", "indexedDB"),
            (r"\bdocument\.cookie\b", "cookies"),
            (r"\bwindow\.open\s*\(", "window.open()"),
            (r"\blocation\.(href\s*=|assign|replace)", "navigation"),
            (r"\bnew\s+(Shared)?Worker\b", "workers"),
            (r"\bnavigator\.serviceWorker\b", "service worker"),
            (r"\bimport\s*[({]", "import"),
        ):
            if re.search(pattern, body):
                problems.append(f"script uses {label}")

    return problems


def process(path: Path, *, check_only: bool) -> list[str]:
    """Inline one page and return any remaining sandbox problems."""
    original = path.read_text(encoding="utf-8")

    if check_only:
        html = original
    else:
        html, css = inline_css(original, path.parent)
        html, images = inline_images(html, path.parent)
        html, links = flatten_doc_links(html)

        if html != original:
            path.write_text(html, encoding="utf-8")
        size = len(html.encode("utf-8")) / 1024
        state = "unchanged" if html == original else "rewritten"
        print(f"  {path.name:<18} css={css} images={images} links={links} {size:>7.0f} KB  {state}")

    return check(html)


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify only, change nothing")
    args = parser.parse_args()

    print("checking" if args.check else "inlining")
    failures = 0
    for name in PAGES:
        problems = process(WALKTHROUGH / name, check_only=args.check)
        if problems:
            failures += 1
            print(f"  {name}: NOT SAFE")
            for problem in problems:
                print(f"    - {problem}")

    if failures:
        print(f"\n{failures} page(s) would not preview correctly")
        return 1

    print("\nboth pages are self-contained and sandbox-safe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
