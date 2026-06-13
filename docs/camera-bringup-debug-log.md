# Camera bring-up debug log — DO NOT repeat these

Single source of truth for the IMX335 camera "silent boot" investigation so we
never re-run an experiment we already did. Read this before touching the camera.

**Status (2026-06-11 night): CAMERA STREAMS. 640x480 RGBP @ 30 fps, 1000+
frames, zero errors — with `CONFIG_LOG_MODE_IMMEDIATE=y`. The only change vs
the failing build was synchronous logging ⇒ the original failure is a
BOOT-TIMING RACE: with fast deferred logging, imx335 init hits the sensor too
early, bails, controls never register, link-freq → -ENOTSUP. Slow synchronous
logging spaces init out and everything works. Pixel-content not yet verified
(could be black frames) — `build/cam-trace-clean` now adds a 1/sec
`DBGMARK pixels: min/max/avg` printk; flash it next power cycle. After that:
pin down the exact race (rebuild with deferred logging + 32 KB log buffer to
see the original bail point at original timing) and fix it properly rather
than shipping the logging change as the fix.**

---

## 2026-06-13 — the race is COLD-BOOT-only; logging speed was a confound

Ran the exact experiment the 2026-06-11 status block asked for: built
`apps/camera-app` with **deferred logging + a 32 KB buffer** (so the DBGMARK
printks no longer block on the UART → original *fast* init timing, but nothing
is dropped) — `overlays/fastlog-repro.conf` + `build/camera-app-fastlog`. Flashed
it over SWD (warm dev-boot) and captured the boot from t=0.

**Result: the camera came up perfectly even with fast/deferred timing.**
`imx335_init` ran `ENTER → i2c-ready ok → all steps ok, calling init_controls`
(no BAIL), the sensor vdev registered all **3 controls**, gain applied,
`/dev/video0` streamed YUYV 640x480 @ 30 fps. The race did **NOT** reproduce.

**So the 2026-06-11 working theory was wrong about the masking variable.** It is
*not* "synchronous logging spaces init out." It's **sensor temperature**:

- `scripts/swd-run.sh` does a `-hardRst` (NRST), which resets the **M33 core**
  and reloads RAM — but the **board stays powered the whole time**. The IMX335's
  power rails and INCK input clock have been up (often for the whole session)
  by the time `imx335_init` runs. The sensor is *warm/ready*, so init's first
  I2C writes always succeed regardless of how fast init runs.
- The driver pulses the sensor's XCLR (`reset-gpios`, active-low) on **every**
  init (configure-active → 500 ns → release → 600 µs T4), so XCLR state is the
  same warm or cold. The cold variable is therefore **rail/INCK readiness**,
  not reset.
- The historical failure was on **flash-boot cold power-on** (BootROM → external
  OSPI chainload → app), where the sensor had *just* been powered and INCK/PLL
  had not settled when init hit it → an early I2C batch NAKs → init bails →
  controls never register → `video_get_csi_link_freq` returns -ENOTSUP → capture
  aborts. Slow logging happened to add enough delay to clear it, which is why
  immediate-logging "fixed" it — a side effect, not the mechanism.

**Consequence:** the race is **unreproducible over SWD dev-boot** — it needs a
genuine cold power-cycle, i.e. the flash-boot path (`preflight-flash.sh
build/<dir> --flash`) + BOOT-jumper moves + a physical power cycle. That can't be
done remotely (needs hands on the board). The fix below is therefore *specified
and code-reviewed but NOT yet applied/validated* — applying it blind would be
"shipping the change as the fix" without proof. **Apply + validate it on the
next session that has physical flash-boot access.**

### The fix to apply + validate at the next flash-boot

In `zephyr/drivers/video/imx335.c`, `imx335_init()`, harden the power-on so it
tolerates a cold/just-powered sensor. Two strictly-safe changes (each a no-op on
the warm path that already works, so they can't regress it):

1. **Longer, always-run post-reset settle.** The 600 µs T4 after XCLR release is
   marginal for a cold INCK/PLL. Give it a datasheet-generous settle (a few ms)
   so register access waits for the sensor's internal boot. Runs every init →
   validated by warm-boot still working.
2. **Bounded retry on the first sensor access.** Wrap the first
   `video_write_cci_multiregs(imx335_init_params, …)` in a retry loop: on
   `ret < 0`, `k_msleep(2)` and retry up to ~10×; only `return ret` after the
   budget is exhausted. This converges the instant the sensor is ready (zero
   added delay warm), and turns a cold NAK from a fatal bail into a short wait.
   It strictly dominates today's behavior — the new path executes *only* where
   the current code already hard-fails.

Validation gate (must pass before reverting the DBGMARK markers): flash-boot the
build with **deferred logging** (cold timing, no logging-speed crutch), power
cycle several times, and confirm every cold boot registers 3 controls + streams.
Only then drop `CONFIG_LOG_MODE_IMMEDIATE=y`'s load-bearing status for the race
and `git -C zephyr checkout` the 4 driver DBGMARK files
(`imx335.c` keeps the fix), restoring a clean upstream-diff (the fix itself is
upstream-worthy: a PR hardening IMX335 cold-boot init).

---

## TL;DR — start here next session

1. Power cycle → `scripts/preflight-flash.sh build/cam-trace-clean --flash`.
2. Capture console with the **reliable** method (see "Serial capture" below).
3. The build logs synchronously (nothing dropped): read the boot output from
   t=0 — the driver's own `LOG_ERR`s plus `DBGMARK imx335_init` step markers
   show exactly where sensor init bails before registering controls.

## 2026-06-11 BREAKTHROUGHS — read before trusting older sections

1. **The whole "silent boot" mystery was a bad USB cable** (plus the flaky
   serlog3.py logger). With a good cable + plain `cat` capture, every camera
   build prints full logs. All hang hypotheses (memory/clock/POST_KERNEL) were
   chasing a ghost; older sections below are kept only for the ruled-out table.
2. **Real failure chain (proven by instrumented runs):** sample main() runs →
   set_fmt I2C writes to the sensor SUCCEED (sensor + bus fine) → stream start
   → `stm32_dcmipp_conf_csi` → `video_get_csi_link_freq(camera@1a)` → both
   LINK_FREQ and PIXEL_RATE `video_get_ctrl` return -134 (-ENOTSUP) → "Failed
   to retrieve source link-frequency" → capture aborts. `video_find_ctrl` sees
   the imx335 vdev (0x341935b4) with **0 controls, src_dev=NULL** —
   `imx335_init_controls` never completed.
3. **LOG-DROP TRAP — invalidates "marker didn't print → code didn't run" for
   init-time code.** The sample config has `CONFIG_LOG_PRINTK=y` +
   `CONFIG_LOG_MODE_DEFERRED=y` + 1 KB log buffer + VIDEO_LOG_LEVEL_DBG. So
   printk is NOT synchronous here: it's rerouted into the deferred buffer,
   which the I2C debug dumps overflow instantly → `--- 58 messages dropped ---`
   ate ALL boot-time output, including driver `LOG_ERR`s and our markers.
   Fix: `overlays/debug-logging.conf` (`CONFIG_LOG_MODE_IMMEDIATE=y`), now
   baked into `cam-trace-clean`. Build args:
   `west build -b nucleo_n657x0_q//sb -d build/cam-trace-clean zephyr/samples/drivers/video/capture -- -DEXTRA_DTC_OVERLAY_FILE=.../overlays/nucleo_n657x0_q_vidpool.overlay -DEXTRA_CONF_FILE=.../overlays/debug-logging.conf`
   The UVC webcam sample (`build/uvc`, superseded by `apps/camera-app` on
   2026-06-12) was built the same way plus
   `--shield st_b_cams_imx_mb1854 -DCONFIG_VIDEO_BUFFER_POOL_HEAP_SIZE=1250000
   -DCONFIG_VIDEO_BUFFER_POOL_ZEPHYR_REGION=y
   -DCONFIG_VIDEO_BUFFER_POOL_ZEPHYR_REGION_NAME='"AXISRAM1"'`
   (the 1.25 MB pool is what makes it advertise 640x480 instead of 48x31).
4. Parked (separate, later): `<err> iocell: HSLV configuration for "vddio3"
   blocked by OTP fuse` at t=0 — CSI pin IO-voltage mode; may matter for real
   MIPI capture *after* the link-freq bug is fixed.

---

## What is PROVEN

- **Board boots fine.** `hello_world` runs via BOTH flash-boot (banner in run
  mode) and serial-boot (`build/hello-sb`, banner + loop). Toolchain, signing,
  cables, jumpers, power all good.
- **Any build with the camera shield is totally silent** — no banner, no logs,
  no fault dump — over the same `usart1`→`ttyACM0` console that hello uses.
  Confirmed by direct 115200 read, not just the logger.
- **Boot order** (`zephyr/kernel/init.c` `bg_thread_main`):
  `POST_KERNEL` device init (line ~302) → `boot_banner()` (line ~311) →
  `APPLICATION` init (line ~318) → `main()`. So a hang in any POST_KERNEL
  device suppresses the banner and everything after.
- **Console (`uart_console`) inits at the END of `PRE_KERNEL_1`** — before any
  camera device. (From the init-table dump in the .map.)
- **Camera drivers init at `POST_KERNEL`:** order is
  `log_core → malloc → gpio_hogs → i2c_stm32 ×3 → imx335 → dcmipp ×4 → shell_uart`.
- **DCMIPP init sequence** (`drivers/video/video_stm32_dcmipp.c` `stm32_dcmipp_init`):
  enable clocks → `reset_line_toggle` dcmipp → `reset_line_toggle` csi →
  `irq_config` → **`HAL_DCMIPP_Init()`** → `device_is_ready(source_dev)` (line ~1729,
  returns -ENODEV cleanly if the sensor isn't ready).

## What is RULED OUT (do not re-investigate)

| Suspect | Verdict | Evidence |
|---|---|---|
| Memory layout / vidpool / FLEXRAM | **NOT it** | pool region is `(NOLOAD)` in linker.cmd → C-startup never touches it |
| Clock tree (DCMIPP/CSI) | **NOT it** | PLL1 + `ic17` (DCMIPP-ker) + `ic18` (CSI-PHY 27MHz) all enabled on Nucleo, identical to the DK. DK's extra `pll2/pll4/ic4/ic10/ic16` feed its display/venc, NOT the camera |
| Jumpers / power / CN9 | **NOT it** | DFU enumerates every flash (proves serial-boot pins correct); board powers + enumerates fine |
| IMX335 sensor | **NOT it (likely)** | deferring `imx335` init (`overlays/defer_imx335.overlay`) did NOT restore output. NOTE: confounded by device-deps (dcmipp lists imx335 as source) — the clean re-test is `cam-trace-clean` |
| Shell output buffering | **NOT it** | rebuilt with `CONFIG_SHELL=n` (`cam-trace-noshell`) → still silent |
| SWD debugger to read the fault | **Unavailable** | serial-boot locks the debug port: "No STM32 target found / Debug Authentication". `--connect port=SWD mode=hotplug` fails |

## Builds tried (all in `build/`, persist on disk; gitignored)

| Build dir | Config | Result |
|---|---|---|
| `hello-sb` | hello, no shield | **prints** (baseline good) |
| `t3-cam-vidpool`, `cam-dbg` | capture + vidpool, shell ON, `VIDEO_LOG_LEVEL_DBG` | silent |
| `cam-defer-sensor` | + `defer_imx335`, shell ON | silent |
| `cam-dcmipp-trace` | + `defer_imx335` + dcmipp printk markers, shell ON | silent (markers never showed — shell buffering suspected then) |
| `cam-trace-noshell` | + `defer_imx335` + markers, **shell OFF** | silent (ruled out shell buffering) |
| **`cam-trace-clean`** | vidpool + dcmipp markers + **boot probes**, **shell OFF**, imx335 NOT deferred | **BUILT, signed, NOT YET FLASHED ← next test** |

## The probe build (`cam-trace-clean`) — what each outcome means

It prints `DBGMARK` lines via `printk` (shell off, so output is immediate/direct):

- **`DBGMARK boot: PRE_KERNEL_2 alive`** prints? → console works in a camera
  build; the hang is somewhere in `POST_KERNEL`. If this is ABSENT, the console
  itself is broken in camera builds (look earlier than POST_KERNEL — something
  the shield changes at PRE_KERNEL_1, or the output path).
- **`DBGMARK dcmipp: enter / clocks ok / reset_dcmipp ok / reset_csi ok / irq ok /
  HAL_DCMIPP_Init returned N`** — the LAST one printed = the exact step that
  hangs. Prime suspect is `HAL_DCMIPP_Init` (ST HAL polling a hardware bit).
  If we never see `dcmipp: enter`, the hang is BEFORE dcmipp — in `gpio_hogs`
  (camera-power hog PA0) or `i2c_stm32` (csi_i2c bus init).
- **`DBGMARK boot: APPLICATION alive`** prints? → boot got all the way past
  POST_KERNEL; the camera init did NOT hang — the problem is in `main()` /
  streaming, a different (easier) class of bug.

## Leading hypothesis

Hang is in a `POST_KERNEL` camera step before the banner — most likely
`HAL_DCMIPP_Init` polling forever, OR the `gpio_hogs`/`i2c` step just before
dcmipp. Upstream has **no capture config for `nucleo_n657x0_q`** (only the DK),
so this board's camera path is essentially first-run / untested upstream.

## Temporary debug instrumentation (applied, in the pinned `zephyr/` tree)

These are NOT committed and must be reverted once debugging concludes
(`git -C zephyr checkout drivers/video/video_common.c drivers/video/video_ctrls.c drivers/video/imx335.c drivers/video/video_stm32_dcmipp.c samples/drivers/video/capture/src/main.c samples/subsys/usb/uvc/src/main.c`):

0. `samples/subsys/usb/uvc/src/main.c`: +video-controls.h include and a
   30 dB ANALOGUE_GAIN default (IMX335 defaults to 0 dB = near-black indoors).
   **SUPERSEDED 2026-06-12 by `apps/camera-app`** (gain is the Kconfig
   `APP_CAMERA_ANALOGUE_GAIN_MDB` there). Revert this sample edit once
   camera-app is verified on hardware — kept until then so the known-good
   `build/uvc` can be rebuilt bit-identical if the app misbehaves.

1. `drivers/video/video_stm32_dcmipp.c` `stm32_dcmipp_init`: `printk("DBGMARK
   dcmipp: ...")` after enter / clocks / reset_dcmipp / reset_csi / irq / HAL.
2. `samples/drivers/video/capture/src/main.c`: two `SYS_INIT` probes printing
   `DBGMARK boot: PRE_KERNEL_2 alive` and `DBGMARK boot: APPLICATION alive`.
3. `drivers/video/video_common.c` `video_get_csi_link_freq`: `DBGMARK linkfreq`
   printks (src/bpp/lanes, LINK_FREQ ret, PIXEL_RATE ret, result).
4. `drivers/video/video_ctrls.c` `video_find_ctrl` + `video_init_ctrl`:
   `DBGMARK findctrl` / `DBGMARK initctrl` printks (vdev, ctrl ids, counts).
5. `drivers/video/imx335.c` `imx335_init`: `DBGMARK imx335_init` step markers
   (ENTER, i2c-ready, BAIL at each early return, "all steps ok").

`build/cam-trace-clean` already has these baked in — flash it directly; no
rebuild needed (a `-p` rebuild after a revert would lose the markers).

## Serial capture — the method that WORKS

```sh
# reliable: harness/background cat (NOT serlog3.py, which hit exit-144 / no file)
sg dialout -c "stty -F /dev/ttyACM0 115200 raw -echo; cat /dev/ttyACM0 > /tmp/x.log"
# run as a background task BEFORE the flash; the firmware prints within ms of jump
```

`serlog3.py` + `setsid/disown` was unreliable this session (exited 144, often
never created the log file). Prefer the plain `cat` above.

## Hard-won serial-boot facts (also in N6-FACTS.md)

- One DFU push per power-up. A **refused** download (wrong link address) ALSO
  consumes the session and zombifies it → next push segfaults (exit -11).
- Always flash via `scripts/preflight-flash.sh build/<dir> --flash` (checks
  link address 0x34180400 / fit / signed / DFU-armed before pushing).
