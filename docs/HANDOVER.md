# HANDOVER

## CURRENT (2026-06-13)

**State: `apps/camera-app` is now our verified primary firmware — HARDWARE
VERIFY PASSED 2026-06-13. M1 done, the app fork is done and proven on silicon.**

**Hardware verify (2026-06-13, board plugged in):** flashed
`build/camera-app` via `scripts/swd-run.sh` → clean boot, and all four checks
passed:
- `lsusb` → `2fe3:0011 STM32N6 IMX335 camera` (our owned USB identity).
- Host negotiated `YUYV 640x480`; `/dev/video0` live at 20 fps (ustreamer).
- Console logged `Analogue gain set to 30000 mdB` (our
  `CONFIG_APP_CAMERA_ANALOGUE_GAIN_MDB`), link-freq resolved (990 MHz),
  capture started, no crash.
- Stream live + updating (3 snapshots had distinct pixel hashes → sensor is
  genuinely capturing; frames were dark only because the bench was unlit).
Post-verify the two forked sample files were reverted
(`git -C zephyr checkout samples/subsys/usb/uvc/src/main.c
samples/drivers/video/capture/src/main.c`) — DONE. The 4 driver-file DBGMARK
edits remain (gated on the imx335 timing-race fix, see NEXT).

**The app fork (2026-06-12):** `apps/camera-app/` = self-contained fork of
`zephyr/samples/subsys/usb/uvc`: shield in CMakeLists, vidpool+uvc node in
`app.overlay`, pool/sync-logging configs in `prj.conf`, USB identity owned
(`src/app_usbd.c` + `APP_USBD_*`), encoder dead code stripped, gain now
`CONFIG_APP_CAMERA_ANALOGUE_GAIN_MDB` (default 30 dB). Build is just
`west build -p -b 'nucleo_n657x0_q//sb' apps/camera-app -d build/camera-app`.
135 KB in the 511 KB window, links at 0x34180400, 1.25 MB pool in
runtime-only AXISRAM1.

**NEXT (M2 — imx335 boot-timing race): CHARACTERIZED 2026-06-13, fix specified,
validation gated on physical flash-boot.** Chased it with the board on: built a
deferred-logging / fast-timing repro (`overlays/fastlog-repro.conf`,
`build/camera-app-fastlog`) and it streamed fine — the race did NOT reproduce
over SWD dev-boot. Reason: dev-boot only resets the M33 core; the board stays
powered, so the IMX335 is warm (rails + INCK already settled) when init runs.
The race is **cold-power-on only** (flash-boot), so logging speed was a confound,
not the cause. **Reproducing/validating the fix needs a real power cycle +
BOOT-jumper flash-boot — physical access only, can't be done remotely.** The
fix (generous post-reset settle + bounded retry on the first I2C batch in
`imx335.c`, both strictly safe) is code-reviewed and written up in
`docs/camera-bringup-debug-log.md` (§2026-06-13) but deliberately NOT applied
yet — applying blind = "shipping the change as the fix." Apply + validate it on
the next flash-boot session; only then revert the 4 DBGMARK driver markers.

### Prior state (2026-06-11 night) — still true

**CAMERA FULLY WORKING, end to end.** The board runs the Zephyr UVC
sample (`build/uvc` — known-good fallback binary, do not delete) and is a
standard USB webcam on atlas (`/dev/video0`); live browser stream via
ustreamer at `http://<host-lan-ip>:8090/stream` (LAN, ufw rule added; also via
Tailscale); on-demand AI scene description through hotchocolate's qwen3-vl.
M1 "first light" is DONE and then some.

The whole "silent boot" saga resolved as three stacked causes (full story:
`docs/camera-bringup-debug-log.md`, traps codified in `CLAUDE.md` §Debugging):
1. **Dead USB cable** — board logged fine all along; we debugged a ghost.
2. **Log-drop trap** — `CONFIG_LOG_PRINTK=y` + deferred 1 KB buffer silently
   ate ALL init-time output incl. the driver's own LOG_ERRs. Cure baked into
   `overlays/debug-logging.conf` (`CONFIG_LOG_MODE_IMMEDIATE=y`).
3. **Boot-timing race (REAL BUG, fix pending)** — fast boots hit the IMX335
   before it's ready → init bails → 0 controls → link-freq -ENOTSUP → capture
   aborts. Slow synchronous logging masks it. Proper fix = delay/retry in
   sensor init; until then every camera build needs `debug-logging.conf`.

**New canonical workflows (both committed):**
- **Flash = `scripts/swd-run.sh build/<dir>`** — BOOT1 jumper parked in dev
  boot; hard-reset + RAM load + VTOR/MSP/PC + run over ST-Link. No power
  cycles, no DFU, no signing. Hard-won: **only `-hardRst`** — a software
  `-rst` bricks AP1 until the next hardware reset. Serial-boot DFU
  (`preflight-flash.sh`) stays for flash-boot shipping only.
- **Webcam demo:** `scripts/cam-stream.sh` (ustreamer, apt-installed, :8090)
  and `scripts/cam-describe.sh ["prompt"]` (one frame → qwen3-vl → text; GPU
  used ONLY on demand — user explicitly wants no background AI).

**Working builds:** `build/uvc` (UVC webcam, VGA, 30 dB gain) ·
`build/cam-trace-clean` (capture sample + pixel-stats, 30 fps verified).
UVC needs the 1.25 MB pool configs to advertise 640x480 (else only 48x31).

**Temp edits in the pinned `zephyr/` tree (6 files, do NOT commit there):**
printk markers (video_common/video_ctrls/imx335/dcmipp/capture-main) + UVC
main.c (controls include + 30 dB ANALOGUE_GAIN default — superseded by
camera-app's Kconfig; revert the two sample files after camera-app passes on
hardware). Revert command + per-file list: `docs/camera-bringup-debug-log.md`.

**NEXT SESSION candidates (after the camera-app hardware verify above):**
(1) fix the timing race properly (then drop the sync-logging requirement +
revert the 4 driver markers; upstream-worthy), (2) white balance for
the green cast (DCMIPP pipeline config) + focus the lens ring, (3) GitHub
remote (user creates repo — do NOT add remotes unasked), (4) `sudo ufw delete`
the dead 8091 rule, (5) ustreamer as a systemd unit if the stream becomes
permanent.

---

### Previous session state (2026-06-11 morning, superseded)

**State then: board bring-up verified; camera build SILENT before the console — believed to be an early-init fault.** (Reality: dead cable + log drops, see above.)

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

(The "next session" plan from this state was executed the same evening — see CURRENT above.)

## DEFERRED

- Upstream PR to zephyr: `zephyr,sram = &axisram1` for nucleo_n657x0_q (mirror of DK commit). Do after first flash confirms the overlay on hardware.
- PYNQ-Z2 FPGA track: deliberately parked until the N6 camera milestone lands (user agreed 2026-06-10).
- NPU (Neural-ART) exploration: separate STM32Cube.AI/X-CUBE-N6 track — Zephyr has no NPU driver; don't look for one.

## HISTORY

(none yet — project started 2026-06-10)
