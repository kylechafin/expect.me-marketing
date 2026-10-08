#!/bin/bash
# weather.sh <in> <out> <seed> <mottle> <grain> <wear> <fade> [scale]
#   mottle 0..1   how far below white the ageing blotches reach (0.10 = subtle)
#   grain  0..1   film-grain amplitude (0.06 is a light tooth)
#   wear   0..100 % of the perforation band nibbled away (higher = more worn)
#   fade   0..10  how far the blacks lift, i.e. how tired the ink looks
#   scale  >0     how big the texture's features are, relative to the sizes
#                 dialled in for a stamp ~270px tall. The blotches, the grain
#                 cell and the nibbled band are all absolute pixel measurements,
#                 so rendering the same stamp larger and leaving them alone
#                 gives you a finer, tidier texture rather than the same one.
#                 Pass the height ratio — a stamp at 3x wants 3 — and the look
#                 holds. DEFAULTS TO 1, which is these radii exactly as they
#                 were, so build-frame.sh's output does not move.
set -e
IN=$1; OUT=$2; SEED=${3:-7}; MOTTLE=${4:-0.10}; GRAIN=${5:-0.06}; WEAR=${6:-62}; FADE=${7:-3}
SCALE=${8:-1}
r() { awk -v s="$SCALE" -v v="$1" 'BEGIN{printf "%.4f", s*v}'; }
W=$(magick identify -format %w "$IN"); H=$(magick identify -format %h "$IN")
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT

magick "$IN" -alpha extract "$T/a.png"
magick "$IN" -alpha off "$T/rgb.png"

# 1. Tired ink: a touch less saturation, blacks lifted slightly, paper warmed.
magick "$T/rgb.png" -modulate 100,96,100 +level ${FADE}%,100% \
  -fill '#d9bc86' -colorize 3 "$T/tone.png"

# 2. Uneven ageing. The field sits just below white and multiplies in, so it
#    only ever darkens a little — no grey wash over the whole stamp.
OFF=$(awk -v m=$MOTTLE 'BEGIN{printf "%.4f", 1-m}')
magick -size ${W}x${H} -seed $SEED plasma:fractal -colorspace Gray \
  -blur 0x$(r 5) -auto-level -function polynomial "$MOTTLE,$OFF" "$T/mottle.png"
magick "$T/tone.png" \( "$T/mottle.png" -colorspace sRGB \) \
  -compose multiply -composite "$T/m.png"

# 3. Paper grain. Composed as Dc + k*(Sc-0.5) via Mathematics rather than
#    soft-light: IM's soft-light does not treat a 50% grey field as neutral and
#    lifted the stamp's mean from 0.47 to 0.71 — a wash, not a texture. This
#    form is signed and provably neutral at Sc=0.5.
HALF=$(awk -v k=$GRAIN 'BEGIN{printf "%.4f", -k/2}')
# One grain cell is one pixel at scale 1. Above that the field is generated
# small and scaled up, so a cell stays the same size on the finished stamp
# instead of dissolving into something too fine to read.
GW=$(awk -v w=$W -v s=$SCALE 'BEGIN{printf "%d", (w/s < 1 ? 1 : w/s)}')
GH=$(awk -v h=$H -v s=$SCALE 'BEGIN{printf "%d", (h/s < 1 ? 1 : h/s)}')
magick -size ${GW}x${GH} -seed $((SEED+31)) xc:gray50 \
  -attenuate 1.0 +noise Gaussian -colorspace Gray \
  -resize ${W}x${H}\! "$T/grain.png"
magick "$T/m.png" \( "$T/grain.png" -colorspace sRGB \) \
  -define compose:args=0,$GRAIN,1,$HALF -compose Mathematics -composite "$T/g.png"

# 4. Worn perforations: nibble the alpha only inside a band along the edge, so
#    the scalloped border goes irregular and the interior stays solid.
magick "$T/a.png" -morphology Erode Disk:$(r 2.5) "$T/inner.png"
magick "$T/inner.png" "$T/a.png" -compose minus -composite "$T/edge.png"
magick -size ${W}x${H} -seed $((SEED+97)) plasma:fractal -colorspace Gray \
  -blur 0x$(r 1.2) -auto-level -threshold ${WEAR}% "$T/speckle.png"
magick "$T/edge.png" "$T/speckle.png" -compose multiply -composite "$T/bite.png"
magick "$T/bite.png" "$T/a.png" -compose minus -composite "$T/a2.png"

magick "$T/g.png" "$T/a2.png" -alpha off -compose CopyOpacity -composite PNG32:"$OUT"
