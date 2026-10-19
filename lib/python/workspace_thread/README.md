# workspace_thread: launching a workspace thread

A workspace thread is a Codex thread working in its own worktree of this
workspace, hosted on Codex's daemon so it appears in the agent command
center (`/agents`) as a live, named, openable row. workspace_thread is the
one program that makes a workspace thread, and every door onto a new one
goes through it: the `workspace-thread` executable for a human at a
terminal, the library for an Imp or an agent that starts a workspace thread
from inside its own thread. This README is the design record for the
component, settled in a grilling session on 2026-10-10 and implemented on
the same branch; the code is the files beside it, each with a header saying
what it owns. The terminology was settled on 2026-10-19, after checking how
Codex itself uses "thread"; the Language section records the result.

## Why

Starting work on something today means cloning the right repositories
into `sources/` by hand, starting `codex`, and telling it what to do, and
an Imp does the same in a temp directory with `codex exec`, invisible until
its Run log is read. The workspace wants one motion for both: name the
work, and get a thread in a fresh worktree with the right sources checked
out, visible alongside every other thread in the command center, and
reachable by other agents while it runs.

The requirements, from that grilling session:

- A workspace thread is launched by a bead id or by a plain name; a bead is
  optional.
- The workspace thread is named after the bead (or the name), and its
  worktree holds the sourceset the bead asks for.
- Every workspace thread looks and behaves the same in the command center,
  whether a human or an Imp started it: a live status, openable, steerable.
- Other agents can send a running workspace thread a message.
- Every door (terminal, skill, Imp) is the same program.
- An agent can start a sibling workspace thread and carry on without
  waiting for it.

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

**Thread** — Codex's word, used here only in Codex's sense: one
conversation on Codex's daemon, whatever started it. A human's chat, a
subagent, a fork and a thread in another directory are all threads.

**Workspace thread** — a thread whose cwd is a fresh worktree of the
workspace at `.workspace-threads/<slug>` with its sourceset loaded, named
after its bead or its given title. The thread itself is ordinary; the
qualifier names the setup that happens before it exists. A workspace thread
is written in full on its first mention in a paragraph; a later "the
thread" in that paragraph refers back to it. A bare "thread" anywhere else
is Codex's object.

Codex's own vocabulary, for reading its docs and tools
([glossary](https://developers.openai.com/codex/glossary)): *thread* is the
app-server object that holds turns and history; *chat* is the same thing in
the product docs; CLI help says *session*; a *subagent* is a delegated
agent and the *agent thread* is where it runs; a *Codex-managed worktree*
is a checkout the desktop app or `codex --worktree` cuts for one chat under
`$CODEX_HOME/worktrees`, which is not what a workspace thread uses (see
"Why the workspace thread lives on Codex's daemon").

**Bead** — an issue in the workspace's `br` store (`.beads/` in the main
checkout). A bead names its sourceset with the label `sourceset:<name>`.

**Codex's daemon** — Codex's local app-server daemon, which hosts threads
and serves the command center. Never "the Daemon": in this workspace that
word is `impd`.

**Door** — a way of asking for a new workspace thread. Three exist: the
human door (`workspace-thread new`, ending in an interactive `codex`), the
agent door (`workspace-thread new --prompt` or MCP, starting the first turn
and returning), and the Imp door (the library, blocking until the turn is
done).

## What workspace_thread commits to

The human door:

```
workspace-thread new <bead-id>                          # named after the bead; sourceset from its label
workspace-thread new --name <title> [--sourceset <s>]   # no bead; without a sourceset sources/ stays empty
workspace-thread new <bead-id-or-flags> --prompt TEXT   # agent door: start the first turn, print the Codex thread id, return
```

Without `--prompt`, either form ends with an interactive `codex` attached
to the new workspace thread, inside the new worktree, with the thread
already named; on a bead, the opening prompt ("Work on bead `<id>`...") is
typed for you as the first turn. With `--prompt`, the agent door, the
thread's first turn is started with that prompt (after the bead prompt,
when there is a bead) and the command returns at once, printing the Codex
thread id; the caller follows up with `codex queue --thread <id>` or by
opening the thread. The Imp door is the library: `prepare(name, sourceset,
settings)` runs the shared steps and returns the workspace thread and the
daemon connection, and
`run_turn(client, thread, prompt, output_schema)` sends the opening prompt,
streams the turn's items into the Run log, and returns a `TurnResult`
(status, error, last agent message) for the Imp to turn into an exit code.
`IMP_DOOR_SETTINGS` is the Imp door's approval and sandbox choice; an Imp
adds a `model` or `config` to it when its Manifest configures one.

What every door guarantees once it returns:

- A detached worktree of the workspace exists at `.workspace-threads/<slug>`,
  with `sources/` holding the sourceset's repositories and assets, or empty
  when no sourceset was named.
- A Codex thread named with the title exists on the daemon with the worktree
  as its cwd, so it is a row in the command center from its first turn and
  `codex resume` and `codex queue --thread` both reach it, by id or by
  exact name.
- With a bead, the thread's opening prompt says to work on that bead. That
  is the whole bead process for now; claiming, branching, handoff and close
  are deliberately unspecified until use shows what they should be.

A title is any text with at least one ASCII letter or digit. Its slug,
lowercase with runs of anything else collapsed to one hyphen and at most
128 characters, names the worktree, so two titles with the same slug
collide: the second is refused with the slug that collided and the `git
worktree remove` that frees it, because two workspace threads sharing a
checkout would share each other's half-done work. This is what an Imp hits
when a Run is relaunched for the same PR; removing the old worktree is part
of the relaunch. A bead id or a Run Id is already a slug and is its own; the
bead id is still checked strictly where it is read back from `br`, because
it goes verbatim into `br show`.

Failures before the Codex thread exists (no such sourceset, a bead without a
`sourceset:` label, a bead `br` does not know, a worktree in the way) exit
1 with one line on stderr. A worktree that was cut before a later step
failed is left in place for inspection, like a failed Run's log.

## Design

### The six steps

Every door runs the same steps; only the last differs.

1. **Name.** The bead id, or the given title. The Codex thread carries the
   title; the worktree carries its slug.
2. **Worktree.** `git worktree add --detach` of the workspace at
   `.workspace-threads/<slug>` inside the clone, gitignored like `.imp/`, so
   a workspace thread's worktree is spelled after its Codex thread and its
   bead, and everything about a workspace stays in the workspace. The
   workspace worktree takes no branch; only sourceset checkouts would, and
   that is part of the open bead process. `sources/` is gitignored, so it
   starts empty.
3. **Sources.** Load the sourceset into the worktree's `sources/`, taken
   from the bead's `sourceset:` label or the `--sourceset` flag; none
   leaves `sources/` empty, for workspace threads that work on the
   workspace itself (the overseer's checks). Full clones, every time; a
   clone cache is a later addition that does not change this shape.
4. **Bead.** Nothing to wire. `br` run inside a linked worktree that has no
   `.beads/` of its own resolves to the primary checkout's `.beads/`
   through the worktree's `.git` file (tested with br 0.7.4 from a worktree
   outside the clone, one inside it, and from a nested repo under its
   `sources/`).
5. **Thread.** Ensure Codex's daemon is up, connect to its socket, send
   `thread/start` with the worktree as cwd and the door's approval and
   sandbox settings, then `thread/name/set`, then a no-op
   `thread/section/move` so the daemon writes the thread's rollout file:
   it otherwise writes nothing until a turn lands, and `codex resume`
   reads from disk, so a thread with no turn yet fails to resume
   (codex.py, `persist_thread`, has what else was tried). The human door
   passes nothing and gets the user's own Codex defaults; the Imp door sets
   approvals to never and the sandbox off, as the imps run today, because
   a thread that paused for approval would hang with no one watching and
   the imp design record's constraints ask for no hardening. Both doors do
   this, which is what makes their workspace threads indistinguishable in
   the command center.
6. **Door.** Human: exec `codex resume <id>` in the worktree. Agent:
   send `turn/start` with the prompt, print the Codex thread id, return.
   Imp: send `turn/start` with the prompt, stream the thread's events into
   the Run log, return the turn's outcome. In every case the thread stays
   on the daemon afterwards as a Ready row.

### Why the workspace thread lives on Codex's daemon and not in `codex exec`

`codex exec` threads do appear in the command center, but only as
"Inactive" rows: status comes from threads the daemon itself has loaded,
exec runs its own in-process server, and opening a running exec thread
gives a frozen read-only snapshot because exec holds the writer lock. New
exec threads also go unnoticed by an already-open command center until it
reconnects. Hosting on the daemon is the only path that gives an Imp's
workspace thread a live "Working" row and lets `codex queue` reach it
mid-run.

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
app-server daemon start`, which is a no-op when it is already up.

Codex's managed worktrees (`codex --worktree`, `/worktree`) were rejected,
and re-checked against the 0.163.0 source on 2026-10-19: the worktree is
cut by the TUI or `codex exec` process itself, with no app-server method,
so neither an agent nor an Imp can ask the daemon for one; its path is a
random `$CODEX_HOME/worktrees/<bucket>/<repo>`, always detached at HEAD
with no branch and no base ref; the CLI copies no ignored files, so
`sources/` would start empty; and the desktop app's auto-cleanup collects
anything under its root that no pinned chat owns. A bead id cannot enter
through them, and nothing placed there is safe.

### Decisions in force

- One library, three doors. The library is the launcher; `workspace-thread`
  is a thin executable over it for the human and agent doors; an Imp
  imports it. The MCP server adds a structured creation tool for agents,
  using the same assignment resolution and launcher as the CLI. Codex's
  native tools own reading, messaging, and waiting; the workspace does not
  duplicate them.
- The Imp door is a Run through `impd`, so an Imp's workspace thread has a
  Run Id and a log as every Run does. Imps have no beads to start with, so
  an Imp's workspace thread is named after its Run Id and the Imp names the
  sourceset itself. How a Run Id and a bead id relate is decided when an
  Imp first has a bead, not before.
- Every Imp moves onto this, the bump loop included; temp-dir clones and
  `codex exec` are gone. An Imp that needs the workspace venv in its
  worktree symlinks the main checkout's `.venv` itself, as the imps did
  into their temp dirs: Codex's daemon runs the agent's shell without
  direnv, and the tradeoff of a shared venv is the Imp's to accept.
- A bead is optional and the workspace thread's name comes from it when
  present.
- `impctl` is not the launcher. The imp design record asks it to be the
  dumb Daemon client; git worktrees, sourcesets and Codex's daemon are all
  outside that boundary, and the launcher is a Client of `impd` when it
  launches a Run.
- The bead process beyond the opening prompt is unspecified on purpose, to
  be discovered by use.
- Sourceset loading moves into workspace_thread as a function, and
  `scripts/ws_load.py` becomes a thin command over it for filling the main
  checkout's `sources/` by hand, which stays a real use: work is loaded
  there outside any workspace thread for review and for human-in-the-loop
  work. The refuse-if-unsaved check stays on that by-hand path only, the
  one place a load can destroy work; the workspace thread path always
  targets an empty `sources/` and carries no guard that cannot fire.

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
The result contains `thread_id`, `turn_id`, `name` (the typed title), and
`worktree` (ending in the title's slug) after the opening turn starts. The
server is bound to the main workspace, so calls from its child worktrees
still create siblings in the same `.workspace-threads/`.

The MCP door uses ordinary Codex defaults, just like the CLI agent door,
not `IMP_DOOR_SETTINGS`. Launcher output goes to stderr, leaving stdout for
MCP. Failed worktrees remain for inspection; an opening-turn failure
reports the already-created Codex thread ID and worktree path. An occupied
slug is never replaced.

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
they were verified against a CLI-launched workspace thread on this
installation. The orchestrator creates and tracks beads and closes them
after reviewing the worker's evidence. The launcher does not mutate issue
state.

After registering, reload MCP configuration or restart/resume the calling
conversation if necessary. Verify tool visibility before using a new server.
Python edits do not hot-reload an already-running server; restart its MCP
process when upgrading it, rather than only reloading unchanged configuration.
Fresh worktrees use workspace HEAD and fresh repository clones: commit needed
workspace changes and publish needed source changes before launch.

## Tests

`test/workspace_thread_e2e_test.py` is the three doors against the real
daemon, a real `br` bead and real turns, which are model calls billed to
the account. It is skipped unless `WORKSPACE_THREAD_E2E=1` is set and is a
final verification, not something to run on every edit; there is no mocked
suite, because the launcher is glue between external systems and a test
that replaces them checks little. Run it with:

```sh
WORKSPACE_THREAD_E2E=1 .venv/bin/python -m unittest test.workspace_thread_e2e_test
```
