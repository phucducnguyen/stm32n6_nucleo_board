# CLAUDE.md — STM32N6 camera project cheat sheet

Architecture → `docs/architecture.md` · Current state → `docs/HANDOVER.md` · Backlog → `docs/TODO.md` ·
**Board/BootROM facts (researched + verified, read before touching boot/memory) → `docs/N6-FACTS.md`**.
This file = how to build/flash and what not to break.

## What this is

Zephyr RTOS camera firmware for the **NUCLEO-N657X0-Q** (STM32N657X0, Cortex-M55 @ 800 MHz + Neural-ART NPU) with the **B-CAMS-IMX** camera module (Sony IMX335, 5 MP, MIPI CSI-2). Long-term arc: camera capture → frame processing → edge-AI vision. NOT a nebula service — lives in `~/projects/`, nothing here touches `/srv/nebula`.

## Product direction (Edge Kit wedge, chosen 2026-06-25)

This board is the on-device vision node for a **vision-grade local occupancy sensor**: it watches a space (room / queue / booth / counter), runs inference **on-device**, and emits only **anonymous usage events** (people count, seat/desk occupancy, queue length, dwell, zone engagement) to atlas. Flow: camera → on-device count → small event out. **INVARIANT — do not break: the camera image NEVER leaves the device.** Local-only is both the privacy story and the wedge vs cloud cameras; "stream the camera frame out to a browser" contradicts it (that's debug plumbing, not product). Positioning is *richer spatial detail than thermal/blob + local-only* for granularity-hungry, less-camera-shy spaces (retail/clinic/gym/booth/queue) — NOT "more private than thermal". Full thesis: Edge Kit planning vault `PRODUCT-DIRECTION.md`.

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

Always with the venv on PATH (west sub-tools resolve `cmake` by name).

**Primary: our application `apps/camera-app/`** (UVC webcam; forked from the
upstream uvc sample 2026-06-12 — shield, vidpool overlay, pool configs,
sync-logging and startup gain are all owned by the app, so no `-D` flags):

```sh
cd ~/projects/stm32n6
export PATH="$PWD/.venv/bin:$PATH"
west build -p -b 'nucleo_n657x0_q//sb' apps/camera-app -d build/camera-app
```

(drop the `//sb` and use the plain board name for the flash-boot build)

Upstream samples can still be built as references; they need the full flag set
on the command line — `--shield st_b_cams_imx_mb1854`,
`-DEXTRA_DTC_OVERLAY_FILE="$PWD/overlays/nucleo_n657x0_q_vidpool.overlay"`,
`-DEXTRA_CONF_FILE="$PWD/overlays/debug-logging.conf"`, plus pool/format
configs — exact capture/uvc commands in `docs/camera-bringup-debug-log.md`.

## Flash / run (STM32N6 has NO internal flash — read this)

The boot ROM only executes **signed** images, chainloaded from external octo-SPI flash (MX25UM51245G) or pushed over USB in serial-boot mode. Signing is automatic at build **iff** `STM32_SigningTool_CLI` (ships inside STM32CubeProgrammer ≥ 2.18) is on PATH.

| Mode | BOOT0 (JP1) | BOOT1 (JP2) | Use |
|---|---|---|---|
| Run from ext flash | 0 (printed) | 0 (printed) | normal operation; debug port CLOSED (normal); red LED = BOOTFAILED |
| Dev boot (flash via ST-Link) | x | 1 (unprinted) | `west flash` for the default target; BOOT1 wins over BOOT0 |
| Serial boot (USB DFU on CN8) | 1 (unprinted) | 0 (printed) | `//sb` variant: push to RAM, runs immediately; needs BOTH cables (CN10 power/console + CN8 DFU) |

Jumper position 1 = the printed/silkscreen side = logic 0. Console: newest `/dev/ttyACM*` (renumbers on replug!) 115200 8N1.

**Dev loop (PRIMARY since 2026-06-11): dev boot + `scripts/swd-run.sh`.**
With BOOT1 parked on the unprinted side (dev boot), SWD stays open and:
```sh
scripts/swd-run.sh build/<dir>    # hard-reset, load RAM @0x34180400, set VTOR/MSP/PC, run
```
gives unlimited flashes — no power cycles, no DFU, no signing — plus debugger
access. Gotcha (in the script): only `-hardRst`; a software `-rst` bricks AP1
until the next hardware reset. Serial-boot DFU below is for flash-boot
shipping / when jumpers are in serial-boot position.

**Serial-boot flashing: ALWAYS through the preflight gate — never bare `west flash`:**
```sh
scripts/preflight-flash.sh build/<dir> --flash    # verifies, then pushes only if it can load
scripts/preflight-flash.sh build/<dir>            # verify-only, no hardware touched
```
It refuses to push an image that can't load, which is what burns power-cycle sessions. Checks: signed bin exists, image links into the BootROM window `0x34180400` (catches relink-overlay builds), fits 511 KB, and DFU is armed. Regression test: `scripts/test-preflight.sh` (run after editing the gate). Rationale below.

**Serial-boot session rules:** one download per power-up; RESET does NOT re-arm DFU — only a full power cycle (both cables out) does; check `lsusb | grep df11` before pushing; a zombie DFU entry makes `STM32_Programmer_CLI` segfault (exit -11) — that means power cycle, nothing is broken. **A *refused* download (wrong link address → "failed to download Sector[0]") ALSO consumes the one-shot session and zombifies it** — so the very next push segfaults. That is exactly why preflight checks the link address *before* pushing. Full detail: `docs/N6-FACTS.md`.

## Debugging — hard-won rules (cost us days; do NOT relearn)

Full story: `docs/camera-bringup-debug-log.md`. The three traps, in order of pain:

1. **printk is NOT synchronous in this project's builds.** The video sample sets
   `CONFIG_LOG_PRINTK=y` + deferred logging + a 1 KB buffer, so printk is rerouted
   into the log buffer and **boot-time output silently vanishes**
   (`--- N messages dropped ---`). "My marker didn't print, so the code didn't run"
   is INVALID for init-time code unless the build has
   `-DEXTRA_CONF_FILE=$PWD/overlays/debug-logging.conf`
   (`CONFIG_LOG_MODE_IMMEDIATE=y` — synchronous, nothing droppable).
   **Any boot/init debugging starts with that fragment. No exceptions.**
2. **Verify the physical layer before debugging firmware.** The multi-session
   "camera build is totally silent" mystery was a dead USB cable. Before any
   "no output" theory: swap the cable, then prove the console with a known-good
   image (`build/hello-sb`). A logger script is not proof — use the raw capture:
   `sg dialout -c "stty -F /dev/ttyACM0 115200 raw -echo; cat /dev/ttyACM0 > /tmp/x.log"`
   started in the background BEFORE flashing (serlog3.py was flaky — don't use it).
3. **Timing races hide behind logging changes.** The camera failure itself was a
   boot-timing race (fast deferred-log boot hits the IMX335 before it's ready →
   init bails → 0 controls → link-freq -ENOTSUP). Synchronous logging slows boot
   and masks it. If behavior changes when logging config changes, suspect a race,
   not the logs.

## Invariants — do not break

- **The BootROM loads images ONLY into the 511 KB window at `0x34180400` (axisram2) — flash boot AND serial boot.** Every image must link there (= the stock `zephyr,sram`). `overlays/nucleo_n657x0_q_bigram.overlay` and `_axisram_noflex.overlay` (relink to axisram1) are **dead ends kept as documentation** — downloads outside the window are refused, flash-boot images linked elsewhere die silently.
- **Camera needs `overlays/nucleo_n657x0_q_vidpool.overlay`**: image stays in the 511 KB window; the 1.25 MB video buffer pool goes to the big RAM as named region `AXISRAM1` via `CONFIG_VIDEO_BUFFER_POOL_ZEPHYR_REGION[_NAME]` (same pattern the DK uses upstream with its PSRAM). Without it: `region RAM overflowed` at link.
- **Zephyr stays pinned at v4.4.1.** No bare `west update` (it would also pull every module). Upgrading = deliberate task: bump tag, `west update --narrow -o=--depth=1 cmsis cmsis_6 hal_stm32`, rebuild, retest camera.
- **Don't fix "cmake not found" with sudo apt.** It's in `.venv/bin`; put it on PATH.
- **Video buffer pool must fit its region:** width×height×2 bytes/frame ×2 buffers ≤ 1536 KB (the shrunk `AXISRAM1` region; the *image* has its own separate 511 KB budget). 640×480 RGB565 ×2 = 1.2 MB fits. Full 5 MP (2592×1944) does NOT fit internal SRAM — that needs DCMIPP downscale/crop (it can) or AXISRAM3–6 (4×448 KB contiguous, RAMCFG-gated, see `docs/N6-FACTS.md`).
- **Signing tool errors ≠ build errors.** `zephyr.bin` builds fine without CubeProgrammer; only flashing/booting needs the signed image.
- Serial port needs `dialout` group membership (no sudo workarounds in scripts).
- Boot pins: don't leave BOOT1=1 after flashing — the board will sit in dev boot doing nothing.

## Conventions (inherited from nebula where they apply)

- Edit in place; this repo is local-only (no remote — don't add one without being asked).
- No `Co-Authored-By` trailers in commits.
- No secrets here, and there should never be any (it's firmware; tokens don't belong in this tree).
- Record design choices in `docs/architecture.md` § Decisions as they happen.
- STOP and ask on unexpected hardware state (board not enumerating, flash verify fail, etc.).
