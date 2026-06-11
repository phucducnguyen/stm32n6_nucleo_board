# Architecture — STM32N6 Zephyr camera

System-level view. Operational commands → root `CLAUDE.md`. State → `HANDOVER.md`.

## Hardware

```
                 ┌──────────────────────────────────────────────┐
                 │ NUCLEO-N657X0-Q                              │
 B-CAMS-IMX      │  ┌─────────────────────────────────────────┐ │
 ┌───────────┐   │  │ STM32N657X0H3Q                          │ │
 │ IMX335    │ 22│  │  Cortex-M55 @ 800 MHz (Helium MVE)      │ │
 │ 5MP RGB   ├───┼──┤  CSI-2 D-PHY ──► DCMIPP (3 pipes)       │ │
 │ CSI-2 2-ln│FFC│  │  Neural-ART NPU (~600 GOPS, no Zephyr   │ │
 └───────────┘   │  │  driver — ST CubeAI only)               │ │
                 │  │  4.2 MB SRAM total / NO internal flash  │ │
                 │  └──┬────────────┬───────────────┬─────────┘ │
                 │     │ XSPI       │ USART1        │ RMII      │
                 │  MX25UM51245G    │               │           │
                 │  64 MB octo-SPI  │           Ethernet RJ45   │
                 │  flash (boot)    │           (unused so far) │
                 │                  │                           │
                 │  STLINK-V3EC ────┴── USB-C ──► atlas         │
                 │  (debug + VCP console + power)               │
                 └──────────────────────────────────────────────┘
```

- Camera connects to the **22-pin FFC CSI connector**; contacts face the board per silkscreen. Sensor control plane is I2C (addr `0x1a`) + 2 GPIOs (reset PO5, power/enable PA0 via gpio-hogs), data plane is 2-lane MIPI CSI-2 D-PHY.
- ST-Link enumerates on atlas as `0483:3754`, console `/dev/ttyACM0` (115200 8N1).
- No external RAM on the Nucleo (the N6570-DK has 32 MB PSRAM — that's why DK examples can do big frames and we can't).

## Memory map (what the firmware actually gets)

STM32N6 boots its application into RAM (no XIP from internal flash — there is none).

| Region | Base | Size | Use |
|---|---|---|---|
| FLEXRAM + AXISRAM1 + AXISRAM2 (`axisram1` node) | 0x34000000 | ~2 MB | **system RAM via our overlay** — code, data, video buffer pool |
| AXISRAM2 alone (`axisram2` node) | +0x180400 | 511 KB | upstream Nucleo default — too small for video; superseded by overlay |
| AXISRAM3–6 | (NPU/ISP domain) | ~2.2 MB | untouched; future NPU/ISP work |
| MX25UM51245G ext flash | XSPI | 64 MB | signed boot image storage |

Budget rule of thumb: ~370 KB code+data for the capture sample, rest is buffer pool. One RGB565 frame = W×H×2 bytes; pool holds 2 frames (double buffering).

## Boot chain (N6-specific, unlike classic STM32)

```
BootROM (mask ROM, checks signature)
  └─► reads signed image from ext octo-SPI flash (BOOT0=0,BOOT1=0)
      or accepts it over USB serial-boot (BOOT0=1)
        └─► our Zephyr image, copied to AXISRAM, runs as "FSBL" in secure mode
```

- Build emits `zephyr.bin`; `STM32_SigningTool_CLI` wraps it into the signed container the BootROM requires. West does this automatically post-build when the tool is on PATH.
- Two board targets: `nucleo_n657x0_q` (sign → ext flash → persistent) and `…/sb` (serial boot: push to RAM over USB each power-up — faster dev loop, nothing persists).

## Software stack (capture path)

```
app (samples/drivers/video/capture → later apps/<ours>)
  │  video_buffer_alloc/enqueue/dequeue          (zephyr video API)
  ▼
DCMIPP driver  drivers/video/video_stm32_dcmipp.c   ── pixel pipeline, DMA to RAM
IMX335 driver  drivers/video/imx335.c               ── sensor init/ctrl over csi_i2c
  ▲
devicetree wiring (this is where board/camera meet):
  shield st_b_cams_imx_mb1854  ── declares imx335 node on csi_i2c, CSI-2 2-lane link,
  │                               chosen zephyr,camera = &csi_capture_port
  board nucleo_n657x0_q        ── csi_connector gpio map, csi pins, clocks (IC18 = 27 MHz CSIPHY)
  our overlay                  ── zephyr,sram = &axisram1 (RAM fix, see CLAUDE.md invariants)
```

Build composition = board dts + shield overlay + our `EXTRA_DTC_OVERLAY_FILE` + Kconfig fragments on the command line. No Zephyr-tree patches; everything we own sits outside `zephyr/`.

## Toolchain / host side

atlas (this machine) is the dev host. Self-contained: `.venv` (west, cmake, ninja, py deps) + `~/zephyr-sdk-1.0.1` (arm-zephyr-eabi gcc 14.3). Flash/debug runner = STM32CubeProgrammer (user-installed, license-gated) over ST-Link. No system packages were installed; no sudo anywhere in the build path.

## Decisions

- **2026-06-10 — Zephyr over STM32Cube/ThreadX** for this project: industry momentum, user's stated goal, first-class IMX335+DCMIPP support already upstream. Tradeoff accepted: the Neural-ART NPU is unreachable from Zephyr today; NPU experiments will be a separate STM32Cube.AI track, with learnings ported back.
- **2026-06-10 — Pinned release (v4.4.1) + narrowed modules** instead of tracking `main` with full module set: reproducible builds, 1.7 GB instead of ~8 GB, camera support is already in-release so we don't need main.
- **2026-06-10 — `zephyr,sram = &axisram1` via local overlay** rather than patching the board dts in-tree: keeps `zephyr/` pristine/pinned; candidate for an upstream PR (the DK already does exactly this).
- **2026-06-10 — Sample-first bring-up**: prove the full HW path with the upstream capture sample before writing our own app, so any failure is isolated to hardware/config, not our code.
