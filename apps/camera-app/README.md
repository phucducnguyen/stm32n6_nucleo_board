# camera-app — STM32N6 + IMX335 as a USB (UVC) webcam

Our application (M2), forked 2026-06-12 from `zephyr/samples/subsys/usb/uvc`
(v4.4.1) so we own the code we build on. The board enumerates as a standard
USB webcam on CN8; the host needs no drivers (`/dev/video0` on Linux).

## What differs from the upstream sample

- **Self-contained build** — shield (`st_b_cams_imx_mb1854`) set in
  `CMakeLists.txt`; vidpool memory overlay + `uvc` node merged into
  `app.overlay`; pool sizes, sync-logging, stack size all in `prj.conf`.
  No `-D` flags needed.
- **USB device identity is ours** — `src/app_usbd.c` + `APP_USBD_*` Kconfig
  (the in-tree `SAMPLE_USBD_*` helpers are sample-scoped by upstream policy).
- **Video-encoder paths removed** — no `zephyr,videoenc` hardware here.
- **Startup analogue gain** — `CONFIG_APP_CAMERA_ANALOGUE_GAIN_MDB`
  (default 30 dB; the IMX335 powers up at 0 dB = near-black indoors).

## Build + run (dev loop)

```sh
cd ~/projects/stm32n6
export PATH="$PWD/.venv/bin:$PATH"
west build -p -b 'nucleo_n657x0_q//sb' apps/camera-app -d build/camera-app
scripts/swd-run.sh build/camera-app      # BOOT1 parked in dev boot
scripts/cam-stream.sh                    # ustreamer on :8090
```

Plain `nucleo_n657x0_q` (no `//sb`) for a signed flash-boot image, shipped via
`scripts/preflight-flash.sh`.

## Frame-stats hook (M3, optional)

`src/frame_stats.{c,h}`, gated behind `CONFIG_APP_FRAME_STATS` (default **off** —
off builds are byte-for-byte the verified webcam). When on, the frame loop peeks
each camera buffer read-only on its way to the host and logs ~1 Hz: luma
mean/min/max, an 8×8-block inter-frame motion score, fps, and per-frame
processing time.

```sh
west build -p -b 'nucleo_n657x0_q//sb' apps/camera-app -d build/camera-app-stats \
  -- -DCONFIG_APP_FRAME_STATS=y
scripts/swd-run.sh build/camera-app-stats   # then stream to drive the loop
```

**Measured on hardware (2026-06-14):** a full per-pixel pass (stride 1) costs
~336 ms/frame and drops UVC 20→2 fps — the frame buffer is non-cacheable
`AXISRAM1` that the CSI DMA is actively writing, so each byte read is ~1 µs
under DMA contention and the cost scales linearly with bytes touched.
`CONFIG_APP_FRAME_STATS_STRIDE` (default 8) samples 64× fewer pixels and keeps
the stream at 20 fps (~4.8 ms/frame). Lesson: don't brute-scan a DMA frame
buffer in uncached memory — sample it. Set stride 1 only to reproduce the cost.

## Known constraints

- `CONFIG_LOG_MODE_IMMEDIATE=y` in `prj.conf` is **load-bearing** until the
  imx335 boot-timing race is fixed in the driver (see root `CLAUDE.md`
  §Debugging).
- Image must fit the 511 KB BootROM window; the 1.25 MB frame pool lives in
  the runtime-only `AXISRAM1` region (`app.overlay`).
- Green cast (no white-balance config in the DCMIPP pipeline yet) and manual
  lens focus are open items — `docs/TODO.md`.
