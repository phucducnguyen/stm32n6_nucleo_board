# Occupancy path: USB webcam to a shippable local occupancy sensor

Written 2026-10-26. Goal: a NUCLEO-N657X0-Q + B-CAMS-IMX node that counts people on the
NPU, emits only counts, can **prove on the device** that no image leaves the MCU, and has a
**measured** people-count accuracy against a hand tally. Tools: `host/occupancy-eval/`.

Status words: *proposed* (written down), *implemented* (code exists), *tested* (its own tests
pass), *verified* (the repo's own check passed on the committed change, on the board where the
claim is about the board).

## The finding that sets stage 1

The people-detect build that carries the DET emitter (`../st-people/build/Project.elf`,
built 2026-06-25) **still streams video over USB**. The ST Makefile forces
`SCR_LIB_SCREEN_ITF ?= UVCL` on Nucleo boards, and `Lib/screenl/scr_lib.mk` offers only
`LTDC`, `UVCL` or `SPI`: there is no "no screen" option. The privacy judge shows it:

```
target: ../st-people/build/Project.elf  (1519 defined symbols, kconfig: no)
  FAIL P1 no-usb-device  HAL_PCDEx_ActivateLPM, HAL_PCDEx_SetRxFiFo, ...
  FAIL P2 no-uvc  UVCL_AbortOnFlyCtx, UVCL_ComputedwMaxVideoFrameSize, UVCL_DataIn, UVCL_IRQHandler, UVCL_Init, UVCL_SendPacket, ...
  PASS P6 det-emitter  DetEmit_Frame
VERDICT: FAIL image egress path or emitter check failed
```

So "the image never leaves the device" is not true of any image built so far. The fix is
small: `host/occupancy-eval/firmware/st-people-no-screen.patch` adds
`SCR_LIB_SCREEN_ITF=NONE`, a 30-line stub screen backend (`scrl_none.c`). Built 2026-10-26 on a
scratch copy of `../st-people` (ARM GNU 13.3, `make BOARD=NUCLEO-N657X0-Q
SCR_LIB_SCREEN_ITF=NONE`, unsigned, sha256 `348cbce3...0f9d58`), it links with
75.05 % of AXISRAM1_2_S and passes every static claim:

```
  PASS P1 no-usb-device
  PASS P2 no-uvc
  PASS P3 no-network
  PASS P4 no-pixel-out
  PASS P5 no-storage
  PASS P6 det-emitter  DetEmit_Frame
VERDICT: PASS no image egress path linked
```

The app's display thread waits only on its own `disp.update` semaphore, not on a screen
callback, so the stubs should not stall the pipeline; that is unverified until stage 2.

## The path (numbered stages, each with its gate)

| # | Stage | Gate (the check that says done) | Effort | Reuses | Needs the board |
|---|---|---|---|---|---|
| 0 | Privacy judges + count scorer + no-screen backend | `privacy_judge.py` FAILS `build/uvc` and the current st-people ELF, PASSES the no-screen ELF; 32 unit tests OK; Gavel prover PROVEN with 2 known-bad cases | done 2026-10-26 (*tested*) | occbridge parser, the st-people fork, ARM toolchain | no |
| 1 | Make the no-screen build the default occupancy image | Patch moved into `patches/` (listed in `patches.yml`), `scripts/build-people-detect.sh` passes `SCR_LIB_SCREEN_ITF=NONE`, then runs `privacy_judge.py --elf ../st-people/build/Project.elf` and refuses to sign on exit 1; judge entry added to `scripts/judges.yaml` | 0.5 day | everything in stage 0; `build-people-detect.sh` | no |
| 2 | Aim, then seal, then first light | (a) aim the camera with the webcam image (`build/camera-app`, the install tool); (b) flash the sealed image; (c) on the board: `lsusb` shows only the ST-Link `0483:3754` (no UVC device on CN8), 10 min of console captured host-stamped, `uart_log_judge.py` PASS, `python -m occbridge --device /dev/ttyACM0 --dry-run` emits events, a 5-walk in/out test moves the count 0 to 1 and back each time, SWD connect refused in run mode | 0.5 day | `flash-people-detect.sh`, BOOT jumper recipe (`docs/N6-FACTS.md`), occbridge | **yes** (jumpers, flashing, walking) |
| 3 | First accuracy measurement | `count_score.py capture.log tally.csv` over >= 60 labelled minutes in >= 3 windows with >= 20 minutes at 2+ people: MEET, or a measured MISS that names the failing metric | 1 day (about 1.5 h of a person tallying) | the scorer, a phone timer | **yes** (plus one human) |
| 4 | Only if stage 3 misses: cheapest knobs, then the model | Re-run stage 3 on fresh windows: MEET | knobs 0.5 day; model swap 1 to 2 days | ST model zoo `st_yolo_x_nano` (SLA0044) at 256 or 320, already sized in the venture step-1 research | yes |
| 5 | Transport for the pilot | Events reach the atlas event spine from the node through `occbridge` over the ST-Link USB serial; 24 h soak with heartbeat uptime >= 99 % | 0.5 day | occbridge spine client (fail-open), heartbeat events | yes (24 h powered) |
| 6 | Weekly report from real events | `host/usage-report` renders a week (or a bench day) from the stage 5 events; the tally note from stage 3 is printed on it | 0.5 day | usage-report (71 tests) | no |

Shortest path to a pilot-ready node: **about 3 working days**, of which about 1.5 days need the
board or a person in the room. Stages 0 to 1 are desk work and stage 0 is done.

### Timeline challenges

- **Model swap (the plan's "later, after TinyYOLOv2 is measured", easily a week).** Days version:
  try the knobs first (detection threshold, NMS, tracker on/off, mount height and angle: a
  shelf at 2 m angled down beats a desk-height view for occlusion). Only on a measured miss,
  generate YOLOX-Nano 256 with ST's model-zoo deploy service and swap the post-process type;
  the app already has the YOLOX path for the DK flavour. 1 to 2 days, gated on stage 3 data.
- **Ethernet (M5 N1 to N4, a week of plumbing).** Not on the pilot path. A Nucleo powered from
  any small host already has a transport: the ST-Link VCP into `occbridge`. Ethernet also
  re-opens claim P3 (see "What the claims do not cover"); defer it until a pilot asks for
  cable-free installs.
- **"Don't start M(n+1) before M(n) passes on hardware."** Keep it, but stages 1, 5 (code side)
  and 6 are hardware-free and can run before stage 2.
- **The one-week pilot** (`validation-plan.md` section 4) is calendar time, not effort. Stage 3
  makes the pilot's spot-check a re-measurement instead of the first measurement.

## Privacy claims proven on the device

The claim a buyer can check: **in the occupancy build, no image bytes can leave the MCU;
the only egress is a 115200-baud console line of integers.**

| Id | Claim | How it is checked | Where | Status |
|---|---|---|---|---|
| P1 | No USB device controller or stack is linked (`HAL_PCD_*`, `usbd_*`, `udc_*`, USBX `ux_device*`) | `privacy_judge.py --elf <image>` | host, on the built ELF | *tested* (FAILS uvc, camera-app, current st-people; PASSES no-screen) |
| P2 | No USB video class (`UVCL_*`, `uvc_*`) | same | host | *tested* |
| P3 | No network or Ethernet transmit path | same (FAILS `build/net-dhcp`) | host | *tested* |
| P4 | No display, JPEG encoder or pixel-pushing panel driver (LTDC, ili9341 SPI, `HAL_JPEG_*`) | same | host | *tested* on synthetic symbols; no real LTDC or SPI build on disk |
| P5 | No filesystem or SD write path | same | host | *tested* on synthetic symbols |
| P6 | The image is the occupancy build (`DetEmit_Frame` linked), so a clean "hello" cannot stand in for it | same | host | *tested* |
| K1 | Zephyr builds: no `CONFIG_UDC_DRIVER`, `CONFIG_USB_DEVICE_STACK*`, `CONFIG_USBD_VIDEO_CLASS`, `CONFIG_NETWORKING`, `CONFIG_DISPLAY`, `CONFIG_FILE_SYSTEM`, `CONFIG_SHELL` | same, reads `.config` beside the ELF | host | *tested* (the ST Makefile build has no `.config`; K1 is skipped there) |
| U1-U7 | The console carries only valid DET lines and short status text: printable ASCII, no 48+ char encoded run, non-DET bytes under 1 % after the first DET line | `uart_log_judge.py capture.log` | host, on a captured log | *tested* on the bridge sample log and known-bads |
| B1 | The board enumerates no USB device of its own (CN8 silent; only the ST-Link) | `lsusb` before and after power-up | **board** | *proposed* (stage 2) |
| B2 | Run mode closes the debug port | SWD connect fails in run mode (`docs/N6-FACTS.md` says verified for other images) | **board** | *proposed* (stage 2) |
| B3 | The flashed image is the judged image | sha256 of the signed bin recorded beside the judge output; reproducible rebuild from the tagged tree gives the same hash | host + board | *proposed* |

The webcam build is the known-bad: `build/uvc/zephyr/zephyr.elf` FAILS P1, P2, P6 and K1
(`CONFIG_UDC_DRIVER=y, CONFIG_USBD_VIDEO_CLASS=y, CONFIG_USB_DEVICE_STACK_NEXT=y`).

### What the claims do not cover (red-team notes)

- **The console is a real channel.** A firmware written to leak could `printf` pixels. U5 and
  U6 catch a hex, base64 or spaced-decimal dump; they cannot catch pixels hidden inside valid
  box tuples (16 boxes x 5 fields is about 100 bytes a line, enough for a small thumbnail in
  seconds). The fix is a count-only protocol line (for example `CNT <seq> <ts_ms> <count>`,
  which needs a `PROTOCOL.md` and parser change), judged with `uart_log_judge.py --count-only`.
  *proposed.* Zone and queue counts would then be computed on the device, not on the host.
- **Physical access.** Moving BOOT1 to dev boot opens SWD, and a debugger can read the frame
  buffer in RAM. A shipped unit needs the debug port closed for good (an OTP / lifecycle step,
  irreversible: an owner decision, not part of this path).
- **Pixels still enter RAM.** The camera, DCMIPP and the overlay buffers are live; "the frame
  stays on the board" means it is overwritten in RAM, never stored or sent.
- **Ethernet.** A networked build must link a network stack, so P3 would be replaced by a
  narrower claim (one socket, fixed event schema, size cap). Not designed yet.
- The judge reads symbol names. A renamed or inlined driver could hide; that is why the judge
  also requires the expected emitter and why B1 (nothing enumerates) is checked on the board.

## Accuracy target

**Metric.** Per labelled minute mark, the device count (median of the held DET count, sampled
once a second over the mark +/- 10 s) against the person's tally at that mark. The emitter
sends on a change plus a 1 s keepalive, so the stream is a step function; a stream silent for
more than 3 s is *missing*, never zero.

**Target (proposed until stage 3 measures it).** All must hold:

| Metric | Target | Why |
|---|---|---|
| coverage (marks with live data) | >= 95 % | the report must disclose gaps, not score them as empty |
| count MAE | <= 0.5 people per minute | at 0 to 6 people a percent is unstable; MAE is the honest unit |
| exact-count rate | >= 70 % | |
| within-one rate | >= 95 % | "obviously wrong while the manager watched" = off by 2+ |
| WAPE (sum of absolute errors / sum of tally) | <= 15 % | the strict end of the validation plan's +/- 15-20 % trust bar |
| occupied-state accuracy (count >= 1 agrees) | >= 95 % | drives the report's utilisation number |
| peak shortfall (tally at device's busiest minute vs tally peak) | <= 1 | "nails when it is busy" |

Dataset minimum: >= 60 labelled minutes across >= 3 windows, >= 20 of them with 2+ people;
smaller sets return INSUFFICIENT (exit 3), never a pass.

**Dataset protocol (privacy-safe).** No image is stored or viewed. One person sits where they
can see the sensor's field of view, a phone timer beeps each minute, and they write
`minute,count` (people in view at the beep). The host stamps each console line with its own
clock (or a `--anchor` pairs one board `ts_ms` with the wall clock). Windows: a busy period, a
quiet period and one with people standing close together (occlusion is where TinyYOLOv2 at
224 px will under-count). Because no frames are kept, a new model needs fresh windows; compare
candidates on the same day and space.

**Known limits.** TinyYOLOv2 on the Nucleo has no published person accuracy (venture step-1
research: "not captured"), and the emitter caps a frame at 16 boxes, so counts above 16
under-count by design.

## Commands

```
cd host/occupancy-eval
python3 -B -m unittest discover -s tests -v                    # 32 tests
python3 -B privacy_judge.py --elf ../../build/uvc/zephyr/zephyr.elf   # known-bad: exit 1
python3 -B privacy_judge.py --elf ../../../st-people/build/Project.elf
python3 -B uart_log_judge.py [--count-only] capture.log
python3 -B count_score.py capture.log tally.csv [--anchor TS_MS=ISO]
```

## Needs the owner or the board

1. Stage 1 edits `patches/`, `patches/patches.yml`, `scripts/build-people-detect.sh` and
   `scripts/judges.yaml` (another work stream owns `scripts/` today).
2. Stage 2 jumper moves and flashing (`scripts/flash-people-detect.sh`), and the walk test.
3. Stage 3: one person for about 1.5 hours of tallying across three windows.
4. Later, not on this path: permanently closing the debug port (irreversible).
