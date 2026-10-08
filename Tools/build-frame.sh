#!/bin/bash
#
# Rebuilds Tools/frame.png — the stamp-and-postmark overlay that
# trip-card-screenshot-template.html embeds as its built-in frame.
#
# frame.png is a derived asset with three sources, none of them obvious:
#
#   1. ../Instagram/Posts/_template.xcf, layers "Stamp" and "Waves". They are
#      placed on that file's own 1004x1339 canvas at its own offsets and the
#      whole canvas is then scaled to 1080x1440, so the geometry matches every
#      post already published. Verified: overlaying the result on
#      instagram-native-hemlock.png leaves ~100 of 1.5M pixels different, which
#      is antialiasing on the perforations. The "Postmark" layer is deliberately
#      NOT used — no published post carries it.
#
#   2. A logo, which replaces the circle inside the stamp. The original ring
#      measures 182x184 centred on (885.5, 183.5) in the finished frame, and
#      the replacement is scaled so its disc reaches 189px and covers it. Both
#      the scale and the offset are computed from the logo's own alpha bbox —
#      see the note at TARGET_DISC. Composited at final resolution so the logo
#      is resampled once, not twice.
#
#   3. Weathering (see weather.sh beside this file).
#
# Finally quantised to 256 colours: ~1% RMSE, and it cuts the file by 60-70%,
# which matters because the template inlines these bytes as a data URI.
#
# Usage: ./build-frame.sh [logo.png]
#   Defaults to the local app checkout, falling back to the live URL.
set -euo pipefail
cd "$(dirname "$0")"

XCF="../Instagram/Posts/_template.xcf"
LOCAL_LOGO="$HOME/Work/Please Shout/expect.me/Expect.Me/Expect.Me.WebApp/wwwroot/images/logo.png"
LOGO_URL="https://app.rangerly.net/images/logo.png"
OUT="frame.png"

T=$(mktemp -d); trap 'rm -rf "$T"' EXIT

# --- the stamp tile, with the current logo, weathered ---------------------
# build-stamp.sh owns the stamp: the logo swap and the weathering live there so
# this frame and the postcard frame carry the same stamp by construction rather
# than by two scripts agreeing. It takes the tile WIDTH; the Instagram stamp is
# 238px wide in the finished 1080x1440 frame.
SW=238
./build-stamp.sh "$SW" "$T/stamp.png" "$@"

# --- the waves ------------------------------------------------------------
# Same relative geometry as the XCF: on its 1004x1339 canvas the stamp sits at
# +714+60 and the waves at +970+105, and the whole thing is scaled by
# 1080/1004. That factor is what puts the stamp at 238 wide, so the waves take
# it too and the postmark stays one mark.
idx_of() {
  local n=0 label
  while :; do
    label=$(magick identify -verbose "$XCF[$n]" 2>/dev/null | grep -m1 -oP '(?<=label: ).*' || true)
    [ -z "$label" ] && { echo ""; return; }
    [ "$label" = "$1" ] && { echo "$n"; return; }
    n=$((n+1)); [ $n -gt 200 ] && { echo ""; return; }
  done
}
WI=$(idx_of Waves); [ -n "$WI" ] || { echo "no Waves layer in $XCF" >&2; exit 1; }

read -r SX SY WW WH WX WY <<<"$(awk 'BEGIN{
  sx = 1080/1004; sy = 1440/1339;
  printf "%d %d %d %d %d %d", 714*sx+0.5, 60*sy+0.5,
         34*sx+0.5, 147*sy+0.5, 970*sx+0.5, 105*sy+0.5 }')"
magick "$XCF[$WI]" +repage -filter Lanczos -resize ${WW}x${WH}! PNG32:"$T/waves.png"
echo "stamp at +${SX}+${SY}   waves ${WW}x${WH} at +${WX}+${WY}"

# --- compose, transparent middle ------------------------------------------
magick -size 1080x1440 xc:none \
  "$T/stamp.png" -geometry +${SX}+${SY} -composite \
  "$T/waves.png" -geometry +${WX}+${WY} -composite \
  -strip PNG32:"$T/frame.png"

magick "$T/frame.png" +dither -colors 256 PNG8:"$OUT"
echo "wrote $OUT ($(du -b "$OUT" | cut -f1) bytes)"
echo
echo "Now re-embed it in the template:  ./embed-frame.py"
