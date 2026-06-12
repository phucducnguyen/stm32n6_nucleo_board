# STM32N6 / NUCLEO-N657X0-Q — verified facts

One-stop reference so we never re-research this. Sources: ST primary docs + Zephyr upstream,
cross-checked against our own hardware experiments on atlas (2026-06-10). Facts marked
**[verified on our board]** were proven by experiment here, not just read.

## Primary documents

- **UM3417** — NUCLEO-N657X0-Q board manual (MB1940): <https://www.st.com/resource/en/user_manual/um3417-stm32n6-nucleo144-board-mb1940-stmicroelectronics.pdf>
- **UM3234** — How to proceed with boot ROM on STM32N6: <https://www.st.com/resource/en/user_manual/um3234-how-to-proceed-with-boot-rom-on-stm32n6-mcus-stmicroelectronics.pdf>
- ST article "STM32N6 boot ROM explained": <https://community.st.com/t5/stm32-mcus/stm32n6-boot-rom-explained/ta-p/763648>
- Schematics (C02): <https://www.st.com/resource/en/schematic_pack/mb1940-n657x0q-c02-schematic.pdf>
- Zephyr board doc: <https://docs.zephyrproject.org/latest/boards/st/nucleo_n657x0_q/doc/index.html>
- ST BSP pin truth: <https://github.com/STMicroelectronics/stm32n6xx-nucleo-bsp> (`stm32n6xx_nucleo.h`)

## Boot architecture (the single most important section)

STM32N657 has **no internal flash**. The on-chip BootROM always runs first and loads a
**signed** FSBL image into RAM, from one of:

| Mode | BOOT0 (JP1) | BOOT1 (JP2) | What happens |
|---|---|---|---|
| **Flash boot** (run) | 0 | 0 | BootROM copies FSBL from external octo-SPI flash into AXISRAM2 and runs it |
| **Serial boot** | 1 | 0 | BootROM enumerates **USB DFU on CN8** (`0483:df11`), accepts one image into AXISRAM2, runs it from RAM |
| **Dev boot** | x | 1 | Debug access open; ST-Link can flash/debug. App does NOT auto-run |

- **BOOT1 has priority over BOOT0** (BOOT1=1 → dev boot regardless of BOOT0).
- Jumper position 1 (the **printed/silkscreened** side) = logic 0; position 2 (unprinted) = logic 1. **[verified on our board]**
- **FSBL load window: AXISRAM2 at `0x3418'0400`, max 511 KB — for BOTH flash boot and serial boot. Not configurable.**
  The BootROM refuses DFU downloads targeting any other address ("failed to download Sector[0]") **[verified on our board]**;
  an image *linked* elsewhere but downloaded into the window crashes silently (no UART, nothing) because the vector
  table/code expect the wrong addresses. This killed our `zephyr,sram = &axisram1` overlay approach.
- **Signing is mandatory** even for non-secure dev: `STM32_SigningTool_CLI -in zephyr.bin -nk -t fsbl -hv 2.3` (Zephyr runs
  this automatically post-build *iff* the tool is on PATH; if missing you get only a CMake *warning* and an unsigned,
  unbootable image — classic trap, see zephyr issue [#96265](https://github.com/zephyrproject-rtos/zephyr/issues/96265)).
- **Run/flash-boot mode closes the debug access port** (BootROM behavior). A healthy running app that's unreachable over
  SWD is NORMAL on N6. Debug needs dev boot. **[verified on our board]**
- **Red user LED (PG10) doubles as the BootROM BOOTFAILED output (AF11)** — if boot fails in run mode, the red LED is the
  only indicator. (ST "boot ROM explained" article.)
- **System reset wipes AXISRAM1/AXISRAM2** — RAM-loaded images don't survive RESET; use core reset when debugging.

### Serial-boot (DFU) session rules — hard-won

- One download per power-up. After a successful `--start`, DFU is gone until power cycle.
- **RESET button does NOT re-arm DFU. Only a full power cycle (both cables out) does.** **[verified on our board]**
  Upstream Zephyr agrees: their CI hardware map runs `board_power_reset.sh` before *every* `//sb` flash.
- A **failed** download leaves the BootROM listening, but the session can go **zombie**: `lsusb` still shows `0483:df11`
  while the device is actually dead — `STM32_Programmer_CLI` then **segfaults (exit -11)** right after the
  "File segment @0x00000001 is not 4-bytes aligned" notice. That message is cosmetic (0x1 is the N6 download-modifier,
  not an address); the segfault is the zombie device. Recovery: power cycle. **[verified on our board]**
- A **refused** download — image linked outside the `0x34180400` window, so the BootROM rejects it with
  "failed to download Sector[0]" — **also consumes the one-shot session and zombifies it.** The *next* push then
  segfaults (exit -11), even though that next image is fine. So a single bad build costs *two* power cycles if you
  don't catch it. **[verified on our board 2026-06-11]** Mitigation: `scripts/preflight-flash.sh` checks the link
  address (and signed-bin/fit/DFU-armed) **before** pushing, so a non-loadable image never reaches the BootROM.
- DFU device check before any push: `lsusb | grep df11`. CN8 must be plugged directly (no hub).
- **Never `west flash` bare. Use `scripts/preflight-flash.sh build/<dir> --flash`** — it gates on all of the above.

## Board hardware (UM3417)

- **CN10** = STLINK-V3EC USB-C: debug + VCP + default board power. **CN8** = user USB-C (USB2 HS, DRP): serial-boot DFU.
  With default power config (**CN9 jumper [1-2]** = 5V from ST-Link) you need **both cables** for serial boot:
  CN10 powers + console, CN8 carries the image.
- **VCP = USART1, TX PE5 / RX PE6 (AF7)**, 115200 8N1. On atlas it's `/dev/ttyACM*` — the number **renumbers on
  replug** (ACM0↔ACM1); always glob for the newest. **[verified on our board]**
- **External flash**: Macronix **MX25UM51245G** (512 Mbit octo-SPI, XSPI2) memory-mapped at `0x70000000`.
  CubeProgrammer external loader: `MX25UM51245G_STM32N6570-NUCLEO.stldr`. Never "full chip erase" (no internal flash).
- **Camera**: **CN6**, 22-pin ZIF FFC, MIPI CSI-2 2-lane (RPi-Zero pinout). Control I2C2 (PB10/PB11), camera reset PO5,
  power-enable PA0, VDD_CAM 3V3. **B-CAMS-IMX plugs in directly, no jumper/solder-bridge prerequisites.**
- LEDs: LD1 green = 5V_PWR ok · LD2 green = VBUS on CN8 · LD4 tricolor = ST-Link status · user LEDs blue PG8 / **red PG10
  (=BOOTFAILED)** / green PG0.

## Memory map (what Zephyr sees)

`dts/arm/st/n6/stm32n657X0.dtsi`: parent node `axisram12@24000000` (non-secure alias; `ranges` remaps children to
secure `0x34xx_xxxx`):

| Node | CPU address | Size | Boot state |
|---|---|---|---|
| `axisram1` (= 400K FLEXRAM + 624K AXISRAM1 + 512K of AXISRAM2) | 0x34000000 | ~2 MB span | live at reset, no clock init needed |
| `axisram2` (FSBL window) | **0x34180400** | **511 KB** | live at reset; **the only place BootROM loads to** |
| AXISRAM3–6 (RAMCFG-gated) | 0x34200000 / 0x34270000 / 0x342E0000 / 0x34350000 | 4 × 448 KB (**contiguous 1792 KB**) | **disabled by default**; enable DT child node → `drivers/misc/stm32n6_axisram` powers them at PRE_KERNEL_2 (multi-region bug fixed in v4.4.0, PR [#104984](https://github.com/zephyrproject-rtos/zephyr/pull/104984)) |

Nucleo board dts: `chosen zephyr,sram = &axisram2` for **both** targets — i.e. **every Nucleo image must fit 511 KB**.
Only the DK's *chainloaded* (MCUboot) variant runs from `axisram1`; the Nucleo has no MCUboot variant yet
(open PR [#103164](https://github.com/zephyrproject-rtos/zephyr/pull/103164)).

## Zephyr specifics

- Two board targets: `nucleo_n657x0_q` (default: ST-Link → external NOR, persistent, **needs dev boot to flash**, flash+run
  jumper dance) and `nucleo_n657x0_q//sb` (serial boot: USB DFU push to RAM, fast loop, lost on power-off).
- Runner args (board.cmake): sb = `--port=usb1 --download-modifiers=0x1 --start-modifiers=noack`;
  default = `--port=swd mode=HOTPLUG ap=1` + the .stldr + `--download-address=0x70000000`.
- `west flash` sends `zephyr.signed.bin` automatically (runner fix PR [#86679](https://github.com/zephyrproject-rtos/zephyr/pull/86679), in ≥ v4.1).
- **Video buffer pool outside system RAM** is the upstream pattern: `CONFIG_VIDEO_BUFFER_POOL_ZEPHYR_REGION=y` +
  `_NAME="<region>"` (PR [#100628](https://github.com/zephyrproject-rtos/zephyr/pull/100628); pool heap is initialized
  lazily on first alloc). The DK does exactly this with region `"PSRAM"` (external PSRAM via its board Kconfig.defconfig).
  The Nucleo has no PSRAM → our analog is a named on-chip region (`"AXISRAM1"`, or AXISRAM3–6 once enabled).
  **Upstream has NO capture-sample conf for the Nucleo at all** — our working config is upstream-worthy.
- IMX335 driver limits (PR [#88825](https://github.com/zephyrproject-rtos/zephyr/pull/88825)): 10-bit, 2-lane,
  24 MHz fixed clock only. DCMIPP driver handles Bayer→RGB (`video_stm32_dcmipp.c`).
- The B-CAMS-IMX shield (`st_b_cams_imx_mb1854`) requires DT labels `csi_i2c`/`csi_interface`/`csi_capture_port`/
  `csi_connector` — the Nucleo defines all of them; sensor at I2C addr 0x1a.
- STM32CubeProgrammer **≥ 2.18** required for N6 (we run 2.22.0, user-space at `~/STMicroelectronics/STM32CubeProgrammer`).

## Console & logging traps **[verified on our board 2026-06-11]**

- **`CONFIG_LOG_PRINTK=y` (the Zephyr default when LOG is on) makes printk
  asynchronous**: it goes into the deferred log buffer, NOT straight to the UART.
  With the video sample's 1 KB buffer + `VIDEO_LOG_LEVEL_DBG` I2C dumps, ALL
  boot-time messages (driver `LOG_ERR`s included) are overwritten before the log
  thread drains them → `--- 58 messages dropped ---`. Absence of output proves
  NOTHING about whether code ran. Cure: `overlays/debug-logging.conf`
  (`CONFIG_LOG_MODE_IMMEDIATE=y`) — synchronous, lossless, slower boot.
- **A dead USB cable produces exactly the same symptom as a firmware hang**
  (zero console output while DFU/power LEDs look normal — the bad cable still
  carried enough for some enumeration). Burn-in lesson: prove cable + console
  with a known-good image (`build/hello-sb`) before forming firmware theories.
- **Boot speed is load-bearing**: the IMX335 camera bring-up failed ONLY with
  fast (deferred-log) boots — sensor init ran before the sensor was ready, bailed,
  left 0 controls registered → `video_get_csi_link_freq` -ENOTSUP → capture
  abort. Synchronous logging slows POST_KERNEL enough to mask it. The camera
  streamed 640x480 RGBP @ 30 fps once logging was immediate. Proper fix (delay/
  retry in sensor init) pending — see `docs/camera-bringup-debug-log.md`.

## Canonical workflows on atlas **[verified on our board]**

Serial-boot dev loop (fast, RAM-only):
1. Jumpers: BOOT0 → pos 2 (unprinted), BOOT1 → pos 1 (printed). Both cables (CN10 + CN8).
2. Power cycle (both cables out → in). Confirm `lsusb | grep df11`.
3. `west flash -d build/<dir>` (PATH must include `.venv/bin` and CubeProgrammer `bin/`).
4. Console on newest `/dev/ttyACM*` 115200. One push per power cycle.

Flash-boot (persistent demo):
1. Dev boot (BOOT1 → pos 2), power cycle, `west flash -d build/<dir>` over ST-Link.
2. Run: BOOT0+BOOT1 → pos 1, power cycle. Debug port now closed = normal. Red LED = boot failed.
