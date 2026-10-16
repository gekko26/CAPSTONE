#!/usr/bin/env bash
# gen_audio.sh — render the DFPlayer voice clips with a LOCAL offline TTS.
#
# Downloads NOTHING. Writes only into ./build/audio/, which is gitignored.
# If no TTS engine is installed it prints the eight lines to record by hand and exits 0,
# so it is safe to run anywhere.
#
# Usage:
#   tools/gen_audio.sh --dry-run      print the plan, write nothing
#   tools/gen_audio.sh                render into ./build/audio/
#   tools/gen_audio.sh --copy-to-sd   also copy onto a mounted SD card (SD_PATH=/path)
#   tools/gen_audio.sh --engine espeak
#
# See docs/audio/MANIFEST.md for the clip table and the SD layout.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$REPO_ROOT/build/audio"
SD_LAYOUT_DIR="01"
DRY_RUN=0
COPY_TO_SD=0
ENGINE=""
WANT_SR=22050          # 22.05 kHz mono: small, mono, decodable by the DFPlayer
SD_PATH="${SD_PATH:-}"

# clip-number|spoken-text
CLIPS=(
  "001|Person detected."
  "002|Please stop at the marked area."
  "003|Sampling. Please stay still."
  "004|Analyzing."
  "005|Pass. You may proceed."
  "006|Warning. Please wait for assistance."
  "007|Fail. Access denied."
  "008|System ready."
)

usage() { sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)   DRY_RUN=1; shift ;;
    --copy-to-sd) COPY_TO_SD=1; shift ;;
    --engine)    ENGINE="${2:-}"; shift 2 ;;
    --out)       OUT_DIR="${2:-}"; shift 2 ;;
    -h|--help)   usage ;;
    *) echo "unknown option: $1" >&2; usage ;;
  esac
done

# ── find a local offline TTS ────────────────────────────────────────────────────
find_engine() {
  for c in $ENGINE espeak-ng espeak pico2wave flite say; do
    [ -z "$c" ] && continue
    if command -v "$c" >/dev/null 2>&1; then echo "$c"; return 0; fi
  done
  return 1
}

print_plan() {
  echo "clip  text"
  echo "----  ------------------------------------------------"
  for entry in "${CLIPS[@]}"; do
    num="${entry%%|*}"; text="${entry#*|}"
    printf "%s   %s\n" "$num" "$text"
  done
  echo
  echo "output dir : $OUT_DIR/$SD_LAYOUT_DIR"
  echo "sample rate: $WANT_SR Hz, mono, MP3"
  echo "card layout: SD:/$SD_LAYOUT_DIR/001.mp3 ... 008.mp3  (FAT32)"
}

if ! ENGINE_FOUND="$(find_engine)"; then
  echo "No local offline TTS found (tried: espeak-ng, espeak, pico2wave, flite, say)."
  echo "Nothing was downloaded and nothing was written."
  echo
  echo "Record these by hand — exact wording, each under 2 seconds:"
  echo
  print_plan
  echo
  echo "Then copy them to a FAT32 card as /$(echo "$SD_LAYOUT_DIR")/NNN.mp3"
  exit 0
fi

echo "Using local TTS: $ENGINE_FOUND"
echo
print_plan
echo

if [ "$DRY_RUN" = "1" ]; then
  echo "--dry-run: wrote nothing."
  exit 0
fi

mkdir -p "$OUT_DIR/$SD_LAYOUT_DIR"

render() {           # render <clip-number> <text> -> <out.wav>
  local num="$1" text="$2" wav="$3"
  case "$ENGINE_FOUND" in
    espeak-ng)  espeak-ng -v en-us -s 165 -p 45 -w "$wav" "$text" ;;
    espeak)     espeak    -v en-us -s 165 -p 45 -w "$wav" "$text" ;;
    pico2wave)  pico2wave -l en_US -w "$wav" "$text" ;;
    flite)      flite -t "$text" -o "$wav" ;;
    say)        say -o "$wav" "$text" ;;
    *)          return 1 ;;
  esac
}

to_mp3() {           # to_mp3 <in.wav> <out.mp3>
  if command -v ffmpeg >/dev/null 2>&1; then
    ffmpeg -loglevel error -y -i "$1" -ar "$WANT_SR" -ac 1 -codec:a libmp3lame -b:a 96k "$2"
  elif command -v lame >/dev/null 2>&1; then
    lame --quiet -m m -b 96 -s "$WANT_SR" "$1" "$2"
  else
    echo "  (no ffmpeg or lame — leaving WAV; the DFPlayer can often play WAV too)" >&2
    cp "$1" "${2%.mp3}.wav"
  fi
}

FAILED=0
for entry in "${CLIPS[@]}"; do
  num="${entry%%|*}"; text="${entry#*|}"
  wav="$OUT_DIR/$SD_LAYOUT_DIR/$num.wav"
  mp3="$OUT_DIR/$SD_LAYOUT_DIR/$num.mp3"
  if render "$num" "$text" "$wav"; then
    to_mp3 "$wav" "$mp3"
    rm -f "$wav"
    printf "  %s.mp3  ok\n" "$num"
  else
    printf "  %s.mp3  FAILED\n" "$num"
    FAILED=1
  fi
done

echo
echo "Wrote clips to $OUT_DIR/$SD_LAYOUT_DIR (build/ is gitignored — MP3s must not be committed)."

if [ "$COPY_TO_SD" = "1" ]; then
  if [ -z "$SD_PATH" ] || [ ! -d "$SD_PATH" ]; then
    echo "COPY_TO_SD set but SD_PATH is unset or not a directory — skipping copy." >&2
  else
    mkdir -p "$SD_PATH/$SD_LAYOUT_DIR"
    cp "$OUT_DIR/$SD_LAYOUT_DIR/"*.mp3 "$SD_PATH/$SD_LAYOUT_DIR/" 2>/dev/null \
      && echo "Copied to $SD_PATH/$SD_LAYOUT_DIR" \
      || echo "Copy failed — is the card mounted and FAT32?" >&2
  fi
fi

exit "$FAILED"