#!/usr/bin/env python3
"""Render Facebook/facebook-background.png — the Rangerly page cover.

WHY THIS EXISTS. The cover used to be the OpenGraph background with the old
stamp pasted on, saved at 1200x630. Facebook does two things to that file which
together are what "the curves look rasterized" means: it scales it up (a 1200px
image lands in a slot that is ~1250 CSS px wide, and 2x that on a HiDPI display)
and it re-encodes it. The curves are hairlines — 3.5px in the 3600px master,
which is 1.2px at 1200 — so upscaling and JPEG have nothing to work with and the
lines break up. Nothing you can do to a 1200px PNG fixes that. The fix is to
stop rasterising early: hold the whole composition as vectors and rasterise once,
at the size Facebook actually wants.

So the drawing here is not a resample of the old PNG. The geometry in
cover-geometry.json was measured off OpenGraph/rangerly-og-background-highres.png
(the 3600x1890 master) and stored in normalised 0..1 coordinates: the fourteen
arcs as traced centre-lines, the two waves as traced silhouettes, the sky as the
gradient stops that were fitted to it. Normalised means the composition retargets
to any frame, which is what let the cover move to Facebook's 1640:664 without
the bottom wave being cropped away the way it was at 1200x630.

The arcs are stored as polylines, not as circles. They look like a family of
concentric circles and they are not — fitting circles to the wide ones leaves
50px of residual. They are, near enough, individually drawn curves, so they are
kept as what was measured. Samples are 36 master-px apart, which on this frame
leaves a sagitta well under a tenth of a pixel; the faceting is not reachable.

The page is also rendered at 2x and box-filtered down, which is the other half of
the fix and the reason the arcs survive being resized again at the other end.
See finish(). Dithering the sky against banding was tried and is deliberately not
here; the note in finish() says why, so it does not get re-added.

There are two lockups, --lockup badge (the default) and --lockup stamp. The
stamp is the perforated one the posts carry, and it is redrawn here rather than
scaled up for the same reason the background is — see the block above stamp().

Usage:  ./build-facebook-cover.py [-o OUT] [--lockup badge|stamp]
                                  [--stamp-placement corner|safe] [--no-weather]
                                  [--width N] [--height N] [--keep-html]
"""

import argparse
import base64
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent

GEOMETRY = HERE / "cover-geometry.json"
WEATHER_SH = HERE / "weather.sh"
BADGE = HERE / "rangerly-badge.svg"
FONT = HERE / "fonts" / "plus-jakarta-sans-latin-wght-normal.woff2"
DEFAULT_OUT = REPO / "Facebook" / "facebook-background.png"

# Facebook renders a page cover from a 1640x664 source; this is that at 1.25x, so
# a HiDPI display is served real pixels rather than an upscale. Same 1640:664.
WIDTH, HEIGHT = 2048, 832

SUPERSAMPLE = 2       # render at this multiple, then box-filter down

PEACH = "#FFEDDA"     # the post margin colour, from Instagram/Posts
WORDMARK = "RANGERLY"

# The lockup, in fractions of the frame. It sits in the upper right, over the
# lighter half of the sky, where the peach has the most contrast to work with.
# Two things bound it. Facebook lays its own furniture over the cover's lower
# left (the profile picture) and lower right (the page's controls), so the
# lockup stays up top. And a phone does not get this aspect ratio: it crops the
# cover towards 16:9 about the centre, which from 1640:664 keeps roughly the
# middle 72% of the width — so the lockup ends at LOCKUP_RIGHT rather than
# running to the margin, and the whole of it stays inside x 0.14..0.86.
BADGE_D = 0.300       # badge diameter, as a fraction of frame height
BADGE_CY = 0.355      # badge centre y
LOCKUP_RIGHT = 0.850  # right edge of the whole lockup
GAP = 0.052           # badge-to-wordmark gap, as a fraction of frame height
TYPE_SIZE = 0.165     # wordmark font-size, as a fraction of frame height
TRACKING = 0.055      # wordmark letter-spacing, in em

# --- the stamp -------------------------------------------------------------
# The other lockup: the perforated stamp the posts and the old cover carry.
#
# It is REDRAWN here rather than scaled. The artwork is a layer in
# ../Instagram/Posts/_template.xcf called "Stamp", and it is 220x249 — Tools/
# build-frame.sh uses it at about 1:1 for a post. A cover wants it near 400px
# tall, and upscaling a 249px raster by 1.7 would put soft perforations and a
# mushy wordmark next to a background that is now razor sharp, which is the
# whole problem this file exists to fix. So every number below was measured off
# that layer and is redrawn at the output's resolution.
#
# Measured, in the layer's own 219x248 pixel units:
STAMP_W, STAMP_H = 219.0, 248.0
PERF_R = 6.4                     # notch radius; chord ~12.8, depth ~6.4
PERF_PITCH_X, PERF_PHASE_X = 19.78, -0.75
PERF_PITCH_Y, PERF_PHASE_Y = 20.13, 1.35
STAMP_CREAM = "#F9E4C6"
# x, y, w, h — the 50% crossing between cream and panel, measured sub-pixel on
# the clean layer rather than by thresholding, which moves the edge by a pixel.
PANEL = (15.37, 15.76, 187.47, 185.28)
PANEL_GREEN = "#1A3C0A"
# The badge's disc covers the old ring. Both numbers come from build-frame.sh's
# TARGET_DISC/RING_C*, divided back out of the 1080/1004 scale it works in, so
# the badge lands exactly where that pipeline puts it on a post.
STAMP_BADGE_D = 189.0 / (1080 / 1004)
STAMP_BADGE_C = (885.5 / (1080 / 1004) - 714, 183.5 / (1080 / 1004) - 60)
STAMP_TEXT_CAP = 22.0            # cap height of RANGERLY, as inked
STAMP_TEXT_W = 178.0             # its width, which sets the tracking
# Weight and size are matched to the artwork rather than assumed. Setting the
# wordmark at the house 800 comes out visibly heavier than the stamp's: measured
# as the fraction of the wordmark's box that is ink, the artwork is 0.355 and
# Plus Jakarta Sans runs 0.318 at 600 and 0.362 at 650, which puts it at ~640.
# CAP_RATIO is the face's inked cap height per em, measured off an 800px render
# (615px, including the ~1% overshoot on the G, which is how the artwork's 22
# was measured too). getBBox is no use for this — it returns the line box.
STAMP_TEXT_WEIGHT = 650
CAP_RATIO = 0.7688
STAMP_TEXT_BASE = 232.0          # baseline (bottom of the caps)
STAMP_TEXT_CX = 110.5            # centred on the body
STAMP_INK = "#094214"

STAMP_FRAME_H = 0.492            # stamp height, as a fraction of frame height

# Where the stamp sits — (right edge as a fraction of width, top edge as a
# fraction of height). The two are a real trade, which is why both are kept
# rather than one being chosen here.
#
#   corner  Franked into the top right the way one sits on a postcard. The
#           margins are the old 1200x630 cover's, which was composed the same
#           way (stamp at x 0.742..0.971, y 0.048..0.548 — about 35px of right
#           margin and 30px of top on that frame), carried across by proportion
#           of each axis. It puts the stamp at x 0.79..0.97, which is the part
#           of a cover a phone is most likely to crop. The old cover had that
#           same exposure.
#
#   safe    The same stamp pulled in to where the badge lockup lives, ending at
#           0.85 and centred on 0.33 of the height, so the whole of it stays
#           inside the middle of the frame. Reads less like a postcard and more
#           like a logo placement, and survives the crop.
STAMP_PLACEMENTS = {
    "corner": (0.971, 0.048),
    "safe": (0.850, 0.330 - STAMP_FRAME_H / 2),   # centred on 0.33 of the height
}
STAMP_PLACEMENT = "corner"       # the default; --stamp-placement overrides

# Ageing, handed to weather.sh. These are build-frame.sh's numbers verbatim, so
# the cover's stamp wears the same as the one on every post. WEATHER_REF is the
# stamp height those numbers were dialled in at; the ratio to the stamp's actual
# height goes to weather.sh as its feature scale, which is what keeps the
# blotches and the grain the same size on the finished stamp rather than letting
# them go fine as the render gets bigger.
WEATHER = ("7", "0.20", "0.10", "42", "6")
WEATHER_REF = 269.0


def data_uri(path: pathlib.Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def polyline(pts, w, h):
    """Normalised points -> an SVG path in output pixels."""
    d = []
    for i, (x, y) in enumerate(pts):
        d.append(("M" if i == 0 else "L") + f"{x * w:.2f},{y * h:.2f}")
    return "".join(d)


def build_svg(geo, w, h, with_lockup=True, background=True,
              placement=STAMP_PLACEMENT):
    """The cover as one SVG, in output-pixel coordinates.

    background=False draws the lockup alone on transparency, which is how the
    stamp gets rendered for its own weathering pass.
    """
    out = []
    out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
               f'viewBox="0 0 {w} {h}">')
    if not background:
        out.append(stamp(w, h, placement) if with_lockup == "stamp" else lockup(w, h))
        out.append('</svg>')
        return "\n".join(out)

    stops = "".join(
        f'<stop offset="{o}" stop-color="{c}"/>' for o, c in geo["sky"]["stops"])
    out.append(f'<defs><linearGradient id="sky" x1="0" y1="0" x2="1" y2="0">{stops}'
               '</linearGradient></defs>')
    out.append(f'<rect width="{w}" height="{h}" fill="url(#sky)"/>')

    # The arcs run under the waves in the master, so they are drawn first and
    # painted over. Each carries its own colour: the family ramps from #2A5235 at
    # the innermost to #3A7746 at the outermost, and that ramp is in the data.
    # Stroke width is held to the master's 3.5/3600 of the frame, with a floor of
    # 2.2px so the lines still have a body to them after Facebook resizes.
    sw = max(2.2, 3.5 / 3600.0 * w)
    out.append(f'<g fill="none" stroke-width="{sw:.2f}" stroke-linecap="round">')
    for arc in geo["arcs"]:
        out.append(f'<path stroke="{arc["color"]}" d="{polyline(arc["pts"], w, h)}"/>')
    out.append('</g>')

    for wave in geo["waves"]:
        d = polyline(wave["pts"], w, h)
        d += f"L{w},{h}L0,{h}Z"
        out.append(f'<path fill="{wave["color"]}" d="{d}"/>')

    if with_lockup == "stamp":
        out.append(stamp(w, h, placement))
    elif with_lockup:
        out.append(lockup(w, h))
    out.append('</svg>')
    return "\n".join(out)


def badge_body(prefix="bdg"):
    """The badge's drawing, from <defs> down, with its ids namespaced."""
    # Comments go first: the badge file's own comment says the word "<defs>", and
    # slicing from the first match would start the body inside it.
    body = re.sub(r"<!--.*?-->", "", BADGE.read_text(), flags=re.S)
    body = body[body.index("<defs>"):body.rindex("</svg>")]
    for name in ("ember", "pine", "disc", "inner-ring"):
        body = body.replace(f'id="{name}"', f'id="{prefix}-{name}"')
        body = body.replace(f"url(#{name})", f"url(#{prefix}-{name})")
    return body


def lockup(w, h):
    """Badge plus wordmark, right-aligned in the upper right."""
    badge_d = BADGE_D * h
    size = TYPE_SIZE * h
    gap = GAP * h

    # The wordmark is measured by the browser, so the lockup is laid out from its
    # right edge: the text ends at LOCKUP_RIGHT and the badge sits a gap to its
    # left of wherever the text starts. text-anchor="end" does the first half;
    # the badge is placed by script once the text has a width. See measure().
    text_x = LOCKUP_RIGHT * w
    cy = BADGE_CY * h

    body = badge_body()
    scale = badge_d / 64.0
    return (
        f'<g id="badge" transform="translate(0,0)">'
        f'<g transform="translate({-badge_d / 2:.2f},{cy - badge_d / 2:.2f}) '
        f'scale({scale:.6f})">{body}</g></g>'
        f'<text id="wordmark" x="{text_x:.2f}" y="{cy:.2f}" text-anchor="end" '
        f'dominant-baseline="central" fill="{PEACH}" '
        f'font-family="Plus Jakarta Sans" font-weight="800" '
        f'font-size="{size:.2f}" letter-spacing="{TRACKING * size:.2f}">{WORDMARK}</text>'
    )


def stamp(w, h, placement=STAMP_PLACEMENT):
    """The perforated stamp, redrawn as vectors at the output's resolution.

    Laid out in the artwork's own 219x248 units and dropped onto the cover with
    one transform, so every constant above can stay as it was measured.

    The perforations are a mask rather than a notched outline path: a white body
    with black circles punched along each edge is the same picture, antialiases
    the same way, and is far less to get wrong than writing the outline out as
    arcs. The notch circles sit centred ON the edge, so each shows as a half
    circle, which is what the artwork has.
    """
    sh = STAMP_FRAME_H * h
    scale = sh / STAMP_H
    sw = STAMP_W * scale
    right, top = STAMP_PLACEMENTS[placement]
    x0 = right * w - sw
    y0 = top * h

    holes = []
    k = 0
    while PERF_PHASE_X + k * PERF_PITCH_X <= STAMP_W + PERF_R:
        cx = PERF_PHASE_X + k * PERF_PITCH_X
        holes.append((cx, 0.0))
        holes.append((cx, STAMP_H))
        k += 1
    k = 0
    while PERF_PHASE_Y + k * PERF_PITCH_Y <= STAMP_H + PERF_R:
        cy = PERF_PHASE_Y + k * PERF_PITCH_Y
        holes.append((0.0, cy))
        holes.append((STAMP_W, cy))
        k += 1
    punches = "".join(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{PERF_R}" fill="black"/>'
                      for cx, cy in holes)

    px, py, pw, ph = PANEL
    bd = STAMP_BADGE_D
    bcx, bcy = STAMP_BADGE_C

    # Set to the measured cap height, then tracked out in the browser until it is
    # the measured width.
    size = STAMP_TEXT_CAP / CAP_RATIO

    return (
        f'<g id="stamp" transform="translate({x0:.2f},{y0:.2f}) scale({scale:.6f})">'
        f'<defs><mask id="perf" maskUnits="userSpaceOnUse" '
        f'x="{-PERF_R}" y="{-PERF_R}" '
        f'width="{STAMP_W + 2 * PERF_R}" height="{STAMP_H + 2 * PERF_R}">'
        f'<rect width="{STAMP_W}" height="{STAMP_H}" fill="white"/>{punches}</mask></defs>'
        f'<g mask="url(#perf)">'
        f'<rect width="{STAMP_W}" height="{STAMP_H}" fill="{STAMP_CREAM}"/>'
        f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" fill="{PANEL_GREEN}"/>'
        f'<g transform="translate({bcx - bd / 2:.3f},{bcy - bd / 2:.3f}) '
        f'scale({bd / 64.0:.6f})">{badge_body("stmp")}</g>'
        f'<text id="stamp-word" x="{STAMP_TEXT_CX}" y="{STAMP_TEXT_BASE}" '
        f'text-anchor="middle" fill="{STAMP_INK}" '
        f'font-family="Plus Jakarta Sans" font-weight="{STAMP_TEXT_WEIGHT}" '
        f'font-size="{size:.3f}">{WORDMARK}</text>'
        f'</g></g>'
    )


def build_html(geo, w, h, with_lockup=True, background=True,
               placement=STAMP_PLACEMENT):
    font = data_uri(FONT, "font/woff2")
    svg = build_svg(geo, w, h, with_lockup, background, placement)
    badge_d = BADGE_D * h
    gap = GAP * h
    # The badge cannot be positioned until the wordmark has been measured, and the
    # face has to be in before that measurement means anything — a headless
    # profile has no Plus Jakarta Sans, and without the @font-face below the text
    # silently sets in the fallback and comes out both wrong and the wrong width.
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Rangerly cover</title>
<style>
@font-face {{
  font-family: "Plus Jakarta Sans";
  src: url({font}) format("woff2-variations");
  font-weight: 200 800;
  font-style: normal;
}}
html, body {{ margin: 0; padding: 0; background: {"#000" if background else "transparent"}; }}
svg {{ display: block; }}
</style></head>
<body>
{svg}
<script>
Promise.all([
  document.fonts.load('800 100px "Plus Jakarta Sans"'),
  document.fonts.load('{STAMP_TEXT_WEIGHT} 100px "Plus Jakarta Sans"'),
]).then(function () {{
  var text = document.getElementById('wordmark');
  if (text) {{
    // Right-aligned text, so its left edge is the badge's right edge plus the gap.
    var cx = text.getBBox().x - {gap:.2f} - {badge_d / 2:.2f};
    document.getElementById('badge').setAttribute(
      'transform', 'translate(' + cx.toFixed(2) + ',0)');
  }}
  var word = document.getElementById('stamp-word');
  if (word) {{
    // Track the wordmark out until it is the width it is in the artwork. The
    // tracking has to be solved rather than assumed: it is what makes a face
    // the artwork was not necessarily set in land on the same footprint.
    var n = word.textContent.length;
    var natural = word.getBBox().width;
    word.setAttribute('letter-spacing',
      (({STAMP_TEXT_W} - natural) / (n - 1)).toFixed(4));
    // Letter-spacing is added after the last glyph too, so a centred string
    // drifts left by half a space; shift it back.
    var extra = parseFloat(word.getAttribute('letter-spacing'));
    word.setAttribute('x', ({STAMP_TEXT_CX} + extra / 2).toFixed(3));
  }}
  document.documentElement.setAttribute('data-ready', '1');
}});
</script>
</body></html>
"""


def render(html_path, out_path, w, h):
    chromium = shutil.which("chromium") or shutil.which("chromium-browser")
    if not chromium:
        sys.exit("chromium not found on PATH")
    subprocess.run(
        [chromium, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
         "--force-device-scale-factor=1", f"--window-size={w},{h}",
         "--default-background-color=00000000", "--virtual-time-budget=15000",
         f"--screenshot={out_path}", html_path.as_uri()],
        check=True, capture_output=True)


def weather_stamp(png, height):
    """Run weather.sh over the rendered stamp, in place."""
    if not WEATHER_SH.exists():
        sys.exit(f"{WEATHER_SH} not found")
    scale = f"{height / WEATHER_REF:.4f}"
    subprocess.run(["bash", str(WEATHER_SH), str(png), str(png), *WEATHER, scale],
                   check=True, capture_output=True)


def finish(shot, out, w, h, stamp_png=None):
    """Box-filter the 2x render down to size.

    An exact 2x2 average, so every arc lands with real intermediate values along
    its edges rather than the single row of antialiasing a direct render gives it.
    That shoulder is what there is to resample when Facebook resizes the file
    again at the other end, and it is half of why the arcs come out clean.

    NO DITHER, AND THIS IS ON PURPOSE. The sky is a ramp of ~28 levels of green
    over a thousand-odd pixels, so undithered it sits on a flat plateau for 68% of
    its width — textbook banding, and the obvious thing is to scatter noise over
    it. Measured, it is not worth it. Facebook re-encodes the cover to JPEG, and
    after that pass the plateau figure is 3-6% whether or not the PNG was
    dithered: the encoder's own quantisation noise has already broken the bands
    up. What the dither does buy is cost. Uniform noise at +/-1.2 levels is
    incompressible and took the file from 148KB to 1.58MB. An ordered 4x4 Bayer
    pattern is far cheaper at 168KB, but it is periodic, and a periodic pattern
    rescaled by an arbitrary factor is how you get moire — which is a worse
    failure than the banding, and one that only shows up after upload.

    So: nothing. The banding is reachable only under about a 40x contrast boost,
    the arcs crossing the sky break it up perceptually, and the one pipeline this
    file actually goes through removes it.
    """
    im = Image.open(shot).convert("RGBA")
    if im.size != (w * SUPERSAMPLE, h * SUPERSAMPLE):
        sys.exit(f"expected a {w * SUPERSAMPLE}x{h * SUPERSAMPLE} render, got {im.size}")
    if stamp_png is not None:
        # The stamp is weathered on its own, because weather.sh nibbles the alpha
        # to rough up the perforations — it has to see the stamp against nothing,
        # not against the sky. Composited here, still at 2x, so it goes through
        # the same box filter as everything else.
        st = Image.open(stamp_png).convert("RGBA")
        if st.size != im.size:
            sys.exit(f"stamp render is {st.size}, expected {im.size}")
        im = Image.alpha_composite(im, st)
    a = np.asarray(im.convert("RGB"), dtype=np.float64)
    a = a.reshape(h, SUPERSAMPLE, w, SUPERSAMPLE, 3).mean(axis=(1, 3))

    Image.fromarray(np.clip(np.rint(a), 0, 255).astype(np.uint8)).save(
        out, "PNG", optimize=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--width", type=int, default=WIDTH)
    ap.add_argument("--height", type=int, default=HEIGHT)
    ap.add_argument("--keep-html", action="store_true",
                    help="leave the intermediate HTML next to the output")
    ap.add_argument("--lockup", choices=("badge", "stamp"), default="badge",
                    help="badge: the mark beside a RANGERLY wordmark (default). "
                         "stamp: the perforated stamp the posts carry.")
    ap.add_argument("--stamp-placement", choices=tuple(STAMP_PLACEMENTS),
                    default=STAMP_PLACEMENT,
                    help="corner: franked into the top right, postcard style "
                         "(default). safe: pulled inside the frame so a phone's "
                         "crop cannot take it.")
    ap.add_argument("--no-weather", action="store_true",
                    help="with --lockup stamp, leave the stamp clean instead of "
                         "ageing it the way the posts' stamp is aged")
    ap.add_argument("--background-only", action="store_true",
                    help="draw the background without the lockup; rendering this at "
                         "--width 3600 --height 1890 reproduces the master, which is "
                         "how the traced geometry is checked")
    args = ap.parse_args()

    geo = json.loads(GEOMETRY.read_text())
    w, h = args.width, args.height
    W, H = w * SUPERSAMPLE, h * SUPERSAMPLE
    kind = False if args.background_only else args.lockup
    # The stamp is drawn in a pass of its own so weather.sh can see its alpha;
    # the badge lockup needs no such thing and goes down with the background.
    two_pass = kind == "stamp" and not args.no_weather

    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        html_path = (args.out.with_suffix(".html") if args.keep_html
                     else tmp / "cover.html")
        html_path.write_text(build_html(
            geo, W, H, False if two_pass else kind,
            placement=args.stamp_placement))
        shot = tmp / "shot.png"
        render(html_path, shot, W, H)

        stamp_png = None
        if two_pass:
            stamp_html = tmp / "stamp.html"
            stamp_html.write_text(build_html(geo, W, H, "stamp", background=False,
                                             placement=args.stamp_placement))
            stamp_png = tmp / "stamp.png"
            render(stamp_html, stamp_png, W, H)
            weather_stamp(stamp_png, STAMP_FRAME_H * H)

        args.out.parent.mkdir(parents=True, exist_ok=True)
        finish(shot, args.out, w, h, stamp_png=stamp_png)

    print(f"{args.out}  {w}x{h}  {args.out.stat().st_size:,} bytes"
          + ("  (stamp weathered)" if two_pass else ""))


if __name__ == "__main__":
    main()
