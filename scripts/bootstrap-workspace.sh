#!/usr/bin/env bash
# bootstrap-workspace.sh — turn a fresh clone into a buildable west workspace:
# .venv (pinned host tools), zephyr/ at the v4.4.1 pin (Zephyr is the manifest
# repo), the three narrowed modules, the IMX335 cold-boot patch, then
# scripts/check-zephyr-patches.sh.
#
# Usage: scripts/bootstrap-workspace.sh [--force]
#   --force  continue in an existing workspace; every step skips what is done.
# Env:
#   ZEPHYR_LOCAL_MIRROR=<workspace root>  clone zephyr/ (git clone --shared)
#       and fetch modules from <root>/zephyr and <root>/modules/... instead of
#       GitHub. Must hold the same pin.
#   PYTHON=python3  interpreter for the venv.
#
# Hazard: never a bare `west update` (pulls every module, ~8 GB).
# Not installed here: the Zephyr SDK (arm-zephyr-eabi) and STM32CubeProgrammer.
set -euo pipefail

ZEPHYR_TAG=v4.4.1
ZEPHYR_PIN=1f6485eca25431b5ff27ce9a754218c9e559bbbb
ZEPHYR_URL=https://github.com/zephyrproject-rtos/zephyr
MODULES=(cmsis cmsis_6 hal_stm32)
PATCH=patches/zephyr/imx335-cold-boot-init.patch

force=0
for a in "$@"; do
	case "$a" in
	--force) force=1 ;;
	-h|--help) sed -n '2,17p' "$0"; exit 0 ;;
	*) echo "unknown argument: $a" >&2; exit 2 ;;
	esac
done

cd "$(dirname "$0")/.."
root=$PWD

existing=0
[[ -d .west ]] && existing=1
[[ -d zephyr && -n "$(ls -A zephyr 2>/dev/null)" ]] && existing=1
[[ -d modules && -n "$(ls -A modules 2>/dev/null)" ]] && existing=1
if (( existing && !force )); then
	echo "refusing: $root already holds a workspace (.west/, zephyr/ or modules/); rerun with --force to complete it in place" >&2
	exit 1
fi

mirror="${ZEPHYR_LOCAL_MIRROR:-}"
if [[ -n "$mirror" ]]; then
	mirror=$(cd "$mirror" && pwd)
	[[ "$mirror" != "$root" ]] || { echo "ZEPHYR_LOCAL_MIRROR is this workspace" >&2; exit 2; }
	git -C "$mirror/zephyr" cat-file -e "$ZEPHYR_PIN^{commit}" 2>/dev/null \
		|| { echo "ZEPHYR_LOCAL_MIRROR: $mirror/zephyr lacks $ZEPHYR_PIN" >&2; exit 1; }
fi

echo "== 1/5 .venv from scripts/requirements-host.txt"
if [[ ! -x .venv/bin/python ]]; then
	"${PYTHON:-python3}" -m venv .venv
fi
.venv/bin/python -m pip install -q --disable-pip-version-check -r scripts/requirements-host.txt
export PATH="$root/.venv/bin:$PATH"

echo "== 2/5 zephyr/ at $ZEPHYR_TAG ($ZEPHYR_PIN)"
if [[ ! -d zephyr/.git ]]; then
	if [[ -n "$mirror" ]]; then
		git clone -q --shared --no-checkout "$mirror/zephyr" zephyr
		git -C zephyr remote set-url origin "$ZEPHYR_URL"
		git -C zephyr checkout -q --detach "$ZEPHYR_PIN"
	else
		git clone -q --depth 1 --branch "$ZEPHYR_TAG" "$ZEPHYR_URL" zephyr
	fi
fi
head=$(git -C zephyr rev-parse HEAD)
[[ "$head" == "$ZEPHYR_PIN" ]] || { echo "FAIL: zephyr/ HEAD is $head, expected $ZEPHYR_PIN" >&2; exit 1; }

echo "== 3/5 west init -l zephyr"
if [[ ! -f .west/config ]]; then
	west init -l zephyr
fi

echo "== 4/5 west update --narrow (${MODULES[*]})"
gitcfg=()
if [[ -n "$mirror" ]]; then
	# Rewrite each module URL to the mirror's copy (file:// so --depth works).
	n=0
	while read -r name path url; do
		if [[ -d "$mirror/$path/.git" || -f "$mirror/$path/.git" ]]; then
			gitcfg+=("GIT_CONFIG_KEY_$n=url.file://$mirror/$path.insteadOf" "GIT_CONFIG_VALUE_$n=$url")
			n=$((n + 1))
		fi
	done < <(west list -f '{name} {path} {url}' "${MODULES[@]}")
	gitcfg+=("GIT_CONFIG_COUNT=$n")
fi
env "${gitcfg[@]}" west update --narrow -o=--depth=1 "${MODULES[@]}"

echo "== 5/5 west patch apply"
# west patch apply is not idempotent: skip when the fix is already in the tree.
if git -C zephyr apply --reverse --check "$root/$PATCH" 2>/dev/null; then
	echo "already applied: $PATCH"
else
	west patch -b "$root/patches" -l "$root/patches/patches.yml" apply
fi

scripts/check-zephyr-patches.sh
echo "workspace ready: export PATH=\"$root/.venv/bin:\$PATH\""
