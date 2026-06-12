#!/usr/bin/env bash
# swd-run.sh <build-dir> — load a Zephyr RAM image over SWD (dev boot) and run it.
#
# Replaces the serial-boot DFU ritual (power cycle per flash). Requires:
#   - BOOT1 jumper on the UNPRINTED side (dev boot) — one-time setup
#   - STM32_Programmer_CLI on PATH
# Repeatable: reset → load raw zephyr.bin into the BootROM window → point
# VTOR/MSP/PC at it → run. No signing, no DFU, no power cycle.
#
# Reset choice (verified on our board): -hardRst (NRST pin, = the button) gives
# a clean re-entry into the dev-boot wait loop. A software -rst BREAKS AP1
# ("Cannot connect to access port 1") until the next hardware reset — never use it.
# The hard reset also wipes AXISRAM (wanted: clean state); we download after it.
set -euo pipefail

BUILD_DIR="${1:?usage: swd-run.sh <build-dir>}"
BIN="$BUILD_DIR/zephyr/zephyr.bin"
LOAD_ADDR=0x34180400

[ -f "$BIN" ] || { echo "swd-run: $BIN not found" >&2; exit 1; }
command -v STM32_Programmer_CLI >/dev/null || {
	echo "swd-run: STM32_Programmer_CLI not on PATH" >&2; exit 1; }

# Vector table at the head of the bin: word0 = initial MSP, word1 = reset vector
read -r MSP PC < <(od -An -tx4 -N8 "$BIN" | awk '{print "0x"$1, "0x"$2}')
PC=$(printf '0x%08X' $((PC & ~1)))   # debugger PC writes want bit0 clear; T bit goes in XPSR

echo "swd-run: $BIN -> $LOAD_ADDR (MSP=$MSP PC=$PC)"

STM32_Programmer_CLI -c port=swd mode=hotplug -hardRst \
	| grep -aE "Hard reset|Error|error" || true
sleep 1

STM32_Programmer_CLI -c port=swd mode=hotplug ap=1 \
	-halt \
	-d "$BIN" "$LOAD_ADDR" \
	-w32 0xE000ED08 "$LOAD_ADDR" \
	-coreReg MSP="$MSP" PC="$PC" XPSR=0x01000000 \
	-run \
	| grep -aE "Core halt|Download in|download complete|Write|Core run|Error|error" || true

echo "swd-run: started."
