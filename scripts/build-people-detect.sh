#!/usr/bin/env bash
# build-people-detect.sh — build + sign the (forked) ST people-detection app for
# NUCLEO-N657X0-Q, with our USART1 detection-emit patch (APP_DET_EMIT).
#
# Default: the SEALED occupancy image. The no-screen backend
# (patches/st-people-no-screen.patch, SCR_LIB_SCREEN_ITF=NONE) replaces ST's UVC
# video stream, and host/occupancy-eval/privacy_judge.py must PASS on the linked
# ELF before the image is signed. A judge FAIL exits 1 and nothing is signed.
#
#   scripts/build-people-detect.sh              sealed image -> build/Project_sign.bin
#   scripts/build-people-detect.sh --webcam     AIMING image (streams camera video
#                                               over USB) -> build-webcam/Project_webcam_sign.bin
#   add --no-sign                               build + judge only (no signing tool needed)
#
# The webcam image builds into its own dir so flash-people-detect.sh (which reads
# build/Project_sign.bin) can never pick it up by accident.
#
# Pure host build — touches NO hardware. Flashing is a separate step (needs
# dev-boot jumper): scripts/flash-people-detect.sh
#
# Patches: both st-people patches (patches/st-people-*.patch) are applied to
# $PEOPLE_DIR if not already there (git apply; a patch that is neither applied
# nor applies cleanly stops the build). They are not west patches and are not
# listed in patches/patches.yml.
#
# Prereqs (no sudo):
#   - ARM GNU toolchain at $ARM_TOOLCHAIN/bin/arm-none-eabi-gcc
#     (default: newest ~/toolchains/arm-gnu-toolchain-*-x86_64-arm-none-eabi)
#   - STM32CubeProgrammer (for STM32_SigningTool_CLI), default ~/STMicroelectronics/...
#   - The ST people-detection repo (our fork) as a sibling: ../st-people
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
PEOPLE_DIR="${PEOPLE_DIR:-$(cd "$HERE/../../st-people" && pwd)}"
BOARD="${BOARD:-NUCLEO-N657X0-Q}"

WEBCAM=0
DO_SIGN=1
for arg in "$@"; do
	case "$arg" in
	--webcam) WEBCAM=1 ;;
	--no-sign) DO_SIGN=0 ;;
	-h|--help) sed -n '2,30p' "$0"; exit 0 ;;
	*) echo "build: unknown argument '$arg' (use --webcam, --no-sign)" >&2; exit 2 ;;
	esac
done

if [ "$WEBCAM" = 1 ]; then
	SCREEN_ITF=UVCL
	BUILD_DIR=build-webcam
	SIGNED_NAME=Project_webcam_sign.bin
	echo "build: *** WEBCAM AIMING IMAGE: streams the camera video over USB (UVC). ***"
	echo "build: *** Not the occupancy image; for aiming the camera only.          ***"
else
	SCREEN_ITF=NONE
	BUILD_DIR=build
	SIGNED_NAME=Project_sign.bin
	echo "build: sealed occupancy image (SCR_LIB_SCREEN_ITF=NONE, privacy judge gates signing)"
fi

# --- locate the ARM toolchain ----------------------------------------------
if [ -z "${ARM_TOOLCHAIN:-}" ]; then
	ARM_TOOLCHAIN="$(ls -d "$HOME"/toolchains/arm-gnu-toolchain-*-x86_64-arm-none-eabi 2>/dev/null | sort -V | tail -1 || true)"
fi
[ -n "$ARM_TOOLCHAIN" ] && [ -x "$ARM_TOOLCHAIN/bin/arm-none-eabi-gcc" ] || {
	echo "build: arm-none-eabi-gcc not found. Set ARM_TOOLCHAIN to the toolchain root." >&2
	exit 1; }
export PATH="$ARM_TOOLCHAIN/bin:$PATH"
echo "build: toolchain $($ARM_TOOLCHAIN/bin/arm-none-eabi-gcc -dumpversion) at $ARM_TOOLCHAIN"

# --- locate the signing tool (before building, so a missing tool fails fast) --
if [ "$DO_SIGN" = 1 ]; then
	SIGN="${SIGN:-$(command -v STM32_SigningTool_CLI || true)}"
	[ -n "$SIGN" ] || SIGN="$(ls "$HOME"/STMicroelectronics/STM32CubeProgrammer/bin/STM32_SigningTool_CLI 2>/dev/null | head -1 || true)"
	[ -n "$SIGN" ] && [ -x "$SIGN" ] || {
		echo "build: STM32_SigningTool_CLI not found. Set SIGN to its path (or pass --no-sign)." >&2; exit 1; }
fi

# --- apply the st-people patches (idempotent) ---------------------------------
for p in "$REPO/patches/st-people-uart-det-emit.patch" "$REPO/patches/st-people-no-screen.patch"; do
	name="$(basename "$p")"
	if git -C "$PEOPLE_DIR" apply --reverse --check "$p" 2>/dev/null; then
		echo "build: $name already applied"
	elif git -C "$PEOPLE_DIR" apply --check "$p"; then
		git -C "$PEOPLE_DIR" apply "$p"
		echo "build: applied $name"
	else
		echo "build: $name is neither applied nor applies cleanly to $PEOPLE_DIR" >&2; exit 1
	fi
done

# --- build ------------------------------------------------------------------
# make does not see a changed SCR_LIB_SCREEN_ITF (it changes C_DEFS, not sources):
# objects from another backend would be relinked. Rebuild all (-B) unless the
# build dir was last built with this backend.
STAMP="$PEOPLE_DIR/$BUILD_DIR/.screen-itf"
MAKE_B=()
if [ "$(cat "$STAMP" 2>/dev/null || true)" != "$SCREEN_ITF" ]; then
	MAKE_B=(-B)
	echo "build: $BUILD_DIR/ not last built with SCR_LIB_SCREEN_ITF=$SCREEN_ITF: full rebuild"
fi
rm -f "$STAMP"
echo "build: make BOARD=$BOARD SCR_LIB_SCREEN_ITF=$SCREEN_ITF BUILD_DIR=$BUILD_DIR in $PEOPLE_DIR"
make -C "$PEOPLE_DIR" "${MAKE_B[@]}" BOARD="$BOARD" SCR_LIB_SCREEN_ITF="$SCREEN_ITF" BUILD_DIR="$BUILD_DIR" -j"$(nproc)"
echo "$SCREEN_ITF" > "$STAMP"

ELF="$PEOPLE_DIR/$BUILD_DIR/Project.elf"
BIN="$PEOPLE_DIR/$BUILD_DIR/Project.bin"
[ -f "$BIN" ] && [ -f "$ELF" ] || { echo "build: expected $ELF and $BIN, not produced" >&2; exit 1; }

# --- privacy judge on what was linked ------------------------------------------
echo "build: privacy judge on $ELF"
set +e
python3 -B "$REPO/host/occupancy-eval/privacy_judge.py" --elf "$ELF"
JUDGE_RC=$?
set -e
if [ "$WEBCAM" = 0 ] && [ "$JUDGE_RC" != 0 ]; then
	echo "build: REFUSED: the sealed image failed the privacy judge (rc $JUDGE_RC); not signed." >&2
	exit 1
fi
if [ "$WEBCAM" = 1 ]; then
	echo "build: privacy judge rc $JUDGE_RC on the webcam image (expected FAIL: it streams video)"
fi

if [ "$DO_SIGN" = 0 ]; then
	echo "build: OK (--no-sign: not signed)  elf: $ELF"
	exit 0
fi

# --- sign (N6 only boots signed images) -------------------------------------
SIGNED="$PEOPLE_DIR/$BUILD_DIR/$SIGNED_NAME"
echo "build: signing -> $SIGNED"
"$SIGN" -bin "$BIN" -nk -t ssbl -hv 2.3 -o "$SIGNED"

echo
echo "build: OK"
echo "  signed app : $SIGNED   (flash at 0x70100000)"
echo "  FSBL       : $PEOPLE_DIR/FSBL/ai_fsbl.hex"
echo "  network    : $PEOPLE_DIR/Model/$BOARD/network_data.hex"
if [ "$WEBCAM" = 1 ]; then
	echo "  WEBCAM AIMING IMAGE: streams video over USB. flash-people-detect.sh flashes build/, not this."
else
	echo "Next: set BOOT1 -> dev-boot, power-cycle, then scripts/flash-people-detect.sh"
fi
