# The ggml-staging-automation bump loop

This directory holds this workspace's whole side of the imp bump loop:
directory-per-imp under `imps/` (each imp's entry point is
its `imp.py`, with its private resources beside it — the fix
imp's `build.py` validation harness lives in its directory and is
copied into each run workspace), and the paired Condition Scripts under
`conditions/`. The imps are self-contained: they clone
ROCm/ggml-staging-automation directly from GitHub and depend on no
checkout under `sources/`. The imp design record is
[docs/imp-design.md](../../docs/imp-design.md) and the tool architecture
[tools/README.md](../../tools/README.md); this README is the authoritative
record of each script's boundary.

`../imps/fix-llama-bump.py` (hyphenated, under `scripts/pipelines/`)
is the earlier hand-launched spike — an experiment to mine, not an
authority.

This workspace runs its own imp Daemon (one per workspace) from the
workspace root, with a gitignored config naming these imps:

```json
{
  "imps": {
    "fix-llama-bump": "scripts/ggml-staging-automation/imps/fix-llama-bump/imp.py",
    "repoint-llama-bump": "scripts/ggml-staging-automation/imps/repoint-llama-bump/imp.py"
  }
}
```

```sh
impd --config impd.json                # from the workspace root
impwatch arm -- $PWD/scripts/ggml-staging-automation/conditions/red_bump_prs.py
impwatch tick                          # wire into cron for a live loop
```

One-time setup: the imp tools build into `build/bin` (see
[tools/README.md](../../tools/README.md)); `impwatch` must be on PATH —
the fix imp invokes it bare to arm its reconcile watch. All imp
state lives in this workspace's `.imp/`.

## `fix-llama-bump` (`imps/fix-llama-bump/imp.py`)

Arguments: one — the bump PR URL. Launched by
`conditions/red_bump_prs.py` under Run Id `fix-bump-pr-<N>` (`<N>` the PR
number).

Observable behavior: it derives everything from its argument and live
GitHub state — no ticket, no standing context document. Upstream fixes are
consumed, never re-derived, with the check scoped by the bump itself: the
bump pins llama.cpp at upstream's head as of bump time, so a fix merged
before that pin is already in the tree (no staircase step re-proves it),
and a fix merged after it is consumed by a plain llama.cpp pin bump. It
repairs the breakage behind the PR's red CI and drives the PR green as a
staircase of green llama.cpp bumps — each step pairs a llama.cpp fix with
the hrx break it answers, and the last step pins hrx-system directly at
the bump's original target (the restoration rides the pair). The
hrx-system pin is never committed alone — it belongs to the automation's
bump — and the common single-missing-fix case degenerates to exactly one
llama.cpp bump commit with hrx untouched. If and only if the repair
requires a llama.cpp change upstream still lacks, it: pushes that change
to the personal fork, retargets the bump PR's `.gitmodules` at the fork so
the fix is validated in the PR's own CI, opens the upstream PR for the
durable version — its description a table mapping each breaking hrx-system
PR (GitHub link) to its fix commit (GitHub link), ending with
`Validated build + benchmark: <staging PR link>.` — and arms the reconcile
watch before exiting:

```sh
impwatch arm --clear -- <ws>/scripts/ggml-staging-automation/conditions/pr_merged.py <upstream-pr-url> <bump-pr-url>
```

It exits `0` iff the bump PR is green — verified mechanically by the
wrapper (`gh pr checks <bump-pr-url>` exits 0 iff every check passed),
never taken from the agent's self-report. A run that needed no llama.cpp
change simply ends green, arming nothing.

Details the implementation settled:

- The slug naming the run workspace and prefixing the numbered fork
  branches is `fix-bump-pr-<N>`, derived from the URL — deliberately
  identical to the Run Id, so fork branches (`fix-bump-pr-46-1`, …) trace
  to their Run without the Imp ever being told its Run Id.
- codex is the agent runtime (`codex exec` with a schema-forced handoff);
  the handoff's required `upstream_pr` field (URL or null) is what drives
  the arming above — armed before the green check, so a stuck run that
  opened an upstream PR still arms the days-long wait.
- Fix commits carry a `Relevant hrx PR: <full URL>` trailer naming the
  breaking hrx-system PR.

## `repoint-llama-bump` (`imps/repoint-llama-bump/imp.py`)

Arguments: two — the merged upstream PR URL, then the original bump PR
URL. Launched by `conditions/pr_merged.py` under Run Id
`fix-bump-pr-<N>-repoint`.

Observable behavior: if the bump PR has been closed in the meantime — the
automation recreates bump PRs freely — the Run ends green having done
nothing but note it in its log; the next bump PR's fix run absorbs the
merged upstream by preference. Otherwise it repoints mechanically first —
no agent — updating the bump PR to consume the merged upstream in place of
the fork, then watches the PR's CI. Green is the expected case (the same
change was already validated on this PR via the fork) and the Run exits
`0`. Red launches an agent whose whole job is diagnosis: its guess goes to
stderr for the Run's log, and the Run exits nonzero — a failed Run is what
summons the human. No retry semantics.

Details the implementation settled:

- The slug naming the run workspace is `fix-bump-pr-<N>-repoint`, derived
  from the bump PR URL — identical to the Run Id, same trick as the fix
  imp.
- The repoint is index-level only, no submodule checkout: `.gitmodules`
  is rewritten to the canonical coordinates
  (`https://github.com/AMD-Ecosystem/llama.cpp.git`, branch
  `hrx-graph-develop-v2`) and the submodule is pinned at the upstream
  PR's `mergeCommit` via
  `git update-index --cacheinfo 160000,<sha>,llama.cpp` — a submodule pin
  is an ordinary tree entry.
- Everything lands as one commit — headline
  `Repoint llama.cpp at merged upstream`, body naming the upstream PR it
  consumes in place of the fork — pushed to the PR's head branch.
  hrx-system is untouched, per the never-alone rule.
- `gh pr checks <bump-pr-url> --watch --interval 60` is the verdict: its
  exit code becomes the Run's outcome.
- On red, codex is the diagnosis agent (`codex exec` with a schema-forced
  handoff, `{"diagnosis": <string>}` and nothing else) under an explicit
  change-nothing rule; the diagnosis is printed to the Run log before the
  nonzero exit.

## The Condition Scripts (`conditions/`)

Both fulfill impwatch's Condition Script contract: run with armed
arguments, emit zero or more Launch Bodies on stdout (one JSON per line);
stderr and exit code are diagnostics only.

- **`red_bump_prs.py`** — the standing sensor; armed once as a
  non-clearing Watch Row, no arguments. Each Tick it queries GitHub (`gh`)
  for open automation bump PRs with failing CI in
  ROCm/ggml-staging-automation and emits one launch per discovery:
  `{"imp": "fix-llama-bump", "id": "fix-bump-pr-46", "args": ["<pr-url>"]}`.
  The Run Id derives from the PR number alone — one automatic fix per bump
  PR, ever; a PR red again after its fix needs a human.
- **`pr_merged.py`** — the reconcile sensor; armed by fix runs as a
  clearing Watch Row with the upstream PR URL then the bump PR URL. Emits
  nothing until the upstream PR reaches merged, then the single
  `repoint-llama-bump` launch (`fix-bump-pr-<N>-repoint`, N from the bump
  PR).
