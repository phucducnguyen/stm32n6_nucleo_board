# TODO — milestone ladder

Each milestone is independently demo-able; don't start M(n+1) before M(n) passes on hardware.

## M0 — toolchain bring-up ✅ 2026-06-10
- [x] west workspace, Zephyr v4.4.1 pinned, narrowed modules, venv-only host tools
- [x] capture sample builds for nucleo_n657x0_q + B-CAMS-IMX shield (RAM overlay fix)

## M1 — first light (camera frames over serial console) ✅ 2026-06-11
- [x] USER: run `scripts/host-setup.sh` via `!` (sudo) ✅ 2026-06-10
- [x] STM32CubeProgrammer 2.22 installed user-space (`~/STMicroelectronics`), signing + flashing verified ✅
- [x] hello_world verified BOTH ways on hardware: flash-boot run mode + serial-boot DFU push ✅
- [x] root-cause camera boot failures → BootROM 511 KB load window; correct fix = vidpool overlay; research consolidated in `docs/N6-FACTS.md` ✅
- [x] flash gate to stop wasting power-cycle sessions: `scripts/preflight-flash.sh` + `scripts/test-preflight.sh` ✅ 2026-06-11
- [x] "silent boot" SOLVED — was dead USB cable + log-drop trap (`CONFIG_LOG_PRINTK` deferred buffer) + imx335 boot-timing race; see `docs/camera-bringup-debug-log.md` ✅ 2026-06-11
- [x] IMX335 probed @ csi_i2c 0x1a, frames dequeuing at stable 30 fps, real pixel content verified (lens-cover test) ✅ 2026-06-11
- [x] SWD debug access regained (dev boot); new flash loop = `scripts/swd-run.sh` — no power cycles, no DFU ✅ 2026-06-11
- [x] **fix the boot-timing race properly** — settle (`K_USEC(600)→K_MSEC(5)`) + bounded I2C retry in `imx335.c`; VALIDATED 3/3 cold flash-boots 2026-06-14; DBGMARK markers reverted; saved as `patches/imx335-cold-boot-init.patch` (upstream-worthy). See debug-log §2026-06-14 ✅
- [x] flash-boot the working config (plain board target) for a persistent standalone demo — done 2026-06-14 (signed image in external flash at 0x70000000, cold-boots and streams standalone) ✅
- [ ] clean up `build/` experiment dirs (now incl. `camera-app-stats`, `camera-app-coldtest`)
- [ ] (optional) switch prod logging off the sync crutch — race no longer needs `CONFIG_LOG_MODE_IMMEDIATE`; use a big deferred buffer instead (avoids the trap-#1 log-drop). Also consider an upstream IMX335 cold-boot PR

## M2 — own application (`apps/camera-app`) — frame export landed early via UVC
- [x] frame export path for eyeballing images on atlas: USB webcam (Zephyr UVC sample → `build/uvc`, VGA needs the 1.25 MB pool configs) + ustreamer browser stream (`scripts/cam-stream.sh`, :8090) + on-demand AI description (`scripts/cam-describe.sh`) ✅ 2026-06-11
- [x] `apps/camera-app` created — UVC sample forked + owned (shield/overlay/configs self-contained, encoder dead code stripped, `app_usbd.c` replaces sample helpers); builds clean, 135 KB in the 511 KB window ✅ 2026-06-12
- [x] **flash `build/camera-app` + verify webcam/stream on hardware** — HW verify PASSED 2026-06-13; the two sample-file temp edits in `zephyr/` reverted ✅
- [ ] extend the app: Zephyr shell cmds (snap/stats), capture-N-frames mode
- [ ] white balance for the green cast (DCMIPP pipeline config) + manual lens focus
- [x] make the 30 dB gain default a proper Kconfig/app setting — `CONFIG_APP_CAMERA_ANALOGUE_GAIN_MDB` in camera-app ✅ 2026-06-12
- [ ] decide+document frame geometry: DCMIPP crop/downscale config vs RAM budget (full 5 MP never fits internal SRAM)

## M3 — processing on target (firmware-engineer muscle)
- [x] per-frame processing hook — `apps/camera-app/src/frame_stats.{c,h}` (gated `CONFIG_APP_FRAME_STATS`, default off): luma mean/min/max + 8×8-block motion + per-frame timing, ~1 Hz log. HW-verified 2026-06-14 (commit 4655ed2) ✅
- [~] measure — fps + per-frame proc time measured on hardware: a full per-pixel scan costs ~336 ms/frame and drops UVC 20→2 fps (cost = ~1 µs/byte reading the non-cacheable AXISRAM DMA buffer under CSI-DMA contention; `CONFIG_APP_FRAME_STATS_STRIDE` default 8 → back to 20 fps). Still TODO: CPU-load %, Helium/MVE + CMSIS-DSP (`west update cmsis-dsp`)

## M4 — edge AI (separate track, feeds back in) — ⭐ PRODUCT PATH (decided 2026-06-25)
> **Venture wedge CHOSEN:** the N6 as a **vision-grade local occupancy sensor** — an
> on-device vision node that watches a space (room / queue / booth / counter), runs
> inference ON-DEVICE, and emits **anonymous usage events** (people count, seat/desk
> occupancy, queue length, dwell, zone engagement). **Core promise: the camera image
> NEVER leaves the device** — that local-only guarantee is both the privacy story and
> the wedge vs cloud cameras. Data flow: **camera → on-device inference (count) → emit a
> small anonymous event to atlas; the frame stays on the board.** Positioning: NOT "more
> private than thermal" (camera-free thermal/blob sensors own a hardware privacy
> guarantee a camera can't match) — the wedge is **richer spatial detail than thermal,
> WITH local-only processing**, aimed at granularity-hungry, less-camera-shy spaces
> (retail / clinic / gym / booth / queue), not privacy-maximalist offices. The pose spike
> (M4 below) already proved the NPU path works at ~15 ms — so the next **product-aligned**
> capability is **on-device person detection / counting + event emit**, NOT frame
> streaming. Full product thesis: `PRODUCT-DIRECTION.md` in the Edge Kit planning vault.
> **Validation gate still holds:** a real space manager must care about a weekly usage
> report before any custom PCB.
- [ ] STM32Cube.AI / X-CUBE-N6 evaluation for Neural-ART NPU (NOT Zephyr — keep in its own dir/repo)
- [ ] ⭐ **product-aligned next build: on-device person-detection / counting** — camera → NPU count → emit one small anonymous event to atlas (NO image leaves the board). This is the wedge capability; build it next. Compare NPU vs M55+Helium fps as a side note, not the goal.
  - **Step-1 de-risk (mirror the pose spike — flash ST's prebuilt hex first):** flash ST's **prebuilt NUCLEO-N657X0-Q + USB/UVC** people-detection hex from `x-cube-n6-ai-people-detection-tracking` (`Binary/`). ⚠️ **Expect `tiny_yolo_v2` on the NUCLEO flavor — `YoloX` is the DK flavor**, so don't assume YOLOX here. Backup: `STM32N6-GettingStarted-ObjectDetection` (also ships a NUCLEO/UVC hex).
  - **Boot mode:** external-flash boot for a standing / café-mount test (N6 has no internal flash; dev-mode loads to SRAM and is lost on power-off). Flash via `swd-run.sh -hardRst` (soft `-rst` bricks AP1).
  - **Verify on the UVC stream, explicitly — do NOT assume:** (a) person **boxes** shown? (b) **track IDs** shown at all on the NUCLEO? (c) IDs **stable** (persist across frames / brief occlusion)? (d) boxes/IDs **programmatically accessible** (event / CDC) or **overlay-only**? The repo README scopes tracking/box-filtering to the **DK**, but the NUCLEO UVC quickstart shows track IDs → **ambiguous; this answer decides v0 vs v0.2**.
  - **Counting:** v0 = count boxes per frame (+ simple server-side temporal smoothing). **dwell / queue-length / zone = v0.2**, only if stable + accessible track IDs prove out. Emit `people_count` over **USB-CDC → host bridge → the event spine** (mirror `pose-bridge.py`; board has no net stack).
  - **Model to standardize on (later, license-clean):** `st_yolo_x_nano` (ST YOLOX-Nano, COCO-Person, INT8, **SLA0044**) at 256² (~9 ms) / 320² (~13 ms), swapped in via the model-zoo deploy service **after** TinyYOLOv2 is measured here. **Reject YOLOv8/v11/26 (AGPL).** Measure real NUCLEO latency/fps at the bench — published numbers are DK-sourced.
- [~] **pose de-risk spike** (ST's `STM32N6-GettingStarted-PoseEstimation`, ST track not Zephyr) — RAN 2026-06-14: camera→NPU→MoveNet→UVC chain works on hardware, ~15 ms inference, skeleton drawn when a person is in frame. TODO to fully close: one clean still (upright, sharp) to confirm nose/shoulder tracking + slouch signal; camera is mounted ~90° rotated. ST repo at `~/projects/st-pose` (sibling, not in git). Reflash via `mode=UR` (see `docs/pose-bringup-checklist.md`).

## M5 — networked camera node (Ethernet) — PLUMBING/LEARNING, not the product path
> Build: Ethernet + HTTP networking muscle for the N6 as a LAN node. Ethernet is
> enabled out-of-the-box in Zephyr v4.4.1 for this board (no overlay); `dhcpv4_client`
> builds at 44% of the 511 KB window. Driver `eth_stm32_hal_v2.c`, MAC from OTP/BSEC.
> **Scope note (2026-06-25):** N1/N2 (DHCP + HTTP) are useful networking PLUMBING toward
> the product event-emit path. **N3 (streaming a camera frame out to a browser) is
> LEARNING ONLY, NOT the product** — sending the frame off the board directly contradicts
> the "images never leave the device" promise. The product path emits a small **anonymous
> event** (a count), not a frame — see N4 and the M4 product note.
- [ ] **N1 — first packet (plumbing):** `swd-run build/net-dhcp` → DHCP lease on the LAN, ping atlas ↔ N6 both ways (board in dev-boot, RJ45 → LAN). Sample already builds (`build/net-dhcp`).
- [ ] **N2 — hello over HTTP (plumbing):** N6 runs an HTTP server; browser → N6 → text + uptime/status. Foundation for the event-emit transport.
- [ ] **N3 — snapshot over HTTP (LEARNING ONLY — NOT product):** browser → N6 → a camera still. Useful as a JPEG/HTTP feasibility spike (does the N6 JPEG-encode? hardware JPEG codec exists; Zephyr driver? — investigate), but streaming a frame off the board CONTRADICTS the local-only promise — do NOT ship this as the product.
- [ ] **N4 — process + report ⭐ (product transport):** on-board count (M4 person-detection) / motion (M3 frame-stats) emits a small **anonymous event** to atlas (Edge Kit event-spine pattern) — the image stays on the board. This is the product-aligned use of the Ethernet stack.

> **Bigger picture (2026-06-12):** this project is the *perception-node lane* of the
> **Nebula Edge Kit** platform (Nanos as BLE sensor tags, atlas as event server, GPU
> boxes as the training flywheel, future carrier-shield PCB). Platform plan:
> `second-brain/projects/edge-kit/PLAN.md` (in the vault). M4 here ≈ Edge Kit "E2b";
> this repo's M-ladder continues unchanged and stays self-contained.

## Housekeeping / opportunistic
- [ ] upstream PR: capture-sample conf/overlay for nucleo_n657x0_q (upstream has NONE; the vidpool named-region pattern is the contribution — the old `zephyr,sram = &axisram1` idea was wrong, see N6-FACTS)
- [ ] `ccache` wiring for faster rebuilds (installed by host-setup.sh; zephyr picks it up via `CCACHE` env or sdkconfig)
- [ ] decide if/when this repo gets a GitHub remote (user call; local-only until asked)

## Parked
- PYNQ-Z2 FPGA vision-accel learning track — revisit after M2/M3
- Government bid tracker — software-only, belongs in the Nebula/n8n world, not here
