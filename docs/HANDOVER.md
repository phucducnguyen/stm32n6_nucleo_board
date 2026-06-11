# HANDOVER

## CURRENT (2026-06-11)

**State: board bring-up fully verified (hello_world runs both boot paths). Camera build loads and runs but is SILENT before the console — a genuine early-init fault, NOT a memory-layout problem. Next move needs fault data, not more blind flashes. A preflight flash gate now prevents the wasted-cycle mistakes that bit us.**

What's proven on hardware:
- Full chain works: build → auto-sign (CubeProgrammer 2.22 user-space at `~/STMicroelectronics`) → flash-boot run (hello banner) AND serial-boot push (`//sb` + DFU on CN8 → banner, ~5 s loop).
- `scripts/host-setup.sh` ran (dialout, udev, tools). Use `sg dialout` until next re-login.
- **BootROM loads images ONLY into the 511 KB window at `0x34180400`** (flash + serial boot). Relink-to-axisram1 overlays (`bigram`/`noflex`) are dead ends. Camera memory fix = `overlays/nucleo_n657x0_q_vidpool.overlay`. Full boot/DFU/RAM reference in **`docs/N6-FACTS.md`** — read that, don't re-research.

Camera bring-up — where it actually stands (debugged 2026-06-11):
- `build/t3-cam-vidpool` (and a verbose-logging `build/cam-dbg`, `CONFIG_VIDEO_LOG_LEVEL_DBG=y`) both DOWNLOAD and START fine, but emit **zero bytes** on the console — no banner, no driver logs, no fault dump. Confirmed not a capture artifact: same `usart1`→`ttyACM0` console as the working hello build; direct 115200 read = nothing.
- **Memory layout is RULED OUT as the cause.** The pool's named region is `(NOLOAD)` in `linker.cmd` → C-startup never copies/zeroes FLEXRAM `0x34000000`. The old "FLEXRAM-as-pool" suspicion is dead.
- Boot order (`zephyr/kernel/init.c` `bg_thread_main`): `POST_KERNEL` device init (line 302) runs *before* `boot_banner()` (line 311). DCMIPP + IMX335 are `POST_KERNEL`. **But** verbose video logging printed nothing either → execution dies **before** `POST_KERNEL`, i.e. earlier than the camera drivers. So the fault is in what the camera DT/shield changes at `PRE_KERNEL` (pinctrl / GPIO hogs / clock tree for the CSI+DCMIPP path), in the secure/serial-boot context. Hello (same board, no shield) does NOT hit it.

New serial-boot facts learned this session (now in N6-FACTS + CLAUDE.md):
- A **refused** download ("failed to download Sector[0]", wrong link address) **consumes the one-shot DFU session** and zombifies it → the next push segfaults (exit -11). Lost a power cycle to this.
- Hence **`scripts/preflight-flash.sh`** (gate) + **`scripts/test-preflight.sh`** (regression test): never `west flash` bare again; preflight checks link address/fit/signed/DFU-armed before pushing.

**Full investigation log + everything ruled out: `docs/camera-bringup-debug-log.md` — READ THAT FIRST, don't repeat experiments.** Ruled out so far: memory layout, clock tree, jumpers/power, the IMX335 sensor, shell buffering, and SWD debugging (debug port locked in serial boot).

**NEXT SESSION — start here (one power cycle):**
1. `scripts/preflight-flash.sh build/cam-trace-clean --flash` — this build is BUILT + signed + waiting. It has `printk` boot probes + DCMIPP-step markers (shell off → direct output).
2. Capture console via the reliable method: background `sg dialout -c "stty -F /dev/ttyACM0 115200 raw -echo; cat /dev/ttyACM0 > /tmp/x.log"` started before the flash. (serlog3.py was flaky — exit 144.)
3. Interpret the `DBGMARK` lines per the table in the debug log: tells us if the console works at all, how far boot gets, and exactly where the DCMIPP init stalls (prime suspect `HAL_DCMIPP_Init`).
4. Temporary debug edits live in the pinned `zephyr/` tree (dcmipp markers + sample SYS_INIT probes) — revert when done: `git -C zephyr checkout drivers/video/video_stm32_dcmipp.c samples/drivers/video/capture/src/main.c`.

## DEFERRED

- Upstream PR to zephyr: `zephyr,sram = &axisram1` for nucleo_n657x0_q (mirror of DK commit). Do after first flash confirms the overlay on hardware.
- PYNQ-Z2 FPGA track: deliberately parked until the N6 camera milestone lands (user agreed 2026-06-10).
- NPU (Neural-ART) exploration: separate STM32Cube.AI/X-CUBE-N6 track — Zephyr has no NPU driver; don't look for one.

## HISTORY

(none yet — project started 2026-06-10)
