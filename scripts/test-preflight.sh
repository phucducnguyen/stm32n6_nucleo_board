#!/usr/bin/env bash
#
# test-preflight.sh — regression test for preflight-flash.sh (no hardware needed).
#
# Asserts the gate's verify-only checks behave correctly against real build dirs:
#   - a build linked into the BootROM window (0x34180400) PASSES
#   - a build linked outside it (the axisram1-relink dead-end) FAILS
#   - a missing/empty build dir FAILS
# Run after touching preflight-flash.sh, or any time the build layout changes.
#
# It auto-discovers a known-good and known-bad build under build/ by reading
# each build's RAM ORIGIN, so it keeps working as build dirs come and go.

set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
PREFLIGHT="scripts/preflight-flash.sh"

red() { printf '\033[31m%s\033[0m\n' "$*"; }
grn() { printf '\033[32m%s\033[0m\n' "$*"; }

fails=0
expect() { # <desc> <expected-rc: pass|fail> <build-dir>
	local desc="$1" want="$2" dir="$3"
	bash "$PREFLIGHT" "$dir" >/dev/null 2>&1
	local rc=$?
	if [[ "$want" == "pass" && $rc -eq 0 ]] || [[ "$want" == "fail" && $rc -ne 0 ]]; then
		grn "PASS: $desc (rc=$rc, wanted $want)"
	else
		red "FAIL: $desc (rc=$rc, wanted $want) [$dir]"
		fails=$((fails + 1))
	fi
}

# Discover a good (origin 0x34180400) and a bad (other origin) build.
good="" bad=""
for d in build/*/; do
	ld="$d/zephyr/linker.cmd"
	[[ -f "$ld" ]] || continue
	[[ -f "$d/zephyr/zephyr.signed.bin" ]] || continue
	o=$(grep -E '\bRAM \(wx\)' "$ld" | grep -oE 'ORIGIN = 0x[0-9A-Fa-f]+' | grep -oE '0x[0-9A-Fa-f]+' | head -1)
	[[ -z "$o" ]] && continue
	if (( o == 0x34180400 )); then [[ -z "$good" ]] && good="${d%/}"
	else [[ -z "$bad" ]] && bad="${d%/}"; fi
done

echo "== preflight regression test =="
if [[ -n "$good" ]]; then expect "in-window build passes" pass "$good"
else echo "skip: no in-window build found under build/"; fi
if [[ -n "$bad" ]]; then expect "out-of-window build fails" fail "$bad"
else echo "skip: no out-of-window build found under build/"; fi
expect "missing build dir fails" fail "build/__does_not_exist__"

if (( fails == 0 )); then grn "ALL PREFLIGHT TESTS PASSED"; else red "$fails test(s) FAILED"; fi
exit $(( fails > 0 ? 1 : 0 ))
