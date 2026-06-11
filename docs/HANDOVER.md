# HANDOVER

## CURRENT (2026-06-10, late)

**State: toolchain + sign + flash + serial-boot ALL verified on hardware (hello_world runs both ways). Camera first light is one power-cycle away: `build/t3-cam-vidpool` is built with the now-correct memory layout, waiting for an armed DFU session.**

What's proven on hardware:
- Full chain works: build → auto-sign (CubeProgrammer 2.22 user-space at `~/STMicroelectronics`) → flash-boot run (hello banner in run mode) AND serial-boot push (`//sb` + DFU on CN8 → banner, ~5 s loop).
- `scripts/host-setup.sh` ran (dialout, udev, tools). Use `sg dialout` until next re-login.
- **Root cause of every camera-boot failure found and documented:** the BootROM loads images ONLY into the 511 KB window at `0x34180400` — the old `bigram`/`noflex` overlays (relink to axisram1) are dead ends. Correct fix = `overlays/nucleo_n657x0_q_vidpool.overlay` (image in stock window, 1.25 MB pool in named region `AXISRAM1`). Deep-researched + written up in **`docs/N6-FACTS.md`** (BootROM, jumpers, DFU rules, LEDs, RAM map — read that, don't re-research).
- `build/t3-cam-vidpool` links exactly right: RAM 113 KB/511 KB, AXISRAM1 1.25 MB/1536 KB. 640×480 RGBP, EARLY_CONSOLE + immediate logging.

Next step (start here):
1. Board to serial boot (BOOT0 unprinted, BOOT1 printed), both cables, **full power cycle** (RESET doesn't re-arm DFU; zombie DFU segfaults the CLI — see N6-FACTS).
2. Start logger: `sg dialout -c "nohup ~/projects/stm32n6/.venv/bin/python /tmp/serlog3.py > /tmp/uartX.log 2>&1 &"` (logger globs newest ttyACM; recreate from N6-FACTS if /tmp was cleaned).
3. `lsusb | grep df11` → then `PATH="$HOME/projects/stm32n6/.venv/bin:$HOME/STMicroelectronics/STM32CubeProgrammer/bin:$PATH" west flash -d build/t3-cam-vidpool` from the workspace.
4. Pass criteria: IMX335 probed @ csi_i2c 0x1a, buffers dequeuing steadily, no DCMIPP overruns. If silent: FLEXRAM-as-pool is the prime suspect (pool region starts at 0x34000000 = FLEXRAM) — fallback is AXISRAM3–6 via RAMCFG (N6-FACTS § memory map).
5. After first light: flash-boot the same config (plain board target) for a persistent demo; then update this file + TODO M1.

## DEFERRED

- Upstream PR to zephyr: `zephyr,sram = &axisram1` for nucleo_n657x0_q (mirror of DK commit). Do after first flash confirms the overlay on hardware.
- PYNQ-Z2 FPGA track: deliberately parked until the N6 camera milestone lands (user agreed 2026-06-10).
- NPU (Neural-ART) exploration: separate STM32Cube.AI/X-CUBE-N6 track — Zephyr has no NPU driver; don't look for one.

## HISTORY

(none yet — project started 2026-06-10)
