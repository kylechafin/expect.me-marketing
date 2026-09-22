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

# --- the logo -------------------------------------------------------------
if [ $# -ge 1 ]; then LOGO="$1"
elif [ -f "$LOCAL_LOGO" ]; then LOGO="$LOCAL_LOGO"
else curl -fsS -o "$T/logo.png" "$LOGO_URL"; LOGO="$T/logo.png"; fi
echo "logo:  $LOGO"

# --- the stamp and waves, by layer NAME rather than index -----------------
idx_of() {
  magick identify "$XCF" 2>/dev/null | wc -l >/dev/null
  local n=0
  while :; do
    local label
    label=$(magick identify -verbose "$XCF[$n]" 2>/dev/null | grep -m1 -oP '(?<=label: ).*' || true)
    [ -z "$label" ] && { echo ""; return; }
    [ "$label" = "$1" ] && { echo "$n"; return; }
    n=$((n+1)); [ $n -gt 200 ] && { echo ""; return; }
  done
}
STAMP=$(idx_of Stamp); WAVES=$(idx_of Waves)
[ -n "$STAMP" ] && [ -n "$WAVES" ] || { echo "could not find Stamp/Waves layers in $XCF" >&2; exit 1; }
echo "layers: Stamp=[$STAMP] Waves=[$WAVES]"

magick "$XCF[$STAMP]" "$T/stamp.png"
magick "$XCF[$WAVES]" "$T/waves.png"

# --- compose at the XCF's geometry, then scale to the post ----------------
magick -size 1004x1339 xc:none \
  "$T/stamp.png" -geometry +714+60  -composite \
  "$T/waves.png" -geometry +970+105 -composite \
  -resize 1080x1440! -strip PNG32:"$T/frame.png"

# --- swap in the current logo ---------------------------------------------
# Scale and placement are DERIVED from the logo's own alpha bounding box, not
# hardcoded. Different exports pad the disc differently inside the 256px canvas
# — app.css's logo.png is a 248px disc with 4px of margin, the gauge-running
# variants are a 214px disc with 21px — so a fixed "-resize 195x195" renders
# one correctly and the other far too small, leaving a rim of the stamp's
# original orange ring showing around it. Normalising on the DISC makes every
# variant land at the same visual size.
#
# TARGET_DISC covers the original ring in the XCF stamp, which measures 182x184
# centred on (885.5, 183.5) once the frame is at 1080x1440. 189 clears the
# larger axis by ~2.5px all round.
TARGET_DISC=189
RING_CX=885.5
RING_CY=183.5

read -r LW LH DW DH DX DY <<<"$(magick "$LOGO" -format "%w %h " info: ; \
  magick "$LOGO" -alpha extract -format "%@" info: | tr 'x+' '  ')"
[ -n "$DW" ] || { echo "could not read logo geometry" >&2; exit 1; }

read -r CANVAS OFFX OFFY <<<"$(awk -v lw="$LW" -v dw="$DW" -v dh="$DH" -v dx="$DX" -v dy="$DY" \
  -v t="$TARGET_DISC" -v cx="$RING_CX" -v cy="$RING_CY" 'BEGIN{
    d = (dw > dh ? dw : dh);        # the disc, on its larger axis
    s = t / d;                      # scale that makes the disc TARGET_DISC wide
    printf "%d %d %d", lw*s+0.5, cx-(dx+dw/2)*s+0.5, cy-(dy+dh/2)*s+0.5;
  }')"
echo "logo:  disc ${DW}x${DH} at +${DX}+${DY} -> canvas ${CANVAS}px at +${OFFX}+${OFFY}"

magick "$LOGO" -filter Lanczos -resize ${CANVAS}x${CANVAS} PNG32:"$T/logo-scaled.png"
magick "$T/frame.png" "$T/logo-scaled.png" -geometry +${OFFX}+${OFFY} -composite PNG32:"$T/frame-logo.png"

# --- weather just the stamp, then put it back -----------------------------
magick "$T/frame-logo.png" -crop 238x269+767+64 +repage PNG32:"$T/stamp-crop.png"
# Args: seed mottle grain wear fade — see weather.sh. These were dialled up from
# 0.09/0.055/66/2, which measured only 3.75% RMSE off the clean stamp and was
# invisible at the size the stamp actually appears. This is ~7%.
./weather.sh "$T/stamp-crop.png" "$T/stamp-worn.png" 7 0.20 0.10 42 6
magick "$T/frame-logo.png" "$T/stamp-worn.png" -geometry +767+64 -composite PNG32:"$T/frame-worn.png"

# --- quantise --------------------------------------------------------------
magick "$T/frame-worn.png" +dither -colors 256 PNG8:"$OUT"
echo "wrote $OUT ($(du -b "$OUT" | cut -f1) bytes)"
echo
echo "Now re-embed it in the template:  ./embed-frame.py"
