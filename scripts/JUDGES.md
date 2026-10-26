# JUDGES

`scripts/judges.yaml` lists the checks that can say "this claim holds" for this repo, in the
house `judges/1` format. Rule: a verdict counts only from a judge that has rejected a known-bad case.

Fields per judge: `id` (snake_case), `claim` (one sentence), `run` (shell; exit 0 = holds),
optional `pass_match` (lines the clean run must print), `fail_exit` (exit codes that count as a
rejection, default `[1]`), `requires`, `setup` (`link:` paths from the working tree), `known_bad`
(list of `name`, `cmd` (a shell edit of the export) or `fault` (a patch file), and `match` (regex,
or a list, the rejected run must print)), `proof` (`status` PROVEN / NOT PROVEN / SKIPPED, `date`,
`failing_line` or `reason`). The loader is strict: see Gavel's `verdict/SCHEMA.md`.

How a prover uses it (Gavel's `verdict/prove.py --manifest scripts/judges.yaml`, not written here):
for each judge, make a clean `git archive HEAD` export, apply one known-bad mutation, run `run`, and
require an exit code in `fail_exit` whose output matches every `match`. Only then is a green run of the unmutated judge a verdict.
Mutations never run on the working tree. The preflight judge reads gitignored `build/`, so link
the real `build/` into the export read-only. `patch_applies` and `firmware_build` link the real
`zephyr/`: both only read it (`git archive`, `git apply --reverse --check`); no judge may edit it.
`firmware_build` is class `heavy` (a full west build, about 25 s with a warm toolchain).

Add a judge: append an entry, give it at least one single-line mutation (a `sed` command, or a
patch file as `fault:`), run it on an export, record `proof`. No proof means
NOT PROVEN, not PROVEN.
