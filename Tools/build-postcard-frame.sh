#!/bin/bash
#
# Builds Tools/postcard-frame.png — the stamp-and-waves overlay that
# postcard-template.html embeds, matching the postcards already on
# rangerly.net (assets/images/postcard-*.webp).
#
# Measured off postcard-mountains.webp, which is the format those all share:
#
#   canvas   800x814, and one 1024x1044 (the footer card) at the same ratio
#   mat      ~31px of #FEEEDD all round — faq.css names that colour itself
#   photo    fills the rest; no drop shadow, just 1-2px of antialiasing
#   stamp    ~205px wide at +540+51 on the 800 canvas, overlapping the photo's
#            top-right, with the postmark waves running off the right edge
#
# Built at 1024 rather than 800 so the template can scale it DOWN for the
# smaller size; upscaling an 800px stamp to 1024 would soften it. Everything
# below is the 800px measurement times 1024/800.
#
# The stamp art itself, including the current logo and the weathering, comes
# from build-stamp.sh — shared with the Instagram frame so the two can't drift.
set -euo pipefail
cd "$(dirname "$0")"

XCF="../Instagram/Posts/_template.xcf"
OUT="postcard-frame.png"
CW=1024; CH=1044                 # canvas
SW=262                           # stamp width  (205 * 1024/800)
SX=691; SY=65                    # stamp offset (540,51 * 1024/800)

T=$(mktemp -d); trap 'rm -rf "$T"' EXIT

./build-stamp.sh "$SW" "$T/stamp.png" "$@"
SH_=$(magick identify -format %h "$T/stamp.png")

# The waves are placed against the POSTCARD, not inherited from the Instagram
# layout. In the XCF they sit +256,+45 from the stamp at source scale, which is
# tuned for that 1080x1440 frame; carried over here it put them 9px too far
# right and 31px too low, so only the left tips of each curve cleared the photo
# and they read as dashes rather than a cancel.
#
# These are fitted to postcard-mountains.webp instead. The check is the strip
# of mat to the right of the photo, where nothing but a wave can be dark: the
# reference covers x769..799 (the full 31px) from y63, and WAVE_* below
# reproduce that. Scale is the stamp's, so the two stay one postmark.
WAVE_DX=293                      # from the stamp's left edge, at 1024 scale
WAVE_DY=14
WAVE_SCALE=1.06                  # slightly larger than the stamp's own scale

SCALE=$(awk -v sw="$SW" -v k="$WAVE_SCALE" 'BEGIN{printf "%.6f", (sw/220)*k}')
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
read -r WW WH WX WY <<<"$(awk -v s="$SCALE" -v sx="$SX" -v sy="$SY" -v dx="$WAVE_DX" -v dy="$WAVE_DY" 'BEGIN{
  printf "%d %d %d %d", 34*s+0.5, 147*s+0.5, sx+dx, sy+dy }')"

magick "$XCF[$WI]" +repage -filter Lanczos -resize ${WW}x${WH}! PNG32:"$T/waves.png"

# Transparent middle: the mat and the photo are the template's, not the
# frame's, exactly as with the Instagram overlay.
magick -size ${CW}x${CH} xc:none \
  "$T/stamp.png" -geometry +${SX}+${SY} -composite \
  "$T/waves.png" -geometry +${WX}+${WY} -composite \
  -strip PNG32:"$T/frame.png"

magick "$T/frame.png" +dither -colors 256 PNG8:"$OUT"
echo "waves: ${WW}x${WH} at +${WX}+${WY}"
echo "wrote $OUT ($(du -b "$OUT" | cut -f1) bytes, ${CW}x${CH})"
