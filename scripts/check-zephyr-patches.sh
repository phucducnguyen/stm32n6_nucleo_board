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
FIX_MARK='T4 (cold-boot hardened)'

[[ -f "$YML" ]] || { echo "FAIL: $YML missing"; exit 1; }
git -C "$ZEPHYR" rev-parse --git-dir >/dev/null 2>&1 || { echo "FAIL: no Zephyr git repo at $ZEPHYR"; exit 1; }

# One line per patch: path sha256 base-rev (our own flat layout of the file).
entries=$(awk '
	/^  - path:/ { if (p) print p, s, r; p=$3; s=""; r="" }
	/^    sha256sum:/ { s=$2 }
	/^      base-rev:/ { r=$2 }
	END { if (p) print p, s, r }' "$YML")
[[ -n "$entries" ]] || { echo "FAIL: $YML lists no patches"; exit 1; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fail=0
fix_rev=""

while read -r path sha rev; do
	file="$BASE/$path"
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

if [[ -n "$fix_rev" ]] && grep -qF "$FIX_MARK" "$tmp/$fix_rev/$FIX_FILE"; then
	echo "PASS: patched copy carries the IMX335 cold-boot fix"
else
	echo "FAIL: patched copy lacks the IMX335 cold-boot fix ($FIX_FILE: '$FIX_MARK')"; fail=1
fi
exit "$fail"
