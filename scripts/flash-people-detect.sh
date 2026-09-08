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
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PEOPLE_DIR="${PEOPLE_DIR:-$(cd "$HERE/../../st-people" && pwd)}"
BOARD="${BOARD:-NUCLEO-N657X0-Q}"
APP_ADDR="${APP_ADDR:-0x70100000}"

CLI="${CLI:-$(command -v STM32_Programmer_CLI || true)}"
[ -n "$CLI" ] || CLI="$(ls "$HOME"/STMicroelectronics/STM32CubeProgrammer/bin/STM32_Programmer_CLI 2>/dev/null | head -1 || true)"
[ -n "$CLI" ] && [ -x "$CLI" ] || { echo "flash: STM32_Programmer_CLI not found" >&2; exit 1; }

NUEL="${NUEL:-$(ls "$(dirname "$CLI")"/ExternalLoader/MX25UM51245G_STM32N6570-NUCLEO.stldr 2>/dev/null | head -1 || true)}"
[ -n "$NUEL" ] && [ -f "$NUEL" ] || { echo "flash: NUCLEO external loader (.stldr) not found" >&2; exit 1; }

FSBL="$PEOPLE_DIR/FSBL/ai_fsbl.hex"
NETWORK="$PEOPLE_DIR/Model/$BOARD/network_data.hex"
SIGNED="$PEOPLE_DIR/build/Project_sign.bin"
for f in "$FSBL" "$NETWORK" "$SIGNED"; do
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
