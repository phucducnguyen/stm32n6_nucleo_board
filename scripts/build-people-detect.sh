#!/usr/bin/env bash
# build-people-detect.sh — build + sign the (forked) ST people-detection app for
# NUCLEO-N657X0-Q, with our USART1 detection-emit patch (APP_DET_EMIT).
#
# Pure host build — touches NO hardware. Produces a signed, flash-ready image.
# Flashing is a separate step (needs dev-boot jumper): scripts/flash-people-detect.sh
#
# Prereqs (no sudo):
#   - ARM GNU toolchain at $ARM_TOOLCHAIN/bin/arm-none-eabi-gcc
#     (default: newest ~/toolchains/arm-gnu-toolchain-*-x86_64-arm-none-eabi)
#   - STM32CubeProgrammer (for STM32_SigningTool_CLI), default ~/STMicroelectronics/...
#   - The ST people-detection repo (our fork) as a sibling: ../st-people
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PEOPLE_DIR="${PEOPLE_DIR:-$(cd "$HERE/../../st-people" && pwd)}"
BOARD="${BOARD:-NUCLEO-N657X0-Q}"

# --- locate the ARM toolchain ----------------------------------------------
if [ -z "${ARM_TOOLCHAIN:-}" ]; then
	ARM_TOOLCHAIN="$(ls -d "$HOME"/toolchains/arm-gnu-toolchain-*-x86_64-arm-none-eabi 2>/dev/null | sort -V | tail -1 || true)"
fi
[ -n "$ARM_TOOLCHAIN" ] && [ -x "$ARM_TOOLCHAIN/bin/arm-none-eabi-gcc" ] || {
	echo "build: arm-none-eabi-gcc not found. Set ARM_TOOLCHAIN to the toolchain root." >&2
	exit 1; }
export PATH="$ARM_TOOLCHAIN/bin:$PATH"
echo "build: toolchain $($ARM_TOOLCHAIN/bin/arm-none-eabi-gcc -dumpversion) at $ARM_TOOLCHAIN"

# --- locate the signing tool ------------------------------------------------
SIGN="${SIGN:-$(command -v STM32_SigningTool_CLI || true)}"
[ -n "$SIGN" ] || SIGN="$(ls "$HOME"/STMicroelectronics/STM32CubeProgrammer/bin/STM32_SigningTool_CLI 2>/dev/null | head -1 || true)"
[ -n "$SIGN" ] && [ -x "$SIGN" ] || {
	echo "build: STM32_SigningTool_CLI not found. Set SIGN to its path." >&2; exit 1; }

# --- build ------------------------------------------------------------------
echo "build: make BOARD=$BOARD in $PEOPLE_DIR"
make -C "$PEOPLE_DIR" BOARD="$BOARD" -j"$(nproc)"

BIN="$PEOPLE_DIR/build/Project.bin"
[ -f "$BIN" ] || { echo "build: expected $BIN, not produced" >&2; exit 1; }

# --- sign (N6 only boots signed images) -------------------------------------
SIGNED="$PEOPLE_DIR/build/Project_sign.bin"
echo "build: signing -> $SIGNED"
"$SIGN" -bin "$BIN" -nk -t ssbl -hv 2.3 -o "$SIGNED"

echo
echo "build: OK"
echo "  signed app : $SIGNED   (flash at 0x70100000)"
echo "  FSBL       : $PEOPLE_DIR/FSBL/ai_fsbl.hex"
echo "  network    : $PEOPLE_DIR/Model/$BOARD/network_data.hex"
echo "Next: set BOOT1 -> dev-boot, power-cycle, then scripts/flash-people-detect.sh"
