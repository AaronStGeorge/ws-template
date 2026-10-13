# threadlib: launching a thread in this workspace

A thread is one Codex session working in its own worktree of this
workspace, hosted on Codex's daemon so it appears in the agent command
center (`/agents`) as a live, named, openable row. threadlib is the one
program that makes a thread, and every door onto a new thread goes through
it: the `thread` executable for a human at a terminal, the library for an
Imp or an agent that starts a thread from inside its own. This README is
the design record for the component, settled in a grilling session on
2026-10-10 and implemented on the same branch; the code is the files beside
it, each with a header saying what it owns.

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
- An agent can start a sibling thread and carry on without waiting for it.

## Language

This component inherits the imp design record's Language
([docs/imp-design.md](../../../docs/imp-design.md)) and adds:

**Workspace** — a clone of this repository: skills, the imp runtime,
`sources/`. The imp design record already uses the word this way.

**Sourceset** — a checked-in manifest, `sourcesets/<name>/sourceset.json`,
naming the repositories that fill `sources/`, plus an `assets/` directory
of files that belong beside them. sourceset.py reads and loads one;
[scripts/ws_load.py](../../../scripts/ws_load.py) is the by-hand door for
the main checkout's `sources/`.

**Thread** — one Codex session in one worktree of the workspace, hosted
on Codex's daemon, named after its bead or its given name.

**Bead** — an issue in the workspace's `br` store (`.beads/` in the main
checkout). A bead names its sourceset with the label `sourceset:<name>`.

**Codex's daemon** — Codex's local app-server daemon, which hosts threads
and serves the command center. Never "the Daemon": in this workspace that
word is `impd`.

**Door** — a way of asking for a new thread. Three exist: the human door
(`thread new`, ending in an interactive session), the agent door
(`thread new --prompt` or MCP, starting the first turn and returning), and the
Imp door (the library, blocking until the turn is done).

## What threadlib commits to

The human door:

```
thread new <bead-id>                          # named after the bead; sourceset from its label
thread new --name <slug> [--sourceset <s>]    # no bead; without a sourceset sources/ stays empty
thread new <bead-id-or-flags> --prompt TEXT   # agent door: start the first turn, print the name, return
```

Without `--prompt`, either form ends with an interactive `codex` attached
to the new thread, inside the new worktree, with the thread already named;
on a bead, the opening prompt ("Work on bead `<id>`...") is typed for you
as the first turn. With `--prompt`, the agent door, the thread's first turn
is started with that prompt (after the bead prompt, when there is a bead)
and the command returns at once, printing the thread's name; the caller
follows up with `codex queue --thread <name>` or by opening the thread.
The Imp door is the library: `prepare(name, sourceset, settings)` runs the
shared steps and returns the thread and the daemon connection, and
`run_turn(client, thread, prompt, output_schema)` sends the opening prompt,
streams the turn's items into the Run log, and returns a `TurnResult`
(status, error, last agent message) for the Imp to turn into an exit code.
`IMP_DOOR_SETTINGS` is the Imp door's approval and sandbox choice; an Imp
adds a `model` or `config` to it when its Manifest configures one.

What every door guarantees once it returns:

- A detached worktree of the workspace exists at `.threads/<name>`, with
  `sources/` holding the sourceset's repositories and assets, or empty
  when no sourceset was named.
- A thread named `<name>` exists on Codex's daemon with the worktree as its
  cwd, so it is a row in the command center from its first turn and
  `codex resume <name>` and `codex queue --thread <name>` both reach it.
- With a bead, the thread's opening prompt says to work on that bead. That
  is the whole bead process for now; claiming, branching, handoff and close
  are deliberately unspecified until use shows what they should be.

A name that already has a worktree is refused, with the `git worktree
remove` that frees it: a thread name is also a Codex thread name, and two
threads sharing a checkout would share each other's half-done work. This
is what an Imp hits when a Run is relaunched for the same PR; removing the
old worktree is part of the relaunch. Names are the Run Id slug shape
(lowercase, digits, hyphens, at most 64 characters), checked where they
enter: the command line, and a bead id read back from `br`.

Failures before the thread exists (no such sourceset, a bead without a
`sourceset:` label, a bead `br` does not know, a worktree in the way) exit
1 with one line on stderr. A worktree that was cut before a later step
failed is left in place for inspection, like a failed Run's log.

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
   from the bead's `sourceset:` label or the `--sourceset` flag; none
   leaves `sources/` empty, for threads that work on the workspace itself
   (the overseer's checks). Full clones, every time; a clone cache is a
   later addition that does not change this shape.
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
6. **Door.** Human: exec `codex resume <thread>` in the worktree. Agent:
   send `turn/start` with the prompt, print the thread's name, return.
   Imp: send `turn/start` with the prompt, stream the thread's events into
   the Run log, return the turn's outcome. In every case the thread stays
   on the daemon afterwards as a Ready row.

### Why the thread lives on Codex's daemon and not in `codex exec`

`codex exec` threads do appear in the command center, but only as
"Inactive" rows: status comes from threads the daemon itself has loaded,
exec runs its own in-process server, and opening a running exec thread
gives a frozen read-only snapshot because exec holds the writer lock. New
exec threads also go unnoticed by an already-open command center until it
reconnects. Hosting on the daemon is the only path that gives an Imp's
thread a live "Working" row and lets `codex queue` reach it mid-run.

Verified against codex-cli 0.162.1: the daemon's socket,
`$CODEX_HOME/app-server-control/app-server-control.sock` (a symlink into
`/tmp`; JSON-RPC 2.0 over WebSocket, mode 0600, no token), accepts
`initialize`, `thread/start {cwd, approvalPolicy, sandbox, model, config}`,
`thread/name/set` and `turn/start {input, outputSchema}` with no login; the
name lands in the state DB and `session_index.jsonl` before any turn;
`codex exec resume <name>` then resolves it; a turn keeps running after the
client that started it disconnects; `codex queue --thread <name> --message`
writes to the shared queue, which the hosting server drains when the
thread goes idle. codex.py is the client, on the `websockets` package; it
reads the socket path from `CODEX_HOME` and starts the daemon with `codex
app-server daemon start`, which is a no-op when it is already up. Codex's managed worktrees (`--worktree`)
were rejected: random path, always detached, no branch, no base ref, and
only Codex's own door can make one, so a bead id cannot enter through them.

### Decisions in force

- One library, three doors. The library is the launcher; `thread` is a
  thin executable over it for the human and agent doors; an Imp imports it.
  The MCP server adds a structured creation tool for agents, using the same
  assignment resolution and launcher as the CLI. Codex's native tools own
  reading, messaging, and waiting; the workspace does not duplicate them.
- The Imp door is a Run through `impd`, so an Imp's thread has a Run Id and
  a log as every Run does. Imps have no beads to start with, so an Imp's
  thread is named after its Run Id and the Imp names the sourceset itself.
  How a Run Id and a bead id relate is decided when an Imp first has a
  bead, not before.
- Every Imp moves onto this, the bump loop included; temp-dir clones and
  `codex exec` are gone. An Imp that needs the workspace venv in its
  worktree symlinks the main checkout's `.venv` itself, as the imps did
  into their temp dirs: Codex's daemon runs the agent's shell without
  direnv, and the tradeoff of a shared venv is the Imp's to accept.
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

## MCP and the workspace-thread skill

The checked-in `.codex/config.toml` registers the server for this trusted
workspace. Codex loads it automatically; no `codex mcp add` or user-level
configuration is needed. Its launcher resolves the main checkout through
Git, so the same configuration works in linked worktrees and at different
checkout paths. It uses the main checkout's virtualenv.

Entering the main checkout with direnv installs the MCP dependencies when
needed. Without direnv, install the workspace package manually:

```sh
.venv/bin/python -m pip install -e lib/python --config-settings editable_mode=compat
```

The project configuration sets `tool_timeout_sec = 600` so full sourceset
clones can finish before the client times out. Shell setup also works when
Codex is not installed; install Codex when you want to use the tool.

The server exposes exactly one tool, `create_workspace_thread`. Give either
`bead_id`, or `name` with a nonempty `prompt` and optional `sourceset`.
A bead provides the sourceset and opening assignment; `prompt` adds context.
`parent_thread_id` adds a return address and tells the worker to report
progress, blockers, and completion through Codex's native messaging tool.
The result contains `thread_id`, `turn_id`, `name`, and `worktree` after the
opening turn starts. The server is bound to the main workspace, so calls
from its child worktrees still create siblings in the same `.threads/`.

The MCP door uses ordinary Codex defaults, just like the CLI agent door,
not `IMP_DOOR_SETTINGS`. Launcher output goes to stderr, leaving stdout for
MCP. Failed worktrees remain for inspection; an opening-turn failure reports
the already-created thread ID and path. An occupied name is never replaced.

When a parent is supplied, the opening turn is a short handshake that replies
`WORKSPACE_THREAD_READY` without starting the assignment. The orchestrator
waits for that turn to finish, then uses native `send_message_to_thread` to
start the assignment. On this installation, the app attaches its native
thread tools when it starts a turn on an idle daemon-created thread; sending
the first native message while the initial turn is active did not attach
them. Without a parent, work starts immediately. No extra server or watcher
is needed for this handoff.

[The workspace-thread skill](../../../.agents/skills/workspace-thread/SKILL.md)
explains creation and coordination. Codex's built-in `read_thread`,
`send_message_to_thread`, and `wait_threads` accept the returned thread ID;
they were verified against a CLI-launched thread on this installation.
The orchestrator creates and tracks beads and closes them after reviewing
the worker's evidence. The launcher does not mutate issue state.

After registering, reload MCP configuration or restart/resume the calling
conversation if necessary. Verify tool visibility before using a new server.
Python edits do not hot-reload an already-running server; restart its MCP
process when upgrading it, rather than only reloading unchanged configuration.
Fresh worktrees use workspace HEAD and fresh repository clones: commit needed
workspace changes and publish needed source changes before launch.
