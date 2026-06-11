# TODO — milestone ladder

Each milestone is independently demo-able; don't start M(n+1) before M(n) passes on hardware.

## M0 — toolchain bring-up ✅ 2026-06-10
- [x] west workspace, Zephyr v4.4.1 pinned, narrowed modules, venv-only host tools
- [x] capture sample builds for nucleo_n657x0_q + B-CAMS-IMX shield (RAM overlay fix)

## M1 — first light (camera frames over serial console)
- [ ] USER: run `scripts/host-setup.sh` via `!` (sudo), re-plug board, re-login
- [ ] USER: download STM32CubeProgrammer (Linux zip) from st.com → give path
- [ ] install CubeProgrammer user-space, PATH, verify `STM32_SigningTool_CLI -v` + `STM32_Programmer_CLI --list`
- [ ] rebuild (auto-sign) → BOOT1=1 → `west flash` → BOOT1=0 → reset
- [ ] verify on console: IMX335 probed @ csi_i2c 0x1a, buffers dequeuing at stable fps, no DCMIPP overruns
- [ ] try the `…/sb` serial-boot variant for the fast dev loop (no flash wear, ~seconds per iteration)

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
- [ ] upstream PR: nucleo_n657x0_q `zephyr,sram = &axisram1` (after M1 validates it on hardware)
- [ ] `ccache` wiring for faster rebuilds (installed by host-setup.sh; zephyr picks it up via `CCACHE` env or sdkconfig)
- [ ] decide if/when this repo gets a GitHub remote (user call; local-only until asked)

## Parked
- PYNQ-Z2 FPGA vision-accel learning track — revisit after M2/M3
- Government bid tracker — software-only, belongs in the Nebula/n8n world, not here
