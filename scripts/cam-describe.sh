#!/usr/bin/env bash
# cam-describe.sh [prompt] — ON-DEMAND AI look through the board camera.
# Grabs one frame from /dev/video0 and asks a vision model on your LAN Ollama
# host what it sees. GPU is used ONLY while this runs; no background watcher.
#
# Set OLLAMA_URL to your LAN Ollama endpoint (default: localhost); optionally
# override OLLAMA_MODEL.
#
# Gets the frame from ustreamer's /snapshot endpoint if the stream is up
# (no interruption); falls back to grabbing /dev/video0 directly if not.
set -euo pipefail

OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3-vl:30b-a3b-instruct}"
PROMPT="${1:-Describe what you see in this image in 2-3 sentences. It comes from a camera with a green color cast and soft focus; ignore those artifacts.}"
FRAME=/tmp/cam-describe-frame.jpg

if ! curl -sf -m 10 -o "$FRAME" http://127.0.0.1:8090/snapshot; then
	ffmpeg -hide_banner -loglevel error -f v4l2 -video_size 640x480 \
		-i /dev/video0 -frames:v 1 -update 1 -y "$FRAME"
fi

PROMPT="$PROMPT" FRAME="$FRAME" OLLAMA_URL="$OLLAMA_URL" OLLAMA_MODEL="$OLLAMA_MODEL" python3 - <<'EOF'
import base64, json, os, urllib.request
img = base64.b64encode(open(os.environ["FRAME"], "rb").read()).decode()
payload = {"model": os.environ["OLLAMA_MODEL"], "stream": False,
           "prompt": os.environ["PROMPT"],
           "images": [img], "options": {"temperature": 0}}
req = urllib.request.Request(os.environ["OLLAMA_URL"].rstrip("/") + "/api/generate",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
print(json.load(urllib.request.urlopen(req, timeout=180)).get("response"))
EOF
