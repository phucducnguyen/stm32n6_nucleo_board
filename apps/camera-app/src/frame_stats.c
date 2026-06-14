/*
 * frame_stats — M3 "processing on target" hook for camera-app.
 *
 * See frame_stats.h. Read-only luma + motion-diff analysis with periodic logging.
 *
 * Copyright (c) 2026 pdnguyen
 * SPDX-License-Identifier: Apache-2.0
 */

#include "frame_stats.h"

#include <stdint.h>

#include <zephyr/kernel.h>
#include <zephyr/drivers/video.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(frame_stats, LOG_LEVEL_INF);

/* Coarse motion signature: GRIDxGRID blocks of average luma. */
#define GRID 8U
#define NUM_BLOCKS (GRID * GRID)

/* Negotiated format, stored at init. */
static uint32_t fs_width;
static uint32_t fs_height;
static uint32_t fs_pixfmt;

/* Previous frame's coarse signature (average luma per block, 0..255). */
static uint8_t prev_grid[NUM_BLOCKS];
static bool have_prev;

/* Reporting window accumulators (~1 s windows). */
static uint32_t win_frames;
static int64_t win_start_ms;
static uint64_t win_proc_us; /* summed per-frame processing time */

/*
 * Read luma for a given pixel column for the configured pixel format.
 *
 * YUYV (a.k.a. YUY2) packs "Y0 U0 Y1 V0 ..." so luma is at every even byte
 * offset: pixel x -> byte offset 2*x. For any OTHER pixel format we don't know
 * the layout, so we fall back to treating every byte as an intensity sample
 * (i.e. "pixel x" -> byte offset x). This fallback is approximate (it mixes
 * chroma/packed bytes into the "luma" stats) but keeps the hook format-agnostic
 * and never reads out of bounds.
 */
static inline uint32_t luma_byte_offset(uint32_t x)
{
	return (fs_pixfmt == VIDEO_PIX_FMT_YUYV) ? (x * 2U) : x;
}

void frame_stats_init(uint32_t width, uint32_t height, uint32_t pixfmt)
{
	fs_width = width;
	fs_height = height;
	fs_pixfmt = pixfmt;

	have_prev = false;
	win_frames = 0;
	win_proc_us = 0;
	win_start_ms = k_uptime_get();

	for (uint32_t i = 0; i < NUM_BLOCKS; i++) {
		prev_grid[i] = 0;
	}

	LOG_INF("frame_stats init: %ux%u fourcc %s (luma %s, stride %d)", width, height,
		VIDEO_FOURCC_TO_STR(pixfmt),
		(pixfmt == VIDEO_PIX_FMT_YUYV) ? "even-byte" : "every-byte fallback",
		CONFIG_APP_FRAME_STATS_STRIDE);
}

void frame_stats_process(const uint8_t *buf, uint32_t bytesused)
{
	uint32_t cycles_start = k_cycle_get_32();

	if (buf == NULL || bytesused == 0 || fs_width == 0 || fs_height == 0) {
		return;
	}

	/*
	 * Per-block accumulators for the coarse signature, plus whole-frame
	 * luma min/max/sum. We sample one pixel every STRIDE in x and y: the
	 * frame lives in non-cacheable AXISRAM that the CSI DMA is actively
	 * writing, so a full per-pixel pass (stride 1) contends with that DMA
	 * and measured ~336 ms/frame on hardware (UVC dropped 20->2 fps).
	 * Stride 8 reads 64x fewer bytes; the coarse luma/motion signal is
	 * unaffected at desk scale.
	 */
	const uint32_t stride = (CONFIG_APP_FRAME_STATS_STRIDE < 1)
				? 1U : (uint32_t)CONFIG_APP_FRAME_STATS_STRIDE;
	uint64_t block_sum[NUM_BLOCKS] = {0};
	uint32_t block_cnt[NUM_BLOCKS] = {0};
	uint64_t luma_sum = 0;
	uint32_t luma_cnt = 0;
	uint8_t luma_min = 255;
	uint8_t luma_max = 0;

	for (uint32_t y = 0; y < fs_height; y += stride) {
		uint32_t row_base = y * fs_width;
		uint32_t by = (y * GRID) / fs_height; /* 0..GRID-1 */

		for (uint32_t x = 0; x < fs_width; x += stride) {
			uint32_t off = luma_byte_offset(row_base + x);

			if (off >= bytesused) {
				/* Truncated/short frame — stop safely. */
				goto done_scan;
			}

			uint8_t l = buf[off];
			uint32_t bx = (x * GRID) / fs_width; /* 0..GRID-1 */
			uint32_t b = by * GRID + bx;

			block_sum[b] += l;
			block_cnt[b]++;

			luma_sum += l;
			luma_cnt++;
			if (l < luma_min) {
				luma_min = l;
			}
			if (l > luma_max) {
				luma_max = l;
			}
		}
	}

done_scan:
	if (luma_cnt == 0) {
		return;
	}

	/* Build the coarse signature and compute motion vs the previous frame. */
	uint8_t grid[NUM_BLOCKS];
	uint32_t motion = 0;

	for (uint32_t b = 0; b < NUM_BLOCKS; b++) {
		grid[b] = (block_cnt[b] > 0) ? (uint8_t)(block_sum[b] / block_cnt[b]) : 0;

		if (have_prev) {
			int diff = (int)grid[b] - (int)prev_grid[b];

			motion += (diff < 0) ? (uint32_t)(-diff) : (uint32_t)diff;
		}
	}

	bool first_frame = !have_prev;

	for (uint32_t b = 0; b < NUM_BLOCKS; b++) {
		prev_grid[b] = grid[b];
	}
	have_prev = true;

	/* Accumulate this window. */
	uint8_t luma_mean = (uint8_t)(luma_sum / luma_cnt);
	uint32_t cycles_end = k_cycle_get_32();

	win_proc_us += k_cyc_to_us_floor32(cycles_end - cycles_start);
	win_frames++;

	/* Emit one summary line per ~1 s window. */
	int64_t now_ms = k_uptime_get();
	int64_t elapsed_ms = now_ms - win_start_ms;

	if (elapsed_ms >= 1000) {
		uint32_t fps = (uint32_t)((win_frames * 1000U) / (uint32_t)elapsed_ms);
		uint32_t avg_proc_us = (uint32_t)(win_proc_us / win_frames);

		LOG_INF("fps=%u luma mean=%u min=%u max=%u motion=%u%s proc_avg=%u us (n=%u)",
			fps, luma_mean, luma_min, luma_max, motion,
			first_frame ? " (first)" : "", avg_proc_us, luma_cnt);

		win_frames = 0;
		win_proc_us = 0;
		win_start_ms = now_ms;
	}
}
