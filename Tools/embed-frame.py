#!/usr/bin/env python3
"""Inline Tools/frame.png into the template's DEFAULT_FRAME constant.

The template cannot just reference frame.png: the PNG exporter draws the card
into an SVG <foreignObject>, which is an isolated document that fetches
nothing, so a relative <img src> renders in the preview and comes out missing
from the exported file. The bytes have to be inline.
"""
import base64, pathlib, re, sys

here = pathlib.Path(__file__).parent
html = here / "trip-card-screenshot-template.html"
png = here / "frame.png"

uri = "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()
src = html.read_text()
out, n = re.subn(r"(const DEFAULT_FRAME = ')data:image/png;base64,[A-Za-z0-9+/=]+(';)",
                 lambda m: m.group(1) + uri + m.group(2), src)
if n != 1:
    sys.exit(f"expected exactly one DEFAULT_FRAME assignment, found {n}")
html.write_text(out)
print(f"embedded {png.name} ({png.stat().st_size} bytes -> {len(uri)} chars) into {html.name}")
