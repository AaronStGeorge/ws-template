# sourceset: the sources a piece of work needs

A sourceset is a checked-in manifest, `<name>/sourceset.json` under a
sourcesets directory, the workspace's `sourcesets/` by default, naming the
repositories that fill a checkout's `sources/`, plus an optional `assets/`
directory of files that belong beside them. This package reads
one and loads it. Two callers do: the workspace thread launcher fills a new
worktree's `sources/` ([workspace_thread](../workspace_thread/README.md)),
and [scripts/ws_load.py](../../../scripts/ws_load.py) fills the main
checkout's by hand.

## Why

Work in this workspace is on other repositories, at particular refs: a
kernel library, a llama.cpp fork, a staging clone. Which ones is a fact
about the work, not about whoever starts it, so it is written down once
and asked for by name, from a bead's `sourceset:<name>` label, a
`--sourceset` flag, an Imp's constant or a human's command line. The
requirements:

- One name fills `sources/` the same way for a human, an agent and an Imp.
- A sourceset can carry files that belong beside its clones, and an edit
  made to one through `sources/` is an edit to the tracked copy.
- Filling the main checkout's `sources/` by hand never loses work that
  exists only there.
- Where sourcesets are kept is the workspace's choice: its own
  `sourcesets/` unless a command is told another directory.

## Language

This package inherits **Workspace** from the imp design record
([docs/imp-design.md](../../../docs/imp-design.md)), a clone of this
repository, and adds:

**Sourceset** — `<name>/` under the sourcesets directory: a
`sourceset.json` manifest listing repositories, each a `url` and an
optional `ref`, and an optional `assets/` directory. The directory's name
is the sourceset's.

**Sourcesets directory** — where sourcesets are read from: the workspace's
`sourcesets/`, `SOURCESETS_DIR`, unless the command was given another with
`--sourcesets-dir`.

**Checkout** — the main checkout or any worktree of the workspace. A load
targets one.

**sources/** — the gitignored directory in a checkout that holds a
sourceset's clones and asset links. Every fresh checkout starts with it
empty.

**Asset** — a file under a sourceset's `assets/`, linked into `sources/`
beside the clones. What an asset is for is not this package's concern; the
`build.py` the build sourcesets carry is the builds side's convention
([lib/python/builds](../builds)).

**By-hand door** — `scripts/ws_load.py`, the way of filling the main
checkout's `sources/` from a sourceset, kept for review and
human-in-the-loop work outside any workspace thread.

## What sourceset commits to

`read_sourceset(sourcesets_dir, name)` returns the `Sourceset` under
`sourcesets_dir`: its name, its repositories, and its `assets/` path or
`None`. The loader takes only a directory; `SOURCESETS_DIR` names the
default a caller with a workspace applies.
The name is checked here, the one place every caller passes through:
lowercase letters and digits, hyphen-joined, else `ValueError`; a name
with no manifest is `FileNotFoundError`. The
manifest's shape is trusted once the file exists, because it is repository
content.

`load_sourceset(sourceset, checkout, log)` fills `<checkout>/sources/` and
returns that directory: one full clone per repository, named after the
last segment of its URL without `.git`, checked out at its `ref` when one
is given, then one relative symlink per asset. Git's output goes to `log`;
an Imp passes its stderr so the clones show in the Run log. A load expects
an empty `sources/` and neither refreshes nor merges: a clone into an
occupied directory fails as git fails it.

`python scripts/ws_load.py <name> [--sourcesets-dir DIR]` empties the main
checkout's `sources/` and loads the sourceset into it, from `DIR` when one
is given. It refuses, naming each entry and its
reason, when anything there exists only locally: an entry that is neither
a symlink nor a git repository, uncommitted or untracked files, or commits
on no remote. A symlink is removed without looking through it, since
removing the link never removes what it points to.

## Design

Reading checks only the name. It arrives from outside and selects a
directory, so it is validated at the one boundary every caller crosses,
and the manifest behind it, being checked in, is not validated again.

A clone is `git clone` and then, with a `ref`, `git checkout <ref>`, both
on git's own defaults: a remote branch name becomes a local tracking
branch, a tag or a SHA detaches. Full clones every time; a clone cache is
a later addition that would live inside `clone` alone.

Assets are symlinked with relative targets, so the links survive the
repository being moved or mounted at another path, and an edit made
through `sources/` lands in the tracked `assets/` directory.

### Decisions in force

- Sourceset loading is this package's, and the workspace thread launcher
  calls it. It sat inside workspace_thread until 2026-10-20, when the
  by-hand door, with no thread in sight, had to import the thread library
  to fill `sources/`.
- The guard against losing work is the by-hand door's, not the loader's.
  That path is the one place a load can destroy something; a workspace
  thread's worktree always offers an empty `sources/`, and the loader
  carries no guard that cannot fire.
- What an asset is for is not decided here. The loader links whatever is
  under `assets/`; `build.py` is the builds side's convention.
- The name rule is the loader's, checked in `read_sourceset`, so a bad
  name from any caller fails the same way and before anything is cloned.
- The loader takes the sourcesets directory and the default is the
  caller's to apply, so the directory is chosen per command: ws_load.py
  takes `--sourcesets-dir`, and the workspace thread entries take theirs
  as their record says.

## Tests

`test/sourceset_test.py` reads and loads a sourceset against real git: a
temporary workspace whose one sourceset names a local repository at a
tagged ref and carries one asset, then the clone, its ref and the relative
link are checked in `sources/`, and a sourceset in another directory is
read from there. It needs only git and touches nothing
outside the temporary directory, so it is cheap enough for every edit:

```sh
.venv/bin/python -m unittest test.sourceset_test
```

The workspace thread e2e suite loads a sourceset through the launcher as
well.
