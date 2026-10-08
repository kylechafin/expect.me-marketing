#!/bin/bash
#
# Builds a weathered Rangerly stamp tile at any width, with the current logo
# swapped into it. Shared by build-frame.sh (the Instagram post overlay) and
# build-postcard-frame.sh, so both carry the same stamp by construction.
#
# The stamp art comes from ../Instagram/Posts/_template.xcf, layer "Stamp",
# which is 220x249. Everything about the logo swap is expressed as a FRACTION
# of that tile rather than in absolute pixels, so one set of numbers serves
# every output size:
#
#   RING_CX/RING_CY  where the original ring's centre sits in the tile
#   DISC             how wide the replacement disc must be to cover that ring
#
# Those three were measured off the source layer (ring bbox x25..193 y25..195,
# i.e. 171px across centred on 109,110 of 220x249) and reproduce the separately
# verified Instagram placement — a 238px tile gives centre (884.9,182.8) and a
# 190px disc, against the 885.5/183.5/189 measured directly in that frame.
#
# Usage: ./build-stamp.sh <width-px> <out.png> [logo.png]
set -euo pipefail
cd "$(dirname "$0")"

W=${1:?width required}; OUT=${2:?output path required}
XCF="../Instagram/Posts/_template.xcf"
LOCAL_LOGO="$HOME/Work/Please Shout/expect.me/Expect.Me/Expect.Me.WebApp/wwwroot/images/logo.png"
LOGO_URL="https://app.rangerly.net/images/logo.png"

RING_CX=0.49545   # of tile width
RING_CY=0.44177   # of tile height
DISC=0.80000      # of tile width

T=$(mktemp -d); trap 'rm -rf "$T"' EXIT

if [ $# -ge 3 ]; then LOGO="$3"
elif [ -f "$LOCAL_LOGO" ]; then LOGO="$LOCAL_LOGO"
else curl -fsS -o "$T/logo.png" "$LOGO_URL"; LOGO="$T/logo.png"; fi

# Find the Stamp layer by NAME — the index shifts if the XCF gains a layer.
idx_of() {
  local n=0 label
  while :; do
    label=$(magick identify -verbose "$XCF[$n]" 2>/dev/null | grep -m1 -oP '(?<=label: ).*' || true)
    [ -z "$label" ] && { echo ""; return; }
    [ "$label" = "$1" ] && { echo "$n"; return; }
    n=$((n+1)); [ $n -gt 200 ] && { echo ""; return; }
  done
}
SI=$(idx_of Stamp); [ -n "$SI" ] || { echo "no Stamp layer in $XCF" >&2; exit 1; }

# Tile height follows the source aspect (220x249) so the stamp never distorts.
H=$(awk -v w="$W" 'BEGIN{printf "%d", w*249/220 + 0.5}')
magick "$XCF[$SI]" +repage -filter Lanczos -resize ${W}x${H}! PNG32:"$T/tile.png"

# Logo scale and offset are derived from the logo's OWN alpha bbox: different
# exports pad the disc differently inside their canvas (the app's logo.png is a
# 248px disc with 4px margin, the gauge-running variants 214px with 21px), so a
# fixed resize renders one right and the other far too small, leaving a rim of
# the original ring showing. Normalising on the disc fixes every variant.
read -r LW LH DW DH DX DY <<<"$(magick "$LOGO" -format "%w %h " info: ; \
  magick "$LOGO" -alpha extract -format "%@" info: | tr 'x+' '  ')"
[ -n "${DY:-}" ] || { echo "could not read logo geometry from $LOGO" >&2; exit 1; }

read -r CANVAS OFFX OFFY <<<"$(awk -v lw="$LW" -v dw="$DW" -v dh="$DH" -v dx="$DX" -v dy="$DY" \
  -v w="$W" -v h="$H" -v rcx="$RING_CX" -v rcy="$RING_CY" -v disc="$DISC" 'BEGIN{
    d  = (dw > dh ? dw : dh);
    s  = (disc * w) / d;
    printf "%d %d %d", lw*s+0.5, rcx*w-(dx+dw/2)*s+0.5, rcy*h-(dy+dh/2)*s+0.5;
  }')"

magick "$LOGO" -filter Lanczos -resize ${CANVAS}x${CANVAS} PNG32:"$T/logo.png"
magick "$T/tile.png" "$T/logo.png" -geometry +${OFFX}+${OFFY} -composite PNG32:"$T/tile-logo.png"

./weather.sh "$T/tile-logo.png" "$OUT" 7 0.20 0.10 42 6
echo "stamp: ${W}x${H}  logo disc $(awk -v d="$DISC" -v w="$W" 'BEGIN{printf "%d", d*w+0.5}')px -> $OUT"
