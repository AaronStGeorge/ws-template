# Imp loops

Everything imp in this workspace lives here: the loops themselves (one
directory each, e.g. [ggml-staging-automation/](ggml-staging-automation/),
each may have its own overseer), the one checked-in config
[imps.json](imps.json), and [loops.py](loops.py), which brings a
workspace's loops up and down. The runtime (`impd`, `impwatch`,
`impctl`) is documented in [tools/README.md](../../tools/README.md) and
its design record in [docs/imp-design.md](../../docs/imp-design.md).

## Running

```sh
scripts/imps/loops.py up --workspace lemonade-ws   # daemon, watches, ticker
scripts/imps/loops.py status                       # processes, runs, watches, log tail
scripts/imps/loops.py down                         # ticker, then daemon; watches stay
```

`up` is safe to rerun: each step is skipped when already done. `down`
never removes Watch Rows, so a pending reconcile watch survives a
restart. The container has no crond; the ticker is `loops.py tick`,
started detached by `up`. Tools come from `build/bin` (the `.envrc`
builds them); `gh`, `codex` and `claude` must be logged in.

## The config

`imps.json` has two sections, `loops` and `workspaces`.

`loops` declares each loop by name. A loop has two parts:

- `imps`: imp name → workspace-relative path to its `imp.py`. This is
  what the Daemon is configured with.
- `watches`: the standing Watch Rows `up` arms once at startup, each as
  an argv list (`condition.py` first, then its arguments). argv[0] is
  workspace-relative in the file and made absolute when armed.

Only standing rows belong in `watches`. Clearing (one-shot) rows are
armed by imps mid-Run with arguments that only exist then — the fix
imp's reconcile watch names the upstream PR it just opened — so they
have no place in a checked-in config.

A loop's overseer is an ordinary pair of entries: its `overseer` imp,
plus a watch row for that imp's `condition.py` carrying the daily slot
times and zone and the codex model and effort for the check.

`workspaces` maps a workspace name to the loops it runs;
`up --workspace <name>` selects one. The selected loops' `imps` are
merged into one Daemon config, so two loops may share an imp name only
at the same path.

## The overseer

Each loop may have an overseer imp: a check at fixed daily slots
(`oversee-<loop>-<date>-<HHMM>` Runs) that decides whether a human is
needed — a PR waiting on review or merge, a failed Run, a watch that
will never fire. The judgment is the loop's brief, a prose
constant in its `imp.py`; the mechanics are in that file's header. There is no generic overseer
yet; what is generic will be clearer once there are more loops.

When a human is needed, the overseer opens a *thread*: a Claude Code
session with Remote Control, named after the Run that opened it, which
digs in, pushes one headline to the human's phone, and waits to be told
what to do. There is at most one thread per loop.

Later checks keep that thread current. The check reads the thread's
transcript so far, and if the same issue still needs a human the thread
is resumed with the new verdict and pushes a status line; if a different
issue does, the old thread is archived and a new one opened; if nothing
does, the thread is archived, silently.

Archiving is `claude stop` + `claude rm`: the session leaves the active
list but its transcript stays on disk and resumable by the id in the
Run log. Deleting a thread yourself is fine too — it just means the
next check has no context.

If the Daemon or the ticker dies, the overseer dies with them and
nothing reports it; `status` is the manual check for that.
