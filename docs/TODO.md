# TODO — milestone ladder

Each milestone is independently demo-able; don't start M(n+1) before M(n) passes on hardware.

## M0 — toolchain bring-up ✅ 2026-06-10
- [x] west workspace, Zephyr v4.4.1 pinned, narrowed modules, venv-only host tools
- [x] capture sample builds for nucleo_n657x0_q + B-CAMS-IMX shield (RAM overlay fix)

## M1 — first light (camera frames over serial console)
- [x] USER: run `scripts/host-setup.sh` via `!` (sudo) ✅ 2026-06-10
- [x] STM32CubeProgrammer 2.22 installed user-space (`~/STMicroelectronics`), signing + flashing verified ✅
- [x] hello_world verified BOTH ways on hardware: flash-boot run mode + serial-boot DFU push ✅
- [x] root-cause camera boot failures → BootROM 511 KB load window; correct fix = vidpool overlay; research consolidated in `docs/N6-FACTS.md` ✅
- [x] flash gate to stop wasting power-cycle sessions: `scripts/preflight-flash.sh` + `scripts/test-preflight.sh` ✅ 2026-06-11
- [ ] **BLOCKER: camera build is silent before the console** (dies before `POST_KERNEL`, earlier than the camera drivers; memory layout ruled out — see HANDOVER CURRENT). Bisect which camera-DT node enabled at `PRE_KERNEL` faults (clock tree / pinctrl / csi_gpio hogs) — static diff cam-dbg vs hello-sb first, then minimal-DT bisection flashes
- [ ] (if reachable) read SCB CFSR/HFSR+PC via ST-Link in serial-boot mode to locate the fault directly
- [ ] once faulting node known: fix → IMX335 probed @ csi_i2c 0x1a, buffers dequeuing at stable fps, no DCMIPP overruns
- [ ] flash-boot the working config (plain board target) for a persistent demo
- [ ] clean up `build/` experiment dirs; keep `cam-sb` naming from CLAUDE.md

## M2 — own application (`apps/camera-app`)
- [ ] minimal app: init camera via video API, capture N frames, expose Zephyr shell cmds (snap/stats)
- [ ] frame export path for eyeballing images on atlas (pick one: UART hexdump (slow, zero deps) vs USB bulk vs Ethernet TCP — `samples/drivers/video/tcpserversink` exists upstream, board has RJ45)
- [ ] decide+document frame geometry: DCMIPP crop/downscale config vs RAM budget (full 5 MP never fits internal SRAM)

## M3 — processing on target (firmware-engineer muscle)
- [ ] per-frame processing hook (start: luma histogram / simple motion diff between buffers)
- [ ] measure: fps, CPU load, memory headroom; try Helium/MVE (CMSIS-DSP is already a west module — just `west update cmsis-dsp`)

## M4 — edge AI (separate track, feeds back in)
- [ ] STM32Cube.AI / X-CUBE-N6 evaluation for Neural-ART NPU (NOT Zephyr — keep in its own dir/repo)
- [ ] candidate demo: person/vehicle detection on live camera, compare NPU vs M55+Helium fps

## Housekeeping / opportunistic
- [ ] upstream PR: capture-sample conf/overlay for nucleo_n657x0_q (upstream has NONE; the vidpool named-region pattern is the contribution — the old `zephyr,sram = &axisram1` idea was wrong, see N6-FACTS)
- [ ] `ccache` wiring for faster rebuilds (installed by host-setup.sh; zephyr picks it up via `CCACHE` env or sdkconfig)
- [ ] decide if/when this repo gets a GitHub remote (user call; local-only until asked)

## Parked
- PYNQ-Z2 FPGA vision-accel learning track — revisit after M2/M3
- Government bid tracker — software-only, belongs in the Nebula/n8n world, not here
