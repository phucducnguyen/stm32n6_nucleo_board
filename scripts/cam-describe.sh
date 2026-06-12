#!/usr/bin/env bash
# cam-describe.sh [prompt] — ON-DEMAND AI look through the board camera.
# Grabs one frame from /dev/video0 and asks qwen3-vl on hotchocolate what it
# sees. GPU is used ONLY while this runs; there is no background watcher.
#
# Gets the frame from ustreamer's /snapshot endpoint if the stream is up
# (no interruption); falls back to grabbing /dev/video0 directly if not.
set -euo pipefail

PROMPT="${1:-Describe what you see in this image in 2-3 sentences. It comes from a camera with a green color cast and soft focus; ignore those artifacts.}"
FRAME=/tmp/cam-describe-frame.jpg

if ! curl -sf -m 10 -o "$FRAME" http://127.0.0.1:8090/snapshot; then
	ffmpeg -hide_banner -loglevel error -f v4l2 -video_size 640x480 \
		-i /dev/video0 -frames:v 1 -update 1 -y "$FRAME"
fi

PROMPT="$PROMPT" FRAME="$FRAME" python3 - <<'EOF'
import base64, json, os, urllib.request
img = base64.b64encode(open(os.environ["FRAME"], "rb").read()).decode()
payload = {"model": "qwen3-vl:30b-a3b-instruct", "stream": False,
           "prompt": os.environ["PROMPT"],
           "images": [img], "options": {"temperature": 0}}
req = urllib.request.Request("http://10.0.0.143:11434/api/generate",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
print(json.load(urllib.request.urlopen(req, timeout=180)).get("response"))
EOF
