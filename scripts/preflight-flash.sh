#!/usr/bin/env bash
#
# preflight-flash.sh — verify a Zephyr build is loadable on the STM32N6 BootROM
# *before* pushing it over serial boot, then (optionally) flash it.
#
# Why this exists: two foot-guns cost real debug cycles on this board —
#   1) flashing an image linked OUTSIDE the BootROM load window (0x34180400).
#      The BootROM silently refuses it with "failed to download Sector[0]".
#      (Happened with an axisram1-relink overlay build — RAM ORIGIN 0x34000000.)
#   2) flashing into a dead/zombie serial-boot DFU session. The N6 arms DFU
#      ONCE per power-up; RESET does not re-arm, and even a *refused* download
#      consumes the session. The next push makes STM32_Programmer_CLI segfault
#      (exit -11). Recovery = full power cycle, nothing is broken.
# See docs/N6-FACTS.md for the full boot/DFU rules.
#
# Usage:
#   scripts/preflight-flash.sh <build-dir>            # verify only (no hardware touched)
#   scripts/preflight-flash.sh <build-dir> --flash    # verify, then west flash if all pass
#
# Exit 0 = all checks pass. Non-zero = a check failed (and we did NOT flash).

set -euo pipefail

# --- N6 BootROM load window (UM3234; verified on hardware) ---
WINDOW_ORIGIN_HEX="0x34180400"
WINDOW_LEN_KB=511

BUILD_DIR="${1:-}"
MODE="${2:-}"
if [[ -z "$BUILD_DIR" ]]; then
	echo "usage: $(basename "$0") <build-dir> [--flash]" >&2
	exit 2
fi
BUILD_DIR="${BUILD_DIR%/}"

fail() { echo "  ✗ FAIL: $*" >&2; exit 1; }
pass() { echo "  ✓ $*"; }

echo "preflight: $BUILD_DIR"

# --- 1. signed image exists (no signing tool at build time => unbootable image) ---
SIGNED="$BUILD_DIR/zephyr/zephyr.signed.bin"
[[ -f "$SIGNED" ]] || fail "no signed image at $SIGNED — was STM32_SigningTool_CLI on PATH at build time?"
SIGNED_SZ=$(stat -c%s "$SIGNED")
pass "signed image present (${SIGNED_SZ} bytes)"

# --- 2. image links INTO the BootROM window (catches relink-overlay mistakes) ---
LD="$BUILD_DIR/zephyr/linker.cmd"
[[ -f "$LD" ]] || fail "no linker.cmd in $BUILD_DIR (incomplete build?)"
ram_line=$(grep -E '\bRAM \(wx\)' "$LD" | head -1 || true)
[[ -n "$ram_line" ]] || fail "could not find the RAM region line in linker.cmd"
origin=$(echo "$ram_line" | grep -oE 'ORIGIN = 0x[0-9A-Fa-f]+' | grep -oE '0x[0-9A-Fa-f]+' | head -1)
[[ -n "$origin" ]] || fail "could not parse RAM ORIGIN from: $ram_line"
if (( origin != WINDOW_ORIGIN_HEX )); then
	fail "image links to $origin, not the BootROM window $WINDOW_ORIGIN_HEX.
        The BootROM loads ONLY into $WINDOW_ORIGIN_HEX and will refuse this
        ('failed to download Sector[0]'). Remove any axisram1-relink overlay;
        keep zephyr,sram on the stock axisram2. See docs/N6-FACTS.md."
fi
pass "links into BootROM window $WINDOW_ORIGIN_HEX"

# --- 3. image fits the 511 KB window ---
WINDOW_BYTES=$(( WINDOW_LEN_KB * 1024 ))
if (( SIGNED_SZ > WINDOW_BYTES )); then
	fail "signed image ${SIGNED_SZ} B exceeds the ${WINDOW_LEN_KB} KB window (${WINDOW_BYTES} B)."
fi
pass "fits the ${WINDOW_LEN_KB} KB window (${SIGNED_SZ} / ${WINDOW_BYTES} B)"

# --- 4. (verify-only) we're done; (--flash) require an armed DFU then push ---
if [[ "$MODE" != "--flash" ]]; then
	echo "preflight: PASS (verify-only; not flashing)"
	exit 0
fi

if ! lsusb 2>/dev/null | grep -qi 'df11'; then
	fail "no DFU device (0483:df11) on the bus — the board is not in an armed serial-boot session.
        Full power cycle (both USB-C cables out → wait 3 s → back in). RESET does NOT re-arm DFU."
fi
pass "DFU armed (0483:df11 present)"

echo "preflight: PASS — flashing $BUILD_DIR"
PATH="$PWD/.venv/bin:$HOME/STMicroelectronics/STM32CubeProgrammer/bin:$PATH" \
	west flash -d "$BUILD_DIR"
