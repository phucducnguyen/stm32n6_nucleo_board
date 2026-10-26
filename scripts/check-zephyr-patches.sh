#!/usr/bin/env bash
# check-zephyr-patches.sh — every patch in patches/patches.yml matches its
# sha256 and applies cleanly to a COPY of its pinned Zephyr revision, and the
# patched copy carries the IMX335 cold-boot fix.
#
# Hazard: never touches zephyr/'s working tree. It only reads git objects
# (`git -C zephyr archive <rev>`) into a mktemp dir. zephyr/ already carries
# the fix as a local edit, so checking there would prove nothing.
#
# Limit: this proves the fix is in the source a build would use after
# `west patch apply`, not that the cold-boot race is gone (a bench claim).
set -euo pipefail

cd "$(dirname "$0")/.."
YML=patches/patches.yml
BASE=patches
ZEPHYR="${ZEPHYR_DIR:-zephyr}"
FIX_FILE=drivers/video/imx335.c
# Behaviour lines of the fix, not its comments: the 5 ms T4 settle and the
# bounded retry of the first register batch.
FIX_LINES=(
	'k_sleep(K_MSEC(5));'
	'for (int attempt = 0; ; attempt++) {'
	'if (ret == 0) {'
	'if (attempt >= 10) {'
	'k_msleep(2);'
)
FIX_GONE='k_sleep(K_USEC(600));'

[[ -f "$YML" ]] || { echo "FAIL: $YML missing"; exit 1; }
git -C "$ZEPHYR" rev-parse --git-dir >/dev/null 2>&1 || { echo "FAIL: no Zephyr git repo at $ZEPHYR"; exit 1; }

# One line per patch: path|sha256|base-rev|module|apply-command (our own flat
# layout of the file). apply-command absent = the schema default, git apply.
entries=$(awk '
	function out() { print p "|" s "|" r "|" m "|" a }
	/^  - path:/ { if (p) out(); p=$3; s=""; r=""; m=""; a="git apply" }
	/^    sha256sum:/ { s=$2 }
	/^    module:/ { m=$2 }
	/^    apply-command:/ { sub(/^    apply-command: */, ""); a=$0 }
	/^      base-rev:/ { r=$2 }
	END { if (p) out() }' "$YML")
[[ -n "$entries" ]] || { echo "FAIL: $YML lists no patches"; exit 1; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fail=0
fix_rev=""

while IFS='|' read -r path sha rev module apply; do
	file="$BASE/$path"
	# west patch runs apply-command in the module dir; anything else than a
	# plain git apply on zephyr would leave this check green while west applies
	# nothing (or something else).
	if [[ "$module" != "zephyr" ]]; then
		echo "FAIL: $path: module is '$module', expected zephyr"; fail=1; continue
	fi
	if [[ "$apply" != "git apply" ]]; then
		echo "FAIL: $path: apply-command is '$apply', expected 'git apply'"; fail=1; continue
	fi
	if [[ -z "$sha" || -z "$rev" ]]; then
		echo "FAIL: $path: sha256sum or custom.base-rev missing"; fail=1; continue
	fi
	if [[ ! -f "$file" ]]; then
		echo "FAIL: $path: patch file missing"; fail=1; continue
	fi
	actual=$(sha256sum "$file" | cut -d' ' -f1)
	if [[ "$actual" != "$sha" ]]; then
		echo "FAIL: $path: sha256 mismatch (west patch apply would refuse it)"; fail=1; continue
	fi
	if ! git -C "$ZEPHYR" cat-file -e "$rev^{commit}" 2>/dev/null; then
		echo "FAIL: $path: base-rev $rev not in $ZEPHYR"; fail=1; continue
	fi
	copy="$tmp/$rev"
	if [[ ! -d "$copy" ]]; then
		mkdir -p "$copy"
		mapfile -t touched < <(sed -n 's#^+++ b/##p' "$file")
		git -C "$ZEPHYR" archive "$rev" -- "${touched[@]}" "$FIX_FILE" | tar -x -C "$copy"
	fi
	if ! out=$(cd "$copy" && git apply --check "$OLDPWD/$file" 2>&1); then
		echo "FAIL: $path does not apply to zephyr ${rev:0:12}: $out"; fail=1; continue
	fi
	(cd "$copy" && git apply "$OLDPWD/$file")
	echo "PASS: $path applies to zephyr ${rev:0:12}"
	fix_rev="$rev"
done <<<"$entries"

if [[ -z "$fix_rev" ]]; then
	echo "FAIL: patched copy lacks the IMX335 cold-boot fix (no patch applied)"; fail=1
else
	fixed="$tmp/$fix_rev/$FIX_FILE"
	missing=0
	for line in "${FIX_LINES[@]}"; do
		if ! grep -qF -- "$line" "$fixed"; then
			echo "FAIL: patched copy lacks the IMX335 cold-boot fix ($FIX_FILE: no '$line')"; missing=1
		fi
	done
	if grep -qF -- "$FIX_GONE" "$fixed"; then
		echo "FAIL: patched copy lacks the IMX335 cold-boot fix ($FIX_FILE: still has '$FIX_GONE')"; missing=1
	fi
	if (( missing )); then
		fail=1
	else
		echo "PASS: patched copy carries the IMX335 cold-boot fix (5 ms settle, bounded retry)"
	fi
fi
exit "$fail"
