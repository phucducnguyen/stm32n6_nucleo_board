# imx335-fix.sh — sourced by check-zephyr-patches.sh and check-fresh-build.sh: the one list of
# the IMX335 cold-boot fix's behaviour lines, and the matcher both use.
#
# Hazard: match code, never comments. A patch that keeps these lines inside /* */ or // (and
# keeps the 600 us settle as 601) must fail, so comments are stripped first with the C
# preprocessor in comment-only mode (-fpreprocessed: no includes, no macro expansion).
FIX_FILE=drivers/video/imx335.c
# The 5 ms T4 settle and the bounded retry of the first register batch.
FIX_LINES=(
	'k_sleep(K_MSEC(5));'
	'for (int attempt = 0; ; attempt++) {'
	'if (ret == 0) {'
	'if (attempt >= 10) {'
	'k_msleep(2);'
)
# The pre-fix settle; any K_USEC() settle left in the init path counts as not fixed.
FIX_GONE='k_sleep(K_USEC(600));'

# Print the C code of $1 with comments removed (exit 1 if it does not preprocess).
imx335_code() {
	gcc -fpreprocessed -dD -E -P -x c "$1" 2>/dev/null
}

# Print one "no '<line>'" or "still has '<line>'" per problem in $1's code; nothing if fixed.
imx335_fix_problems() {
	local code line
	code=$(imx335_code "$1") || { echo "does not preprocess"; return; }
	for line in "${FIX_LINES[@]}"; do
		grep -qF -- "$line" <<<"$code" || echo "no '$line'"
	done
	grep -qF -- "$FIX_GONE" <<<"$code" && echo "still has '$FIX_GONE'"
	return 0
}
