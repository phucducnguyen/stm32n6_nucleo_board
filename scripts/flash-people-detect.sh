#!/usr/bin/env bash
# flash-people-detect.sh — flash the signed people-detection app (+ FSBL + NN
# weights) onto NUCLEO-N657X0-Q over SWD/ST-Link.
#
# REQUIRES the board in DEV-BOOT: BOOT1 -> position 2 (unprinted side), power-cycle.
# In flash-boot the running app holds the octo-SPI and erase fails
# ("failed to erase memory" / "Mass erase ... failed") — that error == wrong jumper.
# After flashing: BOOT0+BOOT1 -> position 1 (printed), power-cycle to RUN.
#
# Build first with scripts/build-people-detect.sh (produces the signed bin).
#
#   scripts/flash-people-detect.sh            sealed image build/Project_sign.bin
#   scripts/flash-people-detect.sh --webcam   AIMING image build-webcam/Project_webcam_sign.bin
#                                             (streams camera video over USB)
#
# Refuses unless <signed>.seal (written by the build) exists and its sha256 matches
# the image about to be flashed; the sealed image also needs judge=PASS.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PEOPLE_DIR="${PEOPLE_DIR:-$(cd "$HERE/../../st-people" && pwd)}"
BOARD="${BOARD:-NUCLEO-N657X0-Q}"
APP_ADDR="${APP_ADDR:-0x70100000}"

WEBCAM=0
for arg in "$@"; do
	case "$arg" in
	--webcam) WEBCAM=1 ;;
	-h|--help) sed -n '2,20p' "$0"; exit 0 ;;
	*) echo "flash: unknown argument '$arg' (use --webcam)" >&2; exit 2 ;;
	esac
done
if [ "$WEBCAM" = 1 ]; then
	SIGNED="$PEOPLE_DIR/build-webcam/Project_webcam_sign.bin"
	echo "flash: *** WEBCAM AIMING IMAGE: streams the camera video over USB. ***"
else
	SIGNED="$PEOPLE_DIR/build/Project_sign.bin"
fi

# --- seal check: before any tool runs, so a refused image never reaches the board --
refuse() { echo "flash: REFUSED: $*" >&2; exit 1; }
SEAL="$SIGNED.seal"
[ -f "$SIGNED" ] || refuse "missing $SIGNED (build first?)"
case "$(readlink -f "$SIGNED")" in
*/build-webcam/*) [ "$WEBCAM" = 1 ] || refuse "$SIGNED resolves into build-webcam/ (pass --webcam to flash the aiming image)" ;;
esac
[ -f "$SEAL" ] || refuse "no seal $SEAL (rebuild with scripts/build-people-detect.sh, signing on)"
seal_get() { sed -n "s/^$1=//p" "$SEAL" | head -1; }
WANT_SHA="$(seal_get signed_sha256)"
HAVE_SHA="$(sha256sum "$SIGNED" | cut -d' ' -f1)"
[ -n "$WANT_SHA" ] && [ "$WANT_SHA" = "$HAVE_SHA" ] || refuse "$SIGNED sha256 $HAVE_SHA does not match its seal (${WANT_SHA:-none})"
if [ "$WEBCAM" = 0 ]; then
	[ "$(seal_get judge)" = PASS ] || refuse "seal judge=$(seal_get judge), not PASS"
	[ "$(seal_get screen_itf)" = NONE ] || refuse "seal screen_itf=$(seal_get screen_itf), not NONE"
fi
echo "flash: seal OK ($SEAL, judge=$(seal_get judge), sha256 $HAVE_SHA)"

CLI="${CLI:-$(command -v STM32_Programmer_CLI || true)}"
[ -n "$CLI" ] || CLI="$(ls "$HOME"/STMicroelectronics/STM32CubeProgrammer/bin/STM32_Programmer_CLI 2>/dev/null | head -1 || true)"
[ -n "$CLI" ] && [ -x "$CLI" ] || { echo "flash: STM32_Programmer_CLI not found" >&2; exit 1; }

NUEL="${NUEL:-$(ls "$(dirname "$CLI")"/ExternalLoader/MX25UM51245G_STM32N6570-NUCLEO.stldr 2>/dev/null | head -1 || true)}"
[ -n "$NUEL" ] && [ -f "$NUEL" ] || { echo "flash: NUCLEO external loader (.stldr) not found" >&2; exit 1; }

FSBL="$PEOPLE_DIR/FSBL/ai_fsbl.hex"
NETWORK="$PEOPLE_DIR/Model/$BOARD/network_data.hex"
for f in "$FSBL" "$NETWORK"; do
	[ -f "$f" ] || { echo "flash: missing $f (build first?)" >&2; exit 1; }
done

prog() {  # program one artifact; on erase failure, surface the jumper hint
	local out
	out="$("$CLI" -c port=swd mode=UR -el "$NUEL" -hardRst -w "$@" 2>&1)" || true
	echo "$out" | grep -aiE 'download|program|complete|verif|error|fail|erase' | tail -8
	if echo "$out" | grep -qaiE 'erase.*fail|failed to erase'; then
		echo "flash: ERASE FAILED -> board is almost certainly in FLASH-BOOT." >&2
		echo "       Set BOOT1 -> position 2 (unprinted = dev-boot), power-cycle, retry." >&2
		exit 2
	fi
}

echo "flash: FSBL    $FSBL";    prog "$FSBL"
echo "flash: network $NETWORK"; prog "$NETWORK"
echo "flash: app     $SIGNED @ $APP_ADDR"; prog "$SIGNED" "$APP_ADDR"

echo
echo "flash: OK. Now set BOOT0+BOOT1 -> position 1 (printed) and power-cycle to RUN."
echo "Then: board enumerates as UVC; read detections on the newest /dev/ttyACM*:"
echo "      python -m occbridge --device /dev/ttyACM0 --dry-run   (from host/occupancy-bridge)"
