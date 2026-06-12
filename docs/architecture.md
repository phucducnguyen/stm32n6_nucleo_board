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
| AXISRAM2 (`axisram2` node) | 0x34180400 | 511 KB | **system RAM — code+data. The ONLY window the BootROM loads into (flash AND serial boot); every image must fit + link here** |
| FLEXRAM + AXISRAM1 (+ first 512 KB of AXISRAM2) | 0x34000000 | 1536 KB (shrunk by our overlay) | runtime-only **named region `AXISRAM1`** — video buffer pool lives here, placed at first alloc, never part of the image |
| AXISRAM3–6 | 0x34200000 | 4 × 448 KB contiguous | RAMCFG-gated, off by default; future bigger pools / NPU work (see `N6-FACTS.md`) |
| MX25UM51245G ext flash | 0x70000000 | 64 MB | signed boot image storage |

Budget rule of thumb: capture-sample image ≈ 110 KB of the 511 KB window; pool = W×H×2 bytes ×2 buffers ≤ 1536 KB (640×480 → 1.2 MB). Full BootROM/boot-mode reference: `docs/N6-FACTS.md`.

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
  our overlay                  ── vidpool: buffer pool → named region AXISRAM1 (see CLAUDE.md invariants)
```

Build composition = board dts + shield overlay + our `EXTRA_DTC_OVERLAY_FILE` + Kconfig fragments on the command line. No Zephyr-tree patches; everything we own sits outside `zephyr/`.

## Toolchain / host side

atlas (this machine) is the dev host. Self-contained: `.venv` (west, cmake, ninja, py deps) + `~/zephyr-sdk-1.0.1` (arm-zephyr-eabi gcc 14.3). Flash/debug runner = STM32CubeProgrammer (user-installed, license-gated) over ST-Link. No system packages were installed; no sudo anywhere in the build path.

## Decisions

- **2026-06-10 — Zephyr over STM32Cube/ThreadX** for this project: industry momentum, user's stated goal, first-class IMX335+DCMIPP support already upstream. Tradeoff accepted: the Neural-ART NPU is unreachable from Zephyr today; NPU experiments will be a separate STM32Cube.AI track, with learnings ported back.
- **2026-06-10 — Pinned release (v4.4.1) + narrowed modules** instead of tracking `main` with full module set: reproducible builds, 1.7 GB instead of ~8 GB, camera support is already in-release so we don't need main.
- **2026-06-10 — `zephyr,sram = &axisram1` via local overlay** rather than patching the board dts in-tree: keeps `zephyr/` pristine/pinned. **SUPERSEDED same day — see next entry.**
- **2026-06-10 — REVERSAL: image stays in the stock 511 KB window; only the video pool moves.** Hardware + ST docs (UM3234; `docs/N6-FACTS.md`) proved the BootROM loads images ONLY into AXISRAM2 @ `0x34180400`, for both flash and serial boot. Relinking `zephyr,sram` to axisram1 yields images the BootROM refuses over DFU ("failed to download Sector[0]") or silently crashes from flash. Correct architecture = `overlays/nucleo_n657x0_q_vidpool.overlay`: shrink `axisram1` to 1536 KB and point `CONFIG_VIDEO_BUFFER_POOL_ZEPHYR_REGION_NAME` at it — the same pattern the DK uses upstream with its PSRAM region. `_bigram` / `_axisram_noflex` overlays kept as documented dead ends.
- **2026-06-10 — Research-first after trial-and-error stalled**: ST + Zephyr primary-source findings consolidated into `docs/N6-FACTS.md` (BootROM window, BOOT1-over-BOOT0 priority, one-DFU-push-per-power-cycle, zombie-DFU segfault, BOOTFAILED red LED). Read it before touching boot or memory config.
- **2026-06-10 — Sample-first bring-up**: prove the full HW path with the upstream capture sample before writing our own app, so any failure is isolated to hardware/config, not our code.
- **2026-06-11 — Synchronous logging for all camera debugging** (`overlays/debug-logging.conf`, `CONFIG_LOG_MODE_IMMEDIATE=y`): the sample's deferred 1 KB log buffer + `CONFIG_LOG_PRINTK=y` silently dropped ALL init-time output (even printk), hiding the real camera failure for days. Side effect: the slower boot also *masks* the imx335 boot-timing race, so camera builds currently REQUIRE this fragment until the race is fixed in the driver.
- **2026-06-11 — Dev boot + `scripts/swd-run.sh` is the dev flash loop**, replacing serial-boot DFU: BOOT1 parked in dev boot keeps SWD open; hard-reset + RAM load + manual VTOR/MSP/PC + run gives unlimited, signing-free iterations with zero power cycles, and restores debugger access. Serial-boot DFU (`preflight-flash.sh`) is kept for flash-boot shipping. Verified gotcha: software `-rst` makes AP1 unreachable until a hardware reset — the script uses `-hardRst` only.
- **2026-06-11 — Frame export = USB UVC, not UART/TCP**: the upstream UVC sample turns the board into a standard USB webcam on CN8 (M2 had planned UART hexdump vs TCP; UVC beats both — standard, 30 fps, zero host deps). Host side: ustreamer (apt) serves the browser stream + `/snapshot`; chosen over a hand-rolled `ffmpeg -listen` loop, which proved single-client, gap-prone, and browser-hostile (Content-Type pitfalls).
- **2026-06-11 — AI sight is strictly on-demand** (`scripts/cam-describe.sh` → qwen3-vl on hotchocolate): no background watcher — the GPU is shared with other Nebula services and the user wants explicit control over when it's used.
