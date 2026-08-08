#!/usr/bin/env bash
# Smoke test for the SupoClip ASR service.
# Usage:
#   bash asr/scripts/smoke.sh [audio-file]
# Without an audio file, a 440 Hz tone is synthesized with ffmpeg when
# available; otherwise only the health check runs and the script exits 0.
set -euo pipefail

ASR_URL="${ASR_URL:-http://127.0.0.1:8765}"

echo "== SupoClip ASR smoke test =="
echo "Target: $ASR_URL"

# --- Health check ---
health_json="$(curl -fsS --max-time 15 "$ASR_URL/health")" || {
  echo "FAIL: GET $ASR_URL/health failed (service unreachable or non-2xx response)"
  exit 1
}
echo "$health_json" | python3 -c '
import json, sys
h = json.load(sys.stdin)
if h.get("status") != "ok" or h.get("model_ready") is not True:
    print("FAIL: health payload =", h, file=sys.stderr)
    sys.exit(1)
'
echo "PASS: health ok, model_ready=true"

# --- Transcription check ---
audio="${1:-}"
if [ -z "$audio" ]; then
  if command -v ffmpeg >/dev/null 2>&1; then
    audio="/tmp/asr_smoke.wav"
    ffmpeg -v error -f lavfi -i "sine=frequency=440:duration=2" -ar 16000 -ac 1 -y "$audio"
    echo "Synthesized test tone: $audio"
  else
    echo "SKIP: no audio file given and ffmpeg not available — health-only smoke"
    exit 0
  fi
fi

[ -f "$audio" ] || {
  echo "FAIL: audio file not found: $audio"
  exit 1
}

transcript_json="$(curl -fsS --max-time 600 -F "audio=@$audio" "$ASR_URL/v1/transcribe")" || {
  echo "FAIL: POST $ASR_URL/v1/transcribe failed"
  exit 1
}
echo "$transcript_json" | python3 -c '
import json, sys
t = json.load(sys.stdin)
words = t.get("words") or []
if not words or t.get("timestamps") is not True:
    print("FAIL: transcription payload =", t, file=sys.stderr)
    sys.exit(1)
print("PASS: transcription ok, %d words with timestamps" % len(words))
'

echo "ALL CHECKS PASSED"
