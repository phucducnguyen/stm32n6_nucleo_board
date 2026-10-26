#!/usr/bin/env bash
# check-fresh-build.sh — a FRESH Zephyr at the pinned revision, plus the
# documented `west patch ... apply`, builds apps/camera-app for
# nucleo_n657x0_q and passes the verify-only preflight.
#
# Why: zephyr/ carries the IMX335 fix as a local edit, so a build there proves
# nothing about the patch step. This builds in a throwaway west workspace:
#   <tmp>/zephyr   git clone --shared of zephyr/ at its HEAD (the pin)
#   <tmp>/modules  symlink to modules/ (read only by the build)
#   <tmp>/.west    copy of .west/config
# Hazard: never writes zephyr/ or modules/. --shared borrows zephyr/'s
# objects through alternates; nothing is written back to it.
#
# Usage: scripts/check-fresh-build.sh   (FRESH_WS_PARENT= where the temp
# workspace goes; default ~/.cache/stm32n6-fresh, not /tmp: it is a RAM tmpfs)
set -euo pipefail

cd "$(dirname "$0")/.."
REPO=$PWD
PATCH="$REPO/patches/zephyr/imx335-cold-boot-init.patch"
FIX_FILE=drivers/video/imx335.c
# Behaviour lines of the fix (same as check-zephyr-patches.sh).
FIX_LINES=(
	'k_sleep(K_MSEC(5));'
	'for (int attempt = 0; ; attempt++) {'
	'if (attempt >= 10) {'
	'k_msleep(2);'
)
BOARD=nucleo_n657x0_q

src=$(realpath zephyr)
mods=$(realpath modules)
[[ -f .west/config ]] || { echo "FAIL: no .west/config"; exit 1; }
[[ -f "$PATCH" ]] || { echo "FAIL: $PATCH missing"; exit 1; }
rev=$(git -C "$src" rev-parse HEAD)

parent="${FRESH_WS_PARENT:-${XDG_CACHE_HOME:-$HOME/.cache}/stm32n6-fresh}"
mkdir -p "$parent"
ws=$(mktemp -d "$parent/ws.XXXXXX")
trap 'rm -rf "$ws"' EXIT

git clone -q --shared --no-checkout "$src" "$ws/zephyr"
git -C "$ws/zephyr" -c advice.detachedHead=false checkout -q --detach "$rev"
ln -s "$mods" "$ws/modules"
mkdir "$ws/.west"
cp .west/config "$ws/.west/config"
if [[ -n "$(git -C "$ws/zephyr" status --porcelain)" ]]; then
	echo "FAIL: fresh zephyr ${rev:0:12} is not clean before the patch step"; exit 1
fi
echo "fresh_build: fresh zephyr ${rev:0:12} at $ws"

export PATH="$REPO/.venv/bin:$HOME/STMicroelectronics/STM32CubeProgrammer/bin:$PATH"
unset ZEPHYR_BASE
cd "$ws"
# The documented step (AGENTS.md "Zephyr patches"), verbatim.
west patch -b "$REPO/patches" -l "$REPO/patches/patches.yml" apply

# The fix must now be in the fresh tree: the whole patch applied, and its
# behaviour lines present.
if ! git -C zephyr apply --reverse --check "$PATCH" 2>/dev/null; then
	echo "FAIL: fresh zephyr lacks imx335-cold-boot-init.patch after west patch apply"; exit 1
fi
for line in "${FIX_LINES[@]}"; do
	grep -qF -- "$line" "zephyr/$FIX_FILE" || {
		echo "FAIL: fresh zephyr lacks the IMX335 cold-boot fix ($FIX_FILE: no '$line')"; exit 1; }
done
echo "PASS: west patch apply put the IMX335 cold-boot fix into fresh zephyr ${rev:0:12}"

west build -p -b "$BOARD" "$REPO/apps/camera-app" -d "$ws/build"

# The build used the fresh tree, and compiled the patched driver from it.
base=$(sed -n 's/^ZEPHYR_BASE:PATH=//p' build/CMakeCache.txt)
if [[ "$(realpath "$base")" != "$(realpath "$ws/zephyr")" ]]; then
	echo "FAIL: build used ZEPHYR_BASE=$base, not the fresh workspace"; exit 1
fi
grep -qF -- "$ws/zephyr/$FIX_FILE" build/compile_commands.json || {
	echo "FAIL: build did not compile $FIX_FILE from the fresh workspace"; exit 1; }
echo "PASS: camera-app built for $BOARD from fresh zephyr ${rev:0:12} with the patched $FIX_FILE"

bash "$REPO/scripts/preflight-flash.sh" "$ws/build"
