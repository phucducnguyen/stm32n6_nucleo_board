# CLAUDE.md — STM32N6 camera project cheat sheet

Architecture → `docs/architecture.md` · Current state → `docs/HANDOVER.md` · Backlog → `docs/TODO.md`.
This file = how to build/flash and what not to break.

## What this is

Zephyr RTOS camera firmware for the **NUCLEO-N657X0-Q** (STM32N657X0, Cortex-M55 @ 800 MHz + Neural-ART NPU) with the **B-CAMS-IMX** camera module (Sony IMX335, 5 MP, MIPI CSI-2). Long-term arc: camera capture → frame processing → edge-AI vision. NOT a nebula service — lives in `~/projects/`, nothing here touches `/srv/nebula`.

## Workspace layout (west T1, Zephyr is the manifest repo)

| Path | What | Git |
|---|---|---|
| `zephyr/` | Zephyr **v4.4.1, pinned**, shallow clone | own repo, do NOT commit here; patches go in `overlays/` or app dirs |
| `modules/` | only `cmsis`, `cmsis_6`, `hal_stm32` (narrowed) | west-managed |
| `.venv/` | west + **cmake + ninja as pip wheels** + all Zephyr py deps | gitignored |
| `overlays/` | our devicetree overlays (see RAM invariant below) | tracked |
| `apps/` | our applications | tracked |
| `build/` | build output (`build/capture` = video sample) | gitignored |

Host has **no system cmake/ninja/gperf/dtc** — everything lives in `.venv`. Toolchain: `~/zephyr-sdk-1.0.1` (arm-zephyr-eabi only).

## Build

Always with the venv on PATH (west sub-tools resolve `cmake` by name):

```sh
cd ~/projects/stm32n6
export PATH="$PWD/.venv/bin:$PATH"
west build -p -b nucleo_n657x0_q --shield st_b_cams_imx_mb1854 \
  zephyr/samples/drivers/video/capture -d build/capture -- \
  -DEXTRA_DTC_OVERLAY_FILE="$PWD/overlays/nucleo_n657x0_q_bigram.overlay" \
  -DCONFIG_VIDEO_FRAME_WIDTH=800 -DCONFIG_VIDEO_FRAME_HEIGHT=480 \
  -DCONFIG_VIDEO_PIXEL_FORMAT='"RGBP"' \
  -DCONFIG_VIDEO_BUFFER_POOL_HEAP_SIZE=1600000 -DCONFIG_MAIN_STACK_SIZE=2048
```

## Flash / run (STM32N6 has NO internal flash — read this)

The boot ROM only executes **signed** images, chainloaded from external octo-SPI flash (MX25UM51245G) or pushed over USB in serial-boot mode. Signing is automatic at build **iff** `STM32_SigningTool_CLI` (ships inside STM32CubeProgrammer ≥ 2.18) is on PATH.

| Mode | BOOT0 | BOOT1 | Use |
|---|---|---|---|
| Run from ext flash | 0 | 0 | normal operation |
| Dev boot (flash via ST-Link) | 0 | 1 | `west flash` target |
| Serial boot (USB DFU-like) | 1 | 0 | `nucleo_n657x0_q/stm32n657xx/sb` variant, RAM-only, re-load every power cycle |

Procedure: BOOT1=1 → `west flash` → BOOT1=0 → reset. Console: `/dev/ttyACM0` 115200 8N1 (ST-Link VCP → USART1).

## Invariants — do not break

- **`overlays/nucleo_n657x0_q_bigram.overlay` is load-bearing for anything using the camera.** Upstream Nucleo dts parks `zephyr,sram` on the 511 KB AXISRAM2; video buffers need the ~2 MB `axisram1` region (the DK does this upstream; the Nucleo doesn't — upstreamable fix). Without it: `region RAM overflowed` at link.
- **Zephyr stays pinned at v4.4.1.** No bare `west update` (it would also pull every module). Upgrading = deliberate task: bump tag, `west update --narrow -o=--depth=1 cmsis cmsis_6 hal_stm32`, rebuild, retest camera.
- **Don't fix "cmake not found" with sudo apt.** It's in `.venv/bin`; put it on PATH.
- **Video buffer pool must fit RAM:** width×height×2 bytes/frame ×2 buffers ≤ ~1.7 MB free. 800×480 RGB565 + 1.6 MB pool = 82% RAM. Full 5 MP (2592×1944) does NOT fit internal SRAM — that needs DCMIPP downscale/crop (it can) or external RAM (Nucleo has none).
- **Signing tool errors ≠ build errors.** `zephyr.bin` builds fine without CubeProgrammer; only flashing/booting needs the signed image.
- Serial port needs `dialout` group membership (no sudo workarounds in scripts).
- Boot pins: don't leave BOOT1=1 after flashing — the board will sit in dev boot doing nothing.

## Conventions (inherited from nebula where they apply)

- Edit in place; this repo is local-only (no remote — don't add one without being asked).
- No `Co-Authored-By` trailers in commits.
- No secrets here, and there should never be any (it's firmware; tokens don't belong in this tree).
- Record design choices in `docs/architecture.md` § Decisions as they happen.
- STOP and ask on unexpected hardware state (board not enumerating, flash verify fail, etc.).
