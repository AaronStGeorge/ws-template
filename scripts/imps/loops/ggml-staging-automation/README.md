# The ggml-staging-automation bump loop

This directory is this workspace's whole side of the bump loop that
[docs/imp-design.md](../../../../docs/imp-design.md) tells the story of,
and this README is the authoritative record of each executable's boundary.
The loop's layout follows [../../README.md](../../README.md).

The loop is three Imps, two standing Watches, and one shared helper.
`fix-llama-bump` repairs a red bump PR and, when it had to change
llama.cpp, opens the upstream PR and arms the wait for it.
`repoint-llama-bump` consumes the merged upstream when that wait fires.
`overseer` checks on the loop at fixed daily slots and keeps the human's
one thread about it current. `sync_pr_body.py` keeps the bump PR's
description truthful while the other two move its llama.cpp pin. All of
them clone `ROCm/ggml-staging-automation` from GitHub and depend on no
checkout under `sources/`.

The Manifest, [imps.json](imps.json), inscribes the three Sigils and
declares two standing Watches: the fix Sensor with no arguments, and the
overseer's Sensor with its slots (`09:00,14:00 America/Boise`, a few hours
after the daily bump workflow at 11:00 UTC) and the codex model and effort
for the check. Bring it up with:

```sh
impctl up --manifest scripts/imps/loops/ggml-staging-automation/imps.json
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

If and only if the repair needed a llama.cpp change upstream still lacks,
the Run pushes it to the personal fork on numbered branches
(`fix-bump-pr-<N>-1`, ...), retargets the bump PR's `.gitmodules` at the
fork so the fix is validated in the PR's own CI, opens the upstream PR
from a fixed template (Motivation, a table of breaking hrx-system PRs to
fix commits, Testing with the validated hashes and run link), and arms the
reconcile Watch before the green check, so a stuck Run that opened an
upstream PR still leaves the days-long wait armed:

```sh
impctl watch --once -- <ws>/scripts/imps/loops/ggml-staging-automation/repoint-llama-bump/sensor.py <upstream-pr-url> <bump-pr-url>
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
the overseer's brief names that case.

`repoint-llama-bump/sensor.py` is the reconcile Sensor, armed by fix
Runs as a one-shot Watch with the upstream PR URL then the bump PR URL. It
emits nothing until the upstream PR is MERGED, then the single repoint
launch. Any other state, CLOSED included, is "not yet": a closed-unmerged
upstream PR keeps the Watch pending forever, on purpose, where the human
sees it in `impctl watches` and judges.

## The overseer and the thread

The overseer is this loop's own: its loop name is `ggml-bump`, and its
brief and Imp names are constants in `overseer/imp.py`. A generic overseer
can wait until more loops show what is generic.

**Thread** — the human's channel for this loop: a Claude Code session
(`claude --bg --remote-control`, Fable, auto permissions) that digs into
what a check found, pushes one headline to the human's phone, and waits to
be directed, changing nothing until told to. There is at most one thread
per loop, named after the Run that opened it and found by that prefix in
`claude agents --json --all`, newest first.

**Check** — one `codex exec`, pinned to the Manifest's model and effort,
schema-forced to `{needs_human, headline, findings, same_issue}`, given
the brief, the live state (`impctl runs`, `.imp/`, `gh`), and the thread's
transcript so far. It runs at every slot whether or not anything is wrong.

**Archive** — `claude stop` then `claude rm`: the session leaves the
active list; its transcript stays on disk, resumable by the id in the Run
log. No thread state is kept anywhere else.

`overseer/sensor.py` emits, on every Tick, one Launch per slot
already past in the zone's local day, under Run Id
`oversee-ggml-bump-<YYYY-MM-DD>-<HHMM>` with `HHMM` the slot's configured
time, so each slot runs once. There is deliberately no firing window: a
slot missed while the machine was down runs late, and a Daemon restart
mid-day re-runs the slot.

Each check ends in one of five actions against the thread: no thread and
a human needed opens one; the same issue resumes it with the new verdict
(it re-verifies, pushes one status line, and honors what the human said
earlier); a different issue archives it and opens a new one; no human
needed archives it silently, since absence of pings says what a closing
one would; nothing and nothing does nothing.

A resume waits for the stop to land and treats a forked copy as a failed
Run. `claude stop` returns before the session's process is gone, and a
resume issued in that window starts a copy under a new id without the
saved settings, whose push is then dropped and which the next check
cannot find. So the stop is followed by a bounded wait for the listing to
show the session gone, and a resume whose output announces a copy stops
and removes the copy and exits nonzero, so the next check sees the
failure in `impctl runs`. This bit once in production.

An unreadable thread transcript means the overseer is broken, most likely
by a Claude Code update, not a check to run with less context: the thread
is resumed with a "fix me" prompt so the human hears about it and the Run
exits nonzero.

A lapsed codex login is escalated as a verdict of its own without running
the check, because it is the loop's likeliest failure and would blind the
check itself; Claude is still available when codex is not.

The Run exits 0 whenever the check completed, whatever its verdict;
`needs_human` is not a failure. Nonzero means the check or the thread
handling itself broke, and the next slot's check sees that Run. If the
Daemon dies, the overseer dies with it and nothing reports it;
`impctl status` is the manual check for that.

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
