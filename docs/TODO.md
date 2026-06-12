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
- [ ] **fix the boot-timing race properly** (sensor-init delay/retry; today masked by `overlays/debug-logging.conf` synchronous logging) — then revert the zephyr temp edits (list in debug log) and consider upstreaming
- [ ] flash-boot the working config (plain board target) for a persistent standalone demo
- [ ] clean up `build/` experiment dirs

## M2 — own application (`apps/camera-app`) — frame export landed early via UVC
- [x] frame export path for eyeballing images on atlas: USB webcam (Zephyr UVC sample → `build/uvc`, VGA needs the 1.25 MB pool configs) + ustreamer browser stream (`scripts/cam-stream.sh`, :8090) + on-demand AI description (`scripts/cam-describe.sh`) ✅ 2026-06-11
- [x] `apps/camera-app` created — UVC sample forked + owned (shield/overlay/configs self-contained, encoder dead code stripped, `app_usbd.c` replaces sample helpers); builds clean, 135 KB in the 511 KB window ✅ 2026-06-12
- [ ] **flash `build/camera-app` via swd-run.sh + verify webcam/stream on hardware** (board was unplugged 2026-06-12) — then revert the two sample-file temp edits in `zephyr/`
- [ ] extend the app: Zephyr shell cmds (snap/stats), capture-N-frames mode
- [ ] white balance for the green cast (DCMIPP pipeline config) + manual lens focus
- [x] make the 30 dB gain default a proper Kconfig/app setting — `CONFIG_APP_CAMERA_ANALOGUE_GAIN_MDB` in camera-app ✅ 2026-06-12
- [ ] decide+document frame geometry: DCMIPP crop/downscale config vs RAM budget (full 5 MP never fits internal SRAM)

## M3 — processing on target (firmware-engineer muscle)
- [ ] per-frame processing hook (start: luma histogram / simple motion diff between buffers)
- [ ] measure: fps, CPU load, memory headroom; try Helium/MVE (CMSIS-DSP is already a west module — just `west update cmsis-dsp`)

## M4 — edge AI (separate track, feeds back in)
- [ ] STM32Cube.AI / X-CUBE-N6 evaluation for Neural-ART NPU (NOT Zephyr — keep in its own dir/repo)
- [ ] candidate demo: person/vehicle detection on live camera, compare NPU vs M55+Helium fps

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
