/*
 * frame_stats — M3 "processing on target" hook for camera-app.
 *
 * Read-only, in-transit per-frame analysis of camera buffers on their way to
 * the UVC host: luma (mean/min/max), inter-frame motion (8x8 coarse grid diff),
 * fps and per-frame processing time. Never modifies pixel data. The whole hook
 * is gated behind CONFIG_APP_FRAME_STATS (default n); when off it compiles out
 * and the firmware is byte-for-byte the verified webcam.
 *
 * Copyright (c) 2026 pdnguyen
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef APP_FRAME_STATS_H_
#define APP_FRAME_STATS_H_

#include <stdint.h>

/*
 * Configure the analyzer with the negotiated format and reset all accumulators.
 * Call once after the host has selected the video format (width/height/pixfmt
 * valid). pixfmt is a Zephyr VIDEO_PIX_FMT_* fourcc.
 */
void frame_stats_init(uint32_t width, uint32_t height, uint32_t pixfmt);

/*
 * Analyze one frame's pixel data (read-only). 'buf' points at the start of the
 * pixel data and 'bytesused' is the count of valid bytes. Accumulates stats and
 * emits a periodic (~1 Hz) LOG_INF summary line.
 */
void frame_stats_process(const uint8_t *buf, uint32_t bytesused);

#endif /* APP_FRAME_STATS_H_ */
