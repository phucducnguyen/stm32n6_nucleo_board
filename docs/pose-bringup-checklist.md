# N6 Pose Bring-up Checklist — desk posture, Step 1 (de-risk)

**Goal:** confirm MoveNet runs **camera → NPU → 17 keypoints** on *this* board and tracks
your upper body well enough for a slouch metric — *before* writing any custom firmware.

This is the de-risk spike for the "desk posture coach" build. It flashes **ST's turnkey
getting-started app**, not our Zephyr `camera-app` — so it temporarily sets our firmware
aside. Everything here is **100% on-device**: camera → NPU → keypoints on your own screen.
No frames leave the board; nothing hits the network or any GPU. Event emission to the
event spine is **Step 3**, not part of this checklist.

> **Authoritative sources:** ST's repo README (exact versions / commands / flash
> addresses) and `docs/N6-FACTS.md` (boot / reset / flash rules for this exact board).
> Where either disagrees with this checklist, **trust them** — ST firmware packages are
> versioned and addresses can move.

ST app: **`STMicroelectronics/STM32N6-GettingStarted-PoseEstimation`** — officially
supports NUCLEO-N657X0-Q + the IMX335 camera, model = MoveNet Lightning 192² INT8
(single-person, 17 COCO keypoints).

> **Flash facts below verified against ST's README 2026-06-14** (artifact list, the
> `0x70100000` app address, and the SWD-HOTPLUG flash command are quoted from it). Re-check
> on a newer release — ST bumps these packages and addresses can move.

---

## 0. Gather

- [ ] Board: NUCLEO-N657X0-Q, camera module (IMX335) seated on the CSI connector, 2× USB-C
      cables, a Linux host to view the stream.
- [ ] **Output mode = USB / UVC** (recommended — view the keypoint skeleton on your host,
      same as the webcam app; **no extra display hardware**). The alternative SPI-display
      mode needs an X-NUCLEO-GFX01M2 you don't have — skip it.
- [ ] **STM32CubeProgrammer ≥ 2.18** installed (it provides `STM32_SigningTool_CLI` and the
      N6 external-flash loader). Verify it's on PATH.
- [ ] **STM32CubeIDE** — only if building from source (vs. a prebuilt binary in the repo).
- [ ] ST Edge AI Core / X-CUBE-AI — **NOT needed** for the spike (the repo ships the
      quantized MoveNet). Only relevant if you later re-train/re-quantize.
- [ ] Clone the ST repo and **read its README top to bottom first.** This checklist is the
      map; the README is the territory.

## 1. Record current board state (so you can always get back)

- Our Zephyr `camera-app` runs via **RAM dev-boot** (`scripts/swd-run.sh`, loads to
  `0x34180400`, **volatile** — a power cycle clears it). ST's app runs from **external
  octo-SPI flash** (persistent). Consequences:
  - [ ] Flashing ST's app writes external flash; it does **not** touch our Zephyr source or
        the dev-boot workflow. To return to our firmware afterward: re-jumper to dev-boot
        and `swd-run.sh` as usual.
  - [ ] The only thing in external flash that *could* be overwritten is a Zephyr **flash-
        boot** image — and we're on dev-boot, so nothing of ours is lost.
- [ ] **Photograph / note the current jumper (BOOT) positions** before changing anything.

## 2. Flash transport — SWD/ST-Link HOTPLUG, **not** DFU

- **ST flashes over SWD/ST-Link in HOTPLUG mode with an external-flash loader — NOT the
  serial-boot/DFU path.** This is the *same* ST-Link transport our `swd-run.sh` already
  uses, so it's far less fragile than the serial-boot DFU ritual. Exact command from ST's
  README (prebuilt path):
  ```
  STM32_Programmer_CLI -c port=SWD mode=HOTPLUG -el $NUEL -hardRst -w \
    Binary/NUCLEO-N657X0-Q/USB-UVC-Display/NUCLEO-N657X0-Q_GettingStarted_PoseEstimation-uvc.hex
  ```
  (`$NUEL` = the N6 external-loader `.stldr` that ships with CubeProgrammer; the README
  names the exact file.)
- ⚠️ **Our N6 reset rule carries over and *matches* ST's:** `-hardRst` only — never the
  soft `-rst` that bricks AP1 until a hardware reset (see `N6-FACTS.md`). The
  one-push-per-power-cycle **DFU-zombie quirk does NOT apply here** — that was the
  serial-boot path, which this app never touches.
- ⚠️ **FLASH GOTCHA (verified on-board 2026-06-14):** ST's `mode=HOTPLUG` command above
  **fails to attach** ("No STM32 target found / Unable to get core ID") if the board is
  **mid-run in flash-boot** — HOTPLUG connects *before* the reset and hits the running
  app's closed debug port. **Fix: connect-under-reset** — swap `mode=HOTPLUG` →
  **`mode=UR`** (catches the M55 at boot, debug-open). Full working command:
  ```
  STM32_Programmer_CLI -c port=swd mode=UR -el "$NUEL" -hardRst -w \
    ~/projects/st-pose/Binary/NUCLEO-N657X0-Q/USB-UVC-Display/NUCLEO-N657X0-Q_GettingStarted_PoseEstimation-uvc.hex
  ```
  (ST repo cloned at `~/projects/st-pose`; flash board in **dev-boot**.)
- [ ] **To flash:** the board must be reachable by ST-Link — our usual **dev-boot** jumper
      position already gives that. **To run** the persistent app afterward: set **flash-boot**
      jumpers and `-hardRst`/power-cycle. Which physical BOOT positions are which: `N6-FACTS.md`
      (BOOT1 selects dev/SWD vs flash-boot).

## 3. Camera sensor config

- [ ] **Prebuilt path: nothing to configure** — the `...-uvc.hex` is built for this exact
      board and its bundled camera (the IMX335 on the B-CAMS-IMX). Only if you **build from
      source** must you confirm the camera target matches your module (ST's app family also
      supports VD55G1 / VD66GY / VD1943 — a wrong sensor target = no frames). Sensor mismatch
      is suspect #1 if keypoints never appear and the frame is black.
- [ ] If the image is **black**: the IMX335 powers up near-black at low gain and has a cold-
      power-on timing characteristic (see `camera-bringup-debug-log.md`). ST's app sets its
      own gain/exposure, but if the frame stays dark, check the gain/exposure config first.

## 4. Flash (fast path strongly preferred)

- **Fast path — ONE prebuilt, pre-signed, *assembled* hex (recommended).** ST ships a ready
  binary that bundles **FSBL + application + NN weights** into a single signed image:
  `Binary/NUCLEO-N657X0-Q/USB-UVC-Display/NUCLEO-N657X0-Q_GettingStarted_PoseEstimation-uvc.hex`.
  Flash it with the single SWD-HOTPLUG command in §2 — no signing step, no multi-artifact
  juggling; the hex is already signed and carries its own load addresses.
- **Source path — three separate artifacts (only if you rebuild).** A from-source build
  (STM32CubeIDE, USB/UVC config) produces three files, each flashed via the external loader:
  1. FSBL — `FSBL/ai_fsbl.hex`
  2. Signed application — `build/Application/NUCLEO-N657X0-Q/Project_sign.bin` **at `0x70100000`**
  3. Network weights — `Model/NUCLEO-N657X0-Q/network_data.hex`
  - [ ] Confirm the post-build **signing** step actually ran (`STM32_SigningTool_CLI`). The
        N6 **only boots signed images**; an unsigned image fails **silently** (CMake warning
        only, then an unbootable board). The prebuilt hex above sidesteps this entirely.
- [ ] Flash completes without a "failed to download Sector[0]" error (that = wrong load
      address → `-hardRst` and retry).

## 5. Run + verify — *this is the actual de-risk*

> **Run 1 result (2026-06-14): substantively PASS, one item open.** Enumerated as
> `0483:5780 STM32 uvc` (YUYV 320×240); overlay "Inference: 14–15 ms" (~66 fps NPU);
> MoveNet **drew the skeleton** once a person was upright-ish in frame (overlay pixels
> 0→5940). OPEN: a clean, sharp, still, upright frame to eyeball nose/shoulder tracking +
> the slouch signal — frames were motion-blurred and the **camera is mounted ~90° rotated**
> (subject appears sideways; MoveNet wants upright). Grab on the next run.

- [ ] Power-cycle into boot-from-flash. The board enumerates as a **USB video device** on
      your host (just like the webcam app did).
- [ ] View the stream on your host. You should see the **17-keypoint skeleton overlaid** on
      live video.
- Sit at your **normal desk distance and lighting**, then check:
  - [ ] **Nose, both ears, both shoulders** detected with **stable** confidence — these are
        exactly the keypoints a slouch metric needs.
  - [ ] Keypoints **don't jitter** while you hold still.
  - [ ] **Slouching forward/down** visibly moves the nose **down toward the shoulder line**
        — i.e. the signal a slouch metric keys on actually *changes*.
  - [ ] **Leaning left/right** tilts the shoulder line.
- [ ] If keypoints are jittery at 192²: note it. Bumping to **224²** (~27 ms, still ~37 FPS)
      is the easy fix and well within the board's budget.

**Pass = MoveNet reliably lands the upper-body keypoints at your desk, and they move the
right way when you slouch.** That's the whole question this spike answers.

## 6. What this unlocks (Step 2 decision)

With the spike passed, choose how the **production** posture node is built:

- **Path A — stay on ST's NPU stack.** You're already here; fastest route to a finished
  coach; uses the Neural-ART NPU properly. Separate ST toolchain from our Zephyr app.
- **Path B — port a pose model into our Zephyr `camera-app`** (Cortex-M55 + Helium). One
  firmware, deeper learning, NPU deferred; the model must fit the **511 KB image budget**
  (~350 KB headroom today). CPU inference is slower but trivially fast enough at 1–5 Hz.

Either way, **Step 3 is identical**: compute the slouch metric from the keypoints
(nose / ears / shoulders, per-user "sit upright" calibration), then emit posture events
over USB-CDC to the host bridge. Step 3 specifics live in the edge-kit plan (`E2b-pose`).

---

### Quick reference — the model (from ST's N6 model zoo)

| Variant | Inference (N6 NPU) | FPS | Why |
|---|---|---|---|
| MoveNet Lightning **192²** | ~18–22 ms | ~45 | default; plenty at desk distance |
| MoveNet Lightning 224² | ~27 ms | ~37 | bump here if 192² keypoints jitter |

Single-person, 17 keypoints, INT8, Apache-2.0. (Multi-person YOLOv8/v11-pose was rejected:
machinery you don't need, heavier flash, AGPL.)
