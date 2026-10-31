#!/usr/bin/env bash
# cam-stream.sh — DEBUG ONLY: serve the board webcam (/dev/video0) via
# ustreamer (multi-client, built-in viewer page). Off unless you run it.
#
#   open  http://127.0.0.1:8091/         viewer page
#         http://127.0.0.1:8091/stream   raw MJPEG (for <img>/VLC)
#         http://127.0.0.1:8091/snapshot single JPEG (used by cam-describe.sh)
#
# Binds loopback by default: the product invariant is that the camera image
# never leaves the device, and this stream is debug plumbing, not product.
# Opt in to a wider bind explicitly, e.g. CAM_STREAM_HOST=<host-ip>.
# Port default 8091 (8090 is taken on the dev host); override with
# CAM_STREAM_PORT or the first argument.
#
# History: the first version looped `ffmpeg -listen 1` — single client,
# 1-2 s dead gap between clients, and browsers choked on its MJPEG framing
# (Content-Type pitfalls). ustreamer replaced it 2026-06-11.
set -euo pipefail

HOST="${CAM_STREAM_HOST:-127.0.0.1}"
PORT="${1:-${CAM_STREAM_PORT:-8091}}"

if [[ "$HOST" != "127.0.0.1" && "$HOST" != "localhost" ]]; then
	echo "cam-stream: WARNING binding $HOST:$PORT: the camera image is reachable beyond this machine" >&2
fi

exec ustreamer --device=/dev/video0 --host="$HOST" --port="$PORT" \
	--resolution=640x480
