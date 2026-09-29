# The bump-automation loop

This directory is this workspace's whole side of the bump loop that
[docs/imp-design.md](../../../../../docs/imp-design.md) tells the story of,
and this README is the authoritative record of each executable's boundary.
The loop's layout follows [scripts/imps/README.md](../../../README.md); it
is one loop of the [ggml-staging-automation](../../README.md) project.

The loop has two Imps of its own and two standing Watches.
`fix-llama-bump` repairs a red bump PR and, when it had to change
llama.cpp, opens the upstream PR and arms the wait for it.
`repoint-llama-bump` consumes the merged upstream when that wait fires.
The workspace's [overseer](../../../overseer/imp.py) checks the newest
`fix-llama-bump` Run every weekday morning and opens a thread for the
human when one is needed. `sync_pr_body.py` keeps the bump PR's
description truthful while the bump Imps move its llama.cpp pin. The
project's shared [build.py](../../build.py) provides local build
infrastructure for the fix Imp. All of them clone
`ROCm/ggml-staging-automation` from GitHub and depend on no checkout under
`sources/`.

The Manifest, [bump-loop.json](bump-loop.json), inscribes the loop's two
Sigils and the workspace's `overseer`, and declares two standing Watches.
One is the fix Sensor with no arguments. The other is the overseer's
Sensor with the Sigil to oversee (`fix-llama-bump`), its slot
(`09:00 America/Boise`, weekdays, a few hours after the daily bump
workflow at 11:00 UTC), and the codex model and effort for the check.
Bring it up with:

```sh
impctl up --manifest scripts/imps/ggml-staging-automation/loops/bump-automation/bump-loop.json
```

Prerequisites: `gh`, `codex`, and `claude` logged in, and `impctl` on
`PATH` when `up` runs (the workspace `.envrc` does this), because the fix
Imp invokes it bare and every Run inherits the Daemon's environment.

## `fix-llama-bump`

Argument: the bump PR URL. Launched by its `sensor.py` under Run Id
`fix-bump-pr-<N>`, `<N>` the PR number. It derives everything else from
live GitHub state: no ticket, no standing context document.

It exits 0 iff the bump PR is green, verified mechanically by the wrapper
(`gh pr checks` exits 0 iff every check passed), never taken from the
agent's self-report. A break that cannot be fixed in llama.cpp alone ends
the Run with the problem described in the Run log and a red PR, which
fails the Run.

It drives the PR green as a staircase of green llama.cpp bumps: each
commit pairs a llama.cpp fix with the hrx-system break it answers, and the
last pins hrx-system at the bump's original target. The hrx-system pin is
never committed alone, because a lone pin move shows nothing; the common
single-missing-fix case is one llama.cpp bump commit with hrx-system
untouched. hrx-system itself is never edited or pushed, and mechanically
so: its checkout's push URL is set to something that is not a URL, the
same way llama.cpp's push URL is pinned to the personal fork.

Upstream fixes are consumed, never re-derived: a fix merged before the
bump's llama.cpp pin is already in the tree, one merged after it is
consumed by a plain pin bump, and only what upstream lacks entirely is
derived here.

When a bump makes a model's perplexity pass where the staging manifest
(`benchmarks/hrx/model_manifest.json`) expected a failure, the Run removes
that `"perplexity": "fail"` and its paired `"lemonade-benchmark": "skip"`
together, so the model is benchmarked again. Green CI cannot enforce this,
because a skipped check never fails; the Imp's standing instructions do.

If and only if the repair needed a llama.cpp change upstream still lacks,
the Run pushes it to the personal fork on numbered branches
(`fix-bump-pr-<N>-1`, ...), retargets the bump PR's `.gitmodules` at the
fork so the fix is validated in the PR's own CI, opens the upstream PR
from a fixed template (Motivation, a table of breaking hrx-system PRs to
fix commits, Testing with the validated hashes and run link), and arms the
reconcile Watch before the green check, so a stuck Run that opened an
upstream PR still leaves the days-long wait armed:

```sh
impctl watch --once -- <ws>/scripts/imps/ggml-staging-automation/loops/bump-automation/repoint-llama-bump/sensor.py <upstream-pr-url> <bump-pr-url>
```

`codex login status` must pass before anything is cloned. codex can go
weeks between uses and its login lapses; the Run fails with a log line
saying so, and the human runs `codex login` and relaunches under a fresh
Run Id.

The run workspace under `/tmp` and the fork branches are named
`fix-bump-pr-<N>`, identical to the Run Id, so everything a Run made traces
back to it without the Imp being told its Id. The rest of the
run-workspace prep, and why each step exists, is the Imp's header.

## `repoint-llama-bump`

Arguments: the merged upstream PR URL, then the original bump PR URL.
Launched by its `sensor.py` under Run Id `fix-bump-pr-<N>-repoint`.

If the bump PR is in any state other than OPEN, the Run ends green having
done nothing but name the state in its log: the automation recreates bump
PRs freely, and the next bump PR's fix Run absorbs the merged upstream by
preference. A bump PR that MERGED while still pointed at the fork lands
here too and is a human matter; the log line is this Run's whole
contribution to it.

Otherwise it repoints mechanically, with no agent and no submodule
checkout: `.gitmodules` back to the canonical coordinates
(`https://github.com/AMD-Ecosystem/llama.cpp.git`, branch
`hrx-graph-develop-v2`) and the pin moved to the upstream PR's merge
commit as an index entry, since a submodule pin is an ordinary tree entry.
It lands as one commit (`Repoint llama.cpp at merged upstream`) pushed to
the PR's head branch, hrx-system untouched.

Then CI decides. The Run waits until the PR's head is the pushed commit
and its check rollup is non-empty, because a watch started earlier returns
the previous head's stale green within seconds (it happened); that wait
failing after ten minutes is a failed Run, never a guessed green. It then
syncs the PR body from the pushed sha and runs `gh pr checks --watch`,
whose exit code is the Run's. Green is expected, since the same change was
validated on this PR via the fork; that expectation is why there are no
retry semantics.

Red launches codex for a diagnosis only, under an explicit change-nothing
rule, schema-forced to `{"diagnosis": <string>}`; the diagnosis is printed
to the Run log and the Run exits nonzero so it is flagged as needing
attention. The run workspace is named after the Run Id, as in the fix Imp.

## The Sensors

Both `sensor.py` files honor the Sensor contract and live
beside the Imp they launch.

`fix-llama-bump/sensor.py` is the fix Sensor, a standing Watch declared in the
Manifest with no arguments. Each Tick it lists open PRs in
`ROCm/ggml-staging-automation` whose head is the automation's bump branch
(`users/automation/bump-submodules`) and emits one Launch per PR whose
check rollup holds a FAILURE; a rollup still churning is "not yet". The Run
Id derives from the PR number alone, so a bump PR gets one automatic fix,
ever: a PR red again after its fix is a silently rejected duplicate, and
[When this loop needs a human](#when-this-loop-needs-a-human) names that
case.

`repoint-llama-bump/sensor.py` is the reconcile Sensor, armed by fix
Runs as a one-shot Watch with the upstream PR URL then the bump PR URL. It
emits nothing until the upstream PR is MERGED, then the single repoint
launch. Any other state, CLOSED included, is "not yet": a closed-unmerged
upstream PR keeps the Watch pending forever, on purpose, where the human
sees it in `impctl watches` and judges.

## The overseer

The loop has no overseer of its own. Its Manifest declares a standing
Watch on the workspace's overseer with `--sigil fix-llama-bump`, so each
weekday slot launches one Run,
`oversee-fix-llama-bump-<YYYY-MM-DD>-<HHMM>`, that checks the newest
`fix-llama-bump` Run. How it checks, the thread it opens, and what it
cannot see are in [its header](../../../overseer/imp.py).

The overseer starts from the fix Run and follows what that Run's log
names. The repoint Run is reached that way: its Run Id is the fix Run's
with `-repoint` appended, and the fix Run's log holds the upstream PR and
the Watch it armed.

The judgment it applies is the next section, which it reads from here.

## When this loop needs a human

This is the loop's judgment: what it leaves for the human and what it
does not. The overseer's check reads it, and so does the human.

The loop needs a human when:

- A bump PR is green and ready to merge, after a fix Run or after a
  repoint. The loop never merges; the human does.
- An upstream llama.cpp PR opened by a fix Run is open with no recent
  review activity. The fix Run's log names it in the handoff's
  `upstream_pr`, and so does the argv of the one-shot Watch the Run
  armed. The human chases the review or merges it.
- A bump PR is red again after its fix Run. The Run Id `fix-bump-pr-<N>`
  is occupied, so the Sensor's relaunch is a rejected duplicate and
  nothing else reports it.
- A Run failed and its bump PR is still open. Quote the tail of its log.
  The common causes: the codex login lapsed (the Run says so before
  cloning anything); a break that could not be fixed in llama.cpp alone
  (the log describes the hrx-system change needed); a PR red after a
  repoint (the log holds a diagnosis).
- A pending one-shot Watch's upstream PR is CLOSED without merging. The
  Watch pends forever, on purpose; the human decides.
- A bump PR MERGED while still pointed at the fork. The repoint Run
  names the state in its log and does nothing else.

The loop does not need a human when:

- A Run is `running`. Fix Runs can take hours.
- An upstream PR is OPEN with recent review activity. Say so in the
  findings; it is not a summons.
- A bump PR is red with no fix Run yet and the fix Sensor ticked
  recently. The loop will pick it up.
- A Run failed and its bump PR has since merged or closed. That is
  history: the next bump PR gets its own fix Run.
- A bump PR was closed and recreated by the automation. The repoint Run
  for the old one ends green having done nothing.

## `sync_pr_body.py`

The bump PR's body carries a submodule table whose llama.cpp row names a
pin the loop moves under it. It re-derives that row (repo link,
branch, "To" cell) from the PR head; the hrx-system row is never touched,
because the loop never moves that pin.

Callers that just pushed pass `--head <sha>`, because the PR API can
report the previous head for a while after a push and a sync trusting it
writes the state the branch just left (it happened); the contents API at
an explicit sha has no such lag. A body already in sync is left alone, a
body without the bot's row is left untouched with a warning, and the exit
code is nonzero only when `gh` itself fails.
