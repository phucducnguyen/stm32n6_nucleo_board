#!/usr/bin/env bash
# cam-describe.sh [prompt] — ON-DEMAND look through the board camera.
# Grabs one frame from /dev/video0 and asks a vision model on an Ollama
# server what it sees. Runs only when you run it; no background watcher.
#
# Required env (no defaults: pick the server and model on purpose):
#   OLLAMA_URL    e.g. http://<ollama-host>:11434
#   OLLAMA_MODEL  a vision model that server has pulled
# Optional: CAM_DESCRIBE_TIMEOUT (whole seconds, default 120: a deadline for the
# model call, not a per-read timeout), CAM_STREAM_PORT (default 8091, where
# cam-stream.sh serves /snapshot; start cam-stream.sh with the same variable,
# not a port argument, so both agree).
#
# Gets the frame from ustreamer's /snapshot endpoint if the stream is up
# (no interruption); falls back to grabbing /dev/video0 directly if not.
# Exit codes: 0 ok · 2 not configured · 3 server unavailable · 1 other error.
set -euo pipefail

if [[ -z "${OLLAMA_URL:-}" || -z "${OLLAMA_MODEL:-}" ]]; then
	echo "cam-describe: set OLLAMA_URL (e.g. http://<ollama-host>:11434) and OLLAMA_MODEL (a vision model on that server)" >&2
	exit 2
fi
TIMEOUT="${CAM_DESCRIBE_TIMEOUT:-120}"
SNAP_PORT="${CAM_STREAM_PORT:-8091}"
if ! [[ "$TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
	echo "cam-describe: CAM_DESCRIBE_TIMEOUT must be a whole number of seconds above 0 (got '$TIMEOUT')" >&2
	exit 2
fi
if ! [[ "$SNAP_PORT" =~ ^[1-9][0-9]*$ ]] || (( SNAP_PORT > 65535 )); then
	echo "cam-describe: CAM_STREAM_PORT must be a port number (got '$SNAP_PORT')" >&2
	exit 2
fi
PROMPT="${1:-Describe what you see in this image in 2-3 sentences. It comes from a camera with a green color cast and soft focus; ignore those artifacts.}"

# Fail fast and clearly before touching the camera.
if ! curl -sf -m 5 -o /dev/null "${OLLAMA_URL%/}/api/version"; then
	echo "cam-describe: Ollama unavailable at $OLLAMA_URL (no answer within 5 s)" >&2
	exit 3
fi

FRAME=$(mktemp --suffix=.jpg)
trap 'rm -f "$FRAME"' EXIT

# Loopback: never through an http_proxy from the environment.
if ! curl -sf -m 10 --noproxy '*' -o "$FRAME" "http://127.0.0.1:$SNAP_PORT/snapshot"; then
	# A camera that never delivers a frame would block ffmpeg forever.
	if ! timeout 15 ffmpeg -hide_banner -loglevel error -f v4l2 -video_size 640x480 \
		-i /dev/video0 -frames:v 1 -update 1 -y "$FRAME"; then
		echo "cam-describe: no frame from /dev/video0 within 15 s (and no stream on :$SNAP_PORT)" >&2
		exit 1
	fi
fi

# urlopen's timeout is per socket read, so a server that trickles bytes would never time out:
# `timeout` is the deadline (124 = it fired).
rc=0
PROMPT="$PROMPT" FRAME="$FRAME" TIMEOUT="$TIMEOUT" OLLAMA_URL="$OLLAMA_URL" OLLAMA_MODEL="$OLLAMA_MODEL" \
	timeout "$TIMEOUT" python3 - <<'PY' || rc=$?
import base64, json, os, socket, sys, urllib.error, urllib.request
url = os.environ["OLLAMA_URL"].rstrip("/") + "/api/generate"
img = base64.b64encode(open(os.environ["FRAME"], "rb").read()).decode()
payload = {"model": os.environ["OLLAMA_MODEL"], "stream": False,
           "prompt": os.environ["PROMPT"],
           "images": [img], "options": {"temperature": 0}}
req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                             headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=float(os.environ["TIMEOUT"])) as r:
        reply = json.load(r)
    text = reply.get("response") if isinstance(reply, dict) else None
    if not isinstance(text, str) or not text.strip():
        print("cam-describe: %s answered without a \"response\" (%s)"
              % (os.environ["OLLAMA_URL"], (reply.get("error") if isinstance(reply, dict) else None) or "no text"),
              file=sys.stderr)
        sys.exit(1)
    print(text)
except ValueError as e:
    print("cam-describe: %s answered with something that is not JSON: %s" % (os.environ["OLLAMA_URL"], e), file=sys.stderr)
    sys.exit(1)
except urllib.error.HTTPError as e:
    print("cam-describe: %s answered HTTP %d (model %s pulled there?)"
          % (os.environ["OLLAMA_URL"], e.code, os.environ["OLLAMA_MODEL"]), file=sys.stderr)
    sys.exit(1)
except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
    print("cam-describe: Ollama unavailable at %s: %s" % (os.environ["OLLAMA_URL"], e), file=sys.stderr)
    sys.exit(3)
PY
if (( rc == 124 )); then
	echo "cam-describe: no full answer from $OLLAMA_URL within ${TIMEOUT} s" >&2
	exit 3
fi
exit "$rc"
