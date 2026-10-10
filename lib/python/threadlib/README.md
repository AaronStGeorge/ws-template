# threadlib: launching a thread in this workspace

A thread is one Codex session working in its own worktree of this
workspace, hosted on Codex's daemon so it appears in the agent command
center (`/agents`) as a live, named, openable row. threadlib is the one
program that makes a thread, and every door onto a new thread goes through
it: the `thread` executable for a human at a terminal, the library for an
Imp or an agent that starts a thread from inside its own. This README is
the design record for the component; the code does not exist yet, and the
decisions here were settled in a grilling session on 2026-10-10.

## Why

Starting work on something today means cloning the right repositories
into `sources/` by hand, starting `codex`, and telling it what to do, and
an Imp does the same in a temp directory with `codex exec`, invisible until
its Run log is read. The workspace wants one motion for both: name the
work, and get a session in a fresh worktree with the right sources checked
out, visible alongside every other session in the command center, and
reachable by other agents while it runs.

The requirements, from the session:

- A thread is launched by a bead id or by a plain name; a bead is optional.
- The thread is named after the bead (or the name), and its worktree holds
  the sourceset the bead asks for.
- Every thread looks and behaves the same in the command center, whether a
  human or an Imp started it: a live status, openable, steerable.
- Other agents can send a running thread a message.
- Every door (terminal, skill, Imp) is the same program.

## Language

This component inherits the imp design record's Language
([docs/imp-design.md](../../../docs/imp-design.md)) and adds:

**Workspace** — a clone of this repository: skills, the imp runtime,
`sources/`. The imp design record already uses the word this way.

**Sourceset** — a checked-in manifest naming the repositories that fill
`sources/`, plus assets that belong beside them. Today it lives at
`workspaces/<name>/workspace.json` and is loaded by
[scripts/ws_load.py](../../../scripts/ws_load.py); the settled name is
`sourcesets/<name>/sourceset.json`, and the rename lands with the loader.

**Thread** — one Codex session in one worktree of the workspace, hosted
on Codex's daemon, named after its bead or its given name.

**Bead** — an issue in the workspace's `br` store (`.beads/` in the main
checkout). A bead names its sourceset with the label `sourceset:<name>`.

**Codex's daemon** — Codex's local app-server daemon, which hosts threads
and serves the command center. Never "the Daemon": in this workspace that
word is `impd`.

**Door** — a way of asking for a new thread. The human door is the
`thread` executable; the non-human door is an Imp calling the library.

## What threadlib commits to

The human door:

```
thread new <bead-id>                      # named after the bead; sourceset from its label
thread new --name <slug> --sourceset <s>  # no bead; both flags required
```

Either form ends with an interactive `codex` attached to the new thread,
inside the new worktree, with the thread already named. The non-human door
is the same steps as a library call that ends with the opening prompt sent
and the call blocking until the turn is done; it returns the turn's outcome
for the Imp to turn into an exit code.

What both doors guarantee once they return:

- A detached worktree of the workspace exists for the thread, with
  `sources/` holding the sourceset's repositories and assets.
- A thread named after the bead (or the slug) exists on Codex's daemon with
  the worktree as its cwd, so it is a row in the command center from its
  first second and `codex resume <name>` and `codex queue --thread <name>`
  both reach it.
- With a bead, the thread's opening prompt says to work on that bead. That
  is the whole bead process for now; claiming, branching, handoff and close
  are deliberately unspecified until use shows what they should be.

## Design

### The six steps

Every door runs the same steps; only the last differs.

1. **Name.** The bead id, or the given slug. Thread and worktree carry it.
2. **Worktree.** `git worktree add --detach` of the workspace at
   `.threads/<name>` inside the clone, gitignored like `.imp/`, so a
   thread's worktree is spelled the same as its thread and its bead, and
   everything about a workspace stays in the workspace. The workspace
   worktree takes no branch; only sourceset checkouts would, and that is
   part of the open bead process. `sources/` is gitignored, so it starts
   empty.
3. **Sources.** Load the sourceset into the worktree's `sources/`, taken
   from the bead's `sourceset:` label or the `--sourceset` flag. Full
   clones, every time; a clone cache is a later addition that does not
   change this shape.
4. **Bead.** Nothing to wire. `br` run inside a linked worktree that has no
   `.beads/` of its own resolves to the primary checkout's `.beads/`
   through the worktree's `.git` file (tested with br 0.7.4 from a worktree
   outside the clone, one inside it, and from a nested repo under its
   `sources/`).
5. **Thread.** Ensure Codex's daemon is up, connect to its socket, send
   `thread/start` with the worktree as cwd and the door's approval and
   sandbox settings, then `thread/name/set`. The human door passes
   nothing and gets the user's own Codex defaults; the Imp door sets
   approvals to never and the sandbox off, as the imps run today, because
   a thread that paused for approval would hang with no one watching and
   the imp design record's constraints ask for no hardening. Both doors do this, which is
   what makes their threads indistinguishable in the command center.
6. **Door.** Human: exec `codex resume <thread>` in the worktree. Imp: send
   `turn/start` with the prompt, stream the thread's events into the Run
   log, return the turn's outcome. The thread stays on the daemon
   afterwards as a Ready row.

### Why the thread lives on Codex's daemon and not in `codex exec`

`codex exec` threads do appear in the command center, but only as
"Inactive" rows: status comes from threads the daemon itself has loaded,
exec runs its own in-process server, and opening a running exec thread
gives a frozen read-only snapshot because exec holds the writer lock. New
exec threads also go unnoticed by an already-open command center until it
reconnects. Hosting on the daemon is the only path that gives an Imp's
thread a live "Working" row and lets `codex queue` reach it mid-run.

Verified against codex-cli 0.162.1: a plain `codex app-server` on stdio
accepts `initialize`, `thread/start {cwd}`, `thread/name/set` with no
login; the name lands in the state DB and `session_index.jsonl` before any
turn; `codex exec resume <name>` then resolves it; `codex queue --thread
<name> --message` writes to the shared queue, which the hosting server
drains when the thread goes idle. The daemon's own socket is
`$CODEX_HOME/app-server-control/app-server-control.sock`, JSON-RPC over
WebSocket, mode 0600, no token. Codex's managed worktrees (`--worktree`)
were rejected: random path, always detached, no branch, no base ref, and
only Codex's own door can make one, so a bead id cannot enter through them.

### Decisions in force

- One library, two doors. The library is the launcher; `thread` is a thin
  executable over it; an Imp imports it. No MCP server until a shell-less
  client needs one; a skill wrapping the executable serves agents.
- The Imp door is a Run through `impd`, so an Imp's thread has a Run Id and
  a log as every Run does. Imps have no beads to start with, so an Imp's
  thread is named after its Run Id and the Imp names the sourceset itself.
  How a Run Id and a bead id relate is decided when an Imp first has a
  bead, not before.
- Every Imp moves onto this, the bump loop included; temp-dir clones and
  `codex exec` go away.
- A bead is optional and the thread's name comes from it when present.
- `impctl` is not the launcher. The imp design record asks it to be the
  dumb Daemon client; git worktrees, sourcesets and Codex's daemon are all
  outside that boundary, and the launcher is a Client of `impd` when it
  launches a Run.
- The bead process beyond the opening prompt is unspecified on purpose, to
  be discovered by use.
- Sourceset loading moves into threadlib as a function, and
  `scripts/ws_load.py` becomes a thin command over it for filling the main
  checkout's `sources/` by hand, which stays a real use: work is loaded
  there outside any thread for review and for human-in-the-loop work. The
  refuse-if-unsaved check stays on that by-hand path only, the one place a
  load can destroy work; the thread path always targets an empty
  `sources/` and carries no guard that cannot fire.

### Open

- What a thread is told about beads beyond "work on bead `<id>`".
