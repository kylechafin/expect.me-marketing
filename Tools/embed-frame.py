#!/usr/bin/env python3
"""Inline each builder's frame PNG into its DEFAULT_FRAME constant.

Neither template can just reference its frame file: the PNG exporter draws the
card into an SVG <foreignObject>, which is an isolated document that fetches
nothing, so a relative <img src> renders fine in the preview and comes out
missing from the exported file. The bytes have to be inline.

Run with no arguments to re-embed every pair, or name one template.
"""
import base64
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent

PAIRS = {
    "trip-card-screenshot-template.html": "frame.png",
    "postcard-template.html": "postcard-frame.png",
}

# Matches both the real constant and the placeholder a freshly written template
# carries, so a new tool can be wired up without hand-editing the blob in.
PATTERN = re.compile(
    r"(const DEFAULT_FRAME = ')"
    r"(?:data:image/png;base64,[A-Za-z0-9+/=]+|FRAME_DATA_URI_PLACEHOLDER)"
    r"(';)"
)


def embed(html_name: str, png_name: str) -> None:
    html = HERE / html_name
    png = HERE / png_name
    for path in (html, png):
        if not path.exists():
            sys.exit(f"missing {path.name}")

    uri = "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()
    src = html.read_text()
    out, n = PATTERN.subn(lambda m: m.group(1) + uri + m.group(2), src)
    if n != 1:
        sys.exit(f"{html.name}: expected exactly one DEFAULT_FRAME assignment, found {n}")
    html.write_text(out)
    print(f"{html.name:38s} <- {png.name} ({png.stat().st_size} bytes -> {len(uri)} chars)")


def main() -> None:
    wanted = sys.argv[1:] or list(PAIRS)
    for name in wanted:
        name = pathlib.Path(name).name
        if name not in PAIRS:
            sys.exit(f"unknown template {name!r}; known: {', '.join(PAIRS)}")
        embed(name, PAIRS[name])


if __name__ == "__main__":
    main()
