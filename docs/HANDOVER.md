# HANDOVER

## CURRENT (2026-06-10)

**State: toolchain + camera build verified end-to-end on atlas; flash blocked on two user actions (sudo script + CubeProgrammer download).**

Done this session:
- Workspace created at `~/projects/stm32n6` (west T1, Zephyr v4.4.1 pinned, modules narrowed to cmsis/cmsis_6/hal_stm32, all host tooling in `.venv` incl. cmake+ninja pip wheels, SDK at `~/zephyr-sdk-1.0.1`).
- **Video capture sample BUILDS clean** for `nucleo_n657x0_q` + `--shield st_b_cams_imx_mb1854`: 800×480 RGB565, 1.6 MB double-buffer pool, 82% of 2 MB RAM. Exact command in root `CLAUDE.md`.
- Root-caused and fixed RAM overflow: upstream Nucleo dts uses 511 KB `axisram2` as system RAM; `overlays/nucleo_n657x0_q_bigram.overlay` switches to the ~2 MB `axisram1` (same as the DK's fsbl variant). **Load-bearing for all camera builds.** Upstream-PR candidate.
- Board enumerates on atlas: STLINK-V3 `0483:3754`, console `/dev/ttyACM0` (first cable/port was dead — re-plug fixed it).
- Docs written: `CLAUDE.md`, `docs/architecture.md`, this file, `docs/TODO.md`; `scripts/host-setup.sh` prepared.

Blocked on user (in order):
1. `! sudo bash ~/projects/stm32n6/scripts/host-setup.sh` — dialout group, gperf/dtc/ccache/picocom, ST-Link udev rule. Then re-plug the board and re-login (or use `sg dialout`).
2. Download **STM32CubeProgrammer ≥ 2.18 (Linux)** from st.com in a browser (login + license wall; headless fetch is blocked — verified). Hand me the zip path; I'll install it user-space to `~/STMicroelectronics` and wire PATH. It bundles `STM32_SigningTool_CLI`, mandatory for the N6 boot ROM's signed-image requirement.

Immediately after unblock (next session start here):
- Install CubeProgrammer from the user's zip → re-run the build (signing now happens post-build) → BOOT1=1 → `west flash -d build/capture` → BOOT1=0 → reset → expect IMX335 probe + frame stats on `/dev/ttyACM0` 115200 (`.venv/bin/python -m serial.tools.miniterm /dev/ttyACM0 115200` or picocom).
- First-light pass criteria: sensor detected on csi_i2c@0x1a, `video_dequeue` returning buffers at a steady rate, no DCMIPP overrun errors.

## DEFERRED

- Upstream PR to zephyr: `zephyr,sram = &axisram1` for nucleo_n657x0_q (mirror of DK commit). Do after first flash confirms the overlay on hardware.
- PYNQ-Z2 FPGA track: deliberately parked until the N6 camera milestone lands (user agreed 2026-06-10).
- NPU (Neural-ART) exploration: separate STM32Cube.AI/X-CUBE-N6 track — Zephyr has no NPU driver; don't look for one.

## HISTORY

(none yet — project started 2026-06-10)
