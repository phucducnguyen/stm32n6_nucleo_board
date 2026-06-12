#!/usr/bin/env bash
# cam-stream.sh — serve the board webcam (/dev/video0) in the browser via
# ustreamer (multi-client, no respawn gaps, built-in viewer page).
#
#   open  http://<atlas>:8090/         viewer page
#         http://<atlas>:8090/stream   raw MJPEG (for <img>/VLC)
#         http://<atlas>:8090/snapshot single JPEG (used by cam-describe.sh)
#
# History: the first version looped `ffmpeg -listen 1` — single client,
# 1-2 s dead gap between clients, and browsers choked on its MJPEG framing
# (Content-Type pitfalls). ustreamer replaced it 2026-06-11.
set -euo pipefail

PORT="${1:-8090}"

exec ustreamer --device=/dev/video0 --host=0.0.0.0 --port="$PORT" \
	--resolution=640x480
