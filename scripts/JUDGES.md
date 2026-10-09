# JUDGES

`scripts/judges.yaml` lists the checks that can say "this claim holds" for this repo, in the
house `judges/1` format. Rule: a verdict counts only from a judge that has rejected a known-bad case.

Fields per judge: `id` (snake_case), `claim` (one sentence), `command` (exit 0 = holds),
`known_bad` (list of `name`, `patch` or `command`, `expect`), `proof` (`status` PROVEN / NOT PROVEN /
SKIPPED, `date`, `failing_line` or `reason`).

How a prover uses it (Gavel's `verdict/prove.py`, not written here): for each judge, make a clean
`git archive HEAD` export, apply one known-bad mutation, run `command`, and require a non-zero exit
whose output contains `expect`. Only then is a green run of the unmutated judge a verdict.
Mutations never run on the working tree. The preflight judge reads gitignored `build/`, so link
the real `build/` into the export read-only; the firmware build judge is SKIPPED by default.

Add a judge: append an entry, give it at least one single-line mutation (a `sed` command, or a
patch under `scripts/judges/mutations/`), run it on an export, record `proof`. No proof means
NOT PROVEN, not PROVEN.
