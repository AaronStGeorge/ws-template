# workspace_thread: launching a workspace thread

A workspace thread is a Codex thread working in its own worktree of this
workspace, hosted on Codex's daemon so it appears in the agent command
center (`/agents`) as a live, named, openable row. workspace_thread is the
one program that makes a workspace thread, and every entry onto a new one
goes through it: the `workspace-thread` executable for a human at a
terminal, the `create_workspace_thread` MCP tool for an agent, the library
for an Imp.

## Why

Starting work on something today means cloning the right repositories
into `sources/` by hand, starting `codex`, and telling it what to do, and
an Imp does the same in a temp directory with `codex exec`, invisible until
its Run log is read. The workspace wants one motion for both: name the
work, and get a thread in a fresh worktree with the right sources checked
out, visible alongside every other thread in the command center, and
reachable by other agents while it runs.

The requirements:

- A workspace thread is launched by a bead id or by a title; a bead is
  optional.
- The workspace thread carries the work's name in the command center, and
  its worktree holds the sources that work needs: the bead's sourceset, the
  one named alongside a title, or none.
- Every workspace thread, whoever started it, is a live, openable,
  steerable row in the command center.
- Other agents can send a running workspace thread a message.
- An agent can start a sibling workspace thread and carry on without
  waiting for it.
- An Imp's workspace thread runs unattended to an outcome the Imp turns
  into an exit code, with its transcript in the Run log.
- The command a human types says whether they stay attached to the thread
  or it runs without them.

## Language

This component inherits the imp design record's Language
([docs/imp-design.md](../../../docs/imp-design.md)) and adds:

**Workspace** — a clone of this repository: skills, the imp runtime,
`sources/`. The imp design record already uses the word this way.

**Sourceset** — the sources a piece of work needs, by name: a checked-in
manifest, `<name>/sourceset.json` in the workspace's `sourcesets/` or in
the directory an entry was given, listing repositories to clone into
`sources/`, with assets linked beside them. The sourceset package reads
and loads one, and its record
([lib/python/sourceset/README.md](../sourceset/README.md)) holds the rest
of that vocabulary, the by-hand door for the main checkout's `sources/`
included.

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

**Entry** — a way of asking for a new workspace thread: the three named at
the top, one per kind of caller, each the one program. An agent without
the MCP tool, or a script, reaches the executable through a shell; that is
a fallback, not a fourth entry. An entry takes an assignment and ends one
of the ways below.

**Assignment** — what the workspace thread is for: a bead id, or a title
with an optional sourceset, plus an optional prompt. A bead supplies the
title, the sourceset and the opening prompt, and a prompt given alongside
it is appended. assignment.py resolves one.

**Ending** — what the caller does once the thread exists. Three exist:

- **attach** — become an interactive `codex` on the thread.
- **detach** — start its first turn and return at once.
- **wait** — start its first turn and follow it to its end.

Each entry offers the endings its caller has a use for: the executable
attach and detach, because a human either sits in the thread or starts it
and comes back later; the MCP tool detach, because an agent carries on and
Codex's native tools do its waiting; the library all three, because the
executable is built on it and an Imp waits. The prompt belongs to the
assignment, not the ending: it says what the thread is for, not whether
the caller stays. Attach and wait both keep the caller in the foreground;
the names say which one drives the thread. The ending's name is not git's:
a worktree's detached HEAD is spelled out as `git worktree add --detach`
in the Design and nowhere else.

**Unattended** — the settings a thread runs under when no one will answer
an approval prompt: approvals never and the sandbox off,
`UNATTENDED_SETTINGS` in launch.py. Which entries pass them is a decision
in force (settings follow attendance).

## What workspace_thread commits to

The executable, the human's entry:

```
workspace-thread new <bead-id> [--prompt TEXT]                         # named after the bead; sourceset from its label
workspace-thread new --name <title> [--sourceset <s>] [--prompt TEXT]  # no bead; without a sourceset sources/ stays empty
workspace-thread new ... --detach                                      # start the first turn, print the Codex thread id, return
workspace-thread new ... --sourcesets-dir DIR                          # read the sourceset from DIR, not the workspace's sourcesets/
```

Either form names an assignment; `--detach` picks the ending;
`--sourcesets-dir DIR` reads the sourceset from `DIR` rather than the
workspace's `sourcesets/`. Without `--detach` the command attaches: it
ends as an interactive `codex` on the new workspace thread, inside the new
worktree, with the thread already named.
The opening prompt is typed for you as the first turn when there is one:
the bead's ("Work on bead `<id>`...") with `--prompt` appended after a
blank line, or `--prompt` alone; with neither, you type the first turn
yourself. An empty `--prompt` counts as none. With `--detach` the thread's
first turn is started with that opening prompt and the command returns at
once; stdout carries the Codex thread id and nothing else, and the
launcher's progress goes to stderr, so a shell can capture the id. The
caller follows up with `codex queue --thread <id>` or by opening the
thread. A detach must have something to send, so a named workspace thread
needs `--prompt` and a bead's already has one. If the opening turn fails
after the thread exists, the command exits 1 with one stderr line naming
the thread id and worktree, as the MCP tool does.

The library is the launcher and has every ending. `prepare(name,
sourceset, settings, sourcesets_dir)` runs the shared steps, reading the
sourceset from `sourcesets_dir` when one is given, and returns the
workspace thread and the daemon connection; `attach(thread, prompt)`
replaces the process with `codex resume`; `start_turn(client, thread,
prompt, output_schema)` sends the opening prompt and returns the turn id;
and `run_turn(client, thread, prompt, output_schema)` sends it, streams
the turn's items into the Run log, and returns a `TurnResult` (status,
error, last agent message) for an Imp to turn into an exit code.
`opening_prompt(prompt)` is the one check the turn-starting endings share:
the prompt, or a `ValueError` when there is none to send. An Imp calls
`prepare` with `UNATTENDED_SETTINGS`, adding a `model` or `config` when its
Manifest configures one, and ends with `run_turn`.

What the launcher guarantees by the time any ending begins:

- A worktree of the workspace, on no branch, exists at `.workspace-threads/<slug>`,
  with `sources/` holding the sourceset's repositories and assets, or empty
  when no sourceset was named.
- A Codex thread named with the title exists on the daemon with the worktree
  as its cwd, so it is a row in the command center from its first turn and
  `codex resume` and `codex queue --thread` both reach it, by id or by
  exact name.
- With a bead, the thread's opening prompt says to work on that bead, and
  that is the whole bead process for now (Decisions in force).

A title is any text with at least one ASCII letter or digit. Its slug,
lowercase with runs of anything else collapsed to one hyphen, leading and
trailing hyphens dropped, and at most 128 characters, names the worktree,
so two titles with the same slug collide: the second is refused with the
slug that collided and the `git worktree remove` that frees it, because
two workspace threads sharing a checkout would share each other's
half-done work. This is what an Imp hits when a Run is relaunched for the
same PR; removing the old worktree is part of the relaunch. A bead id or a
Run Id is already a slug and is its own; the bead id is still checked
strictly where it is read back from `br`, because it goes verbatim into
`br show`.

Failures before the Codex thread exists (no such sourceset, a bead without a
`sourceset:` label, a bead `br` does not know, a worktree in the way, a
detach with nothing to send) exit 1 with one line on stderr. The detach
refusal is checked before anything is cut, so a refused detach leaves no
worktree behind. A worktree that was cut before a later step failed, and a
thread whose opening turn failed, are left in place for inspection, like a
failed Run's log.

## Design

### The six steps

Every entry runs the same steps; only the ending differs.

1. **Name.** The bead id, or the given title. The Codex thread carries the
   title; the worktree carries its slug. For a detach, the opening prompt
   is checked here too.
2. **Worktree.** `git worktree add --detach` of the workspace at
   `.workspace-threads/<slug>` inside the clone, gitignored like `.imp/`, so
   a workspace thread's worktree is spelled after its Codex thread and its
   bead, and everything about a workspace stays in the workspace. The
   workspace worktree takes no branch; only sourceset checkouts would, and
   that is part of the open bead process. `sources/` is gitignored, so it
   starts empty. `models/` is a relative symlink to the main checkout's
   `models/`, sharing downloaded models across threads. The link is created
   even if the main checkout has no models yet; it becomes usable when that
   directory is created.
3. **Sources.** Load the sourceset into the worktree's `sources/` through
   the sourceset package, named by the bead's `sourceset:` label or the
   `--sourceset` flag and read from the workspace's `sourcesets/` unless
   the entry was given another directory; none leaves `sources/` empty,
   for workspace threads that work on the workspace itself.
4. **Bead.** Nothing to wire. `br` run inside a linked worktree that has no
   `.beads/` of its own resolves to the primary checkout's `.beads/`
   through the worktree's `.git` file (tested with br 0.7.4 from a worktree
   outside the clone, one inside it, and from a nested repo under its
   `sources/`).
5. **Thread.** Ensure Codex's daemon is up, connect to its socket, send
   `thread/start` with the worktree as cwd and the caller's approval and
   sandbox settings (Decisions in force: settings follow attendance), then
   `thread/name/set`, then a no-op `thread/section/move` so the daemon
   writes the thread's rollout file: it otherwise writes nothing until a
   turn lands, and `codex resume` reads from disk, so a thread with no
   turn yet fails to resume (codex.py, `persist_thread`, has what else was
   tried). This step being the same for every entry is what makes their
   workspace threads the same rows in the command center.
6. **Ending.** Attach: exec `codex resume <id>` in the worktree, with the
   opening prompt when there is one. Detach: send `turn/start` with the
   prompt and return; the library hands back the turn id, the executable
   prints the thread id. Wait: send `turn/start` with the prompt, stream
   the thread's events into the Run log, return the turn's outcome. In
   every case the thread stays on the daemon afterwards as a Ready row.

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

- One library, three entries, three endings. The library owns every
  ending; the executable and the MCP tool are thin layers over it that
  share its assignment resolution, and an Imp imports it. Which endings
  each entry offers is in Language. Codex's native tools own reading,
  messaging, and waiting on a thread that already exists; the workspace
  does not duplicate them.
- The ending is the caller's to say, never inferred from the assignment;
  `--detach` says it. Until 2026-10-19 the executable read `--prompt` as
  the request to detach, which left a human unable to open a named thread
  with a first turn typed for them, and made detaching on a bead with
  nothing to add a matter of passing an empty prompt.
- The prompt check is the ending's, not the assignment's. `opening_prompt`
  lives in launch.py beside `start_turn`; an assignment may lack a prompt,
  and only an ending that starts a turn refuses that.
- Settings follow attendance, not the entry. `UNATTENDED_SETTINGS` is for
  a thread no one will answer, which is every Imp's: a thread that paused
  for approval in a Run would hang, and the imp design record's
  constraints ask for no hardening, so the imps keep the settings they ran
  under with `codex exec`. The executable and the MCP tool pass the user's
  defaults in every ending, detach included, so a detached thread can
  pause for an approval that only a human watching the command center can
  give. That is right when a human detached it and open when an agent
  did; whether the MCP tool should launch unattended is to be settled the
  first time one of its threads hangs.
- The executable has no wait ending. An Imp needs the output schema, the
  streamed log and the `TurnResult`, which is the library's shape, and
  nothing else has asked to block on a turn from a shell.
- An Imp's workspace thread is a Run through `impd`, so it has a Run Id
  and a log as every Run does. Imps have no beads to start with, so an
  Imp's workspace thread is named after its Run Id and the Imp names the
  sourceset itself. How a Run Id and a bead id relate is decided when an
  Imp first has a bead, not before.
- Every Imp moves onto this, the bump loop included; temp-dir clones and
  `codex exec` are gone. An Imp that needs the workspace venv in its
  worktree symlinks the main checkout's `.venv` itself, as the imps did
  into their temp dirs: Codex's daemon runs the agent's shell without
  direnv, and the tradeoff of a shared venv is the Imp's to accept.
- `impctl` is not the launcher. The imp design record asks it to be the
  dumb Daemon client; git worktrees, sourcesets and Codex's daemon are all
  outside that boundary, and the launcher is a Client of `impd` when it
  launches a Run.
- The bead process beyond the opening prompt (claiming, branching, handoff
  and close) is unspecified on purpose, to be discovered by use.
- Sourceset loading is the sourceset package's; the launcher hands it a
  name. It lived here until 2026-10-20, when filling the main checkout's
  `sources/` by hand meant importing a thread library. That by-hand door
  and the guard that keeps it from destroying work are recorded with the
  package.
- Where sourcesets are read from is the entry's configuration, not the
  assignment's: `--sourcesets-dir` on the executable and on the MCP server
  command, `prepare`'s `sourcesets_dir` for an Imp, and the workspace's
  `sourcesets/` when none is given. The tool's parameters do not carry it:
  an agent names a sourceset, and where the workspace keeps them is the
  workspace's to configure.

## MCP and the workspace-thread skill

The checked-in `.codex/config.toml` registers the server for this trusted
workspace. Codex loads it automatically; no `codex mcp add` or user-level
configuration is needed. The configured command resolves the main checkout
through Git and runs the server from its virtualenv, bound to that
checkout, so the same configuration works in linked worktrees and at
different checkout paths, and a call from a child worktree creates a
sibling in the same `.workspace-threads/`. `--sourcesets-dir DIR` on that
command reads sourcesets from `DIR` instead of the main checkout's
`sourcesets/`.

Entering the main checkout with direnv installs the MCP dependencies when
needed. Without direnv, install the workspace package manually:

```sh
.venv/bin/python -m pip install -e lib/python --config-settings editable_mode=compat
```

The project configuration sets `tool_timeout_sec = 600` so full sourceset
clones can finish before the client times out. Shell setup also works when
Codex is not installed; install Codex when you want to use the tool.

The server exposes exactly one tool, `create_workspace_thread`. Give either
`bead_id`, or `name` with a nonempty `prompt` and optional `sourceset`,
resolved as the executable's arguments are.
`parent_thread_id` adds a return address and tells the worker to report
progress, blockers, and completion through Codex's native messaging tool.
The result contains `thread_id`, `turn_id`, `name` (the typed title), and
`worktree` (ending in the title's slug) after the opening turn starts.

The tool's ending is detach under the user's Codex defaults, as
`workspace-thread new --detach` is; its failures are those in What
workspace_thread commits to, surfaced as the tool's error.

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

`test/workspace_thread_e2e_test.py` is the three endings against the real
daemon, a real `br` bead and real turns, which are model calls billed to
the account: detach runs the `workspace-thread` executable as a subprocess
and reads its stdout as a shell would; attach calls the command's entry
point in-process, because the only way to see the `codex resume` argv is
to intercept the final exec, and so checks what is handed to Codex rather
than what Codex types; wait goes through the library as an Imp does. It is
skipped unless `WORKSPACE_THREAD_E2E=1` is set and is a final
verification, not something to run on every edit; there is no mocked
suite, because the launcher is glue between external systems and a test
that replaces them checks little. Run it with:

```sh
WORKSPACE_THREAD_E2E=1 .venv/bin/python -m unittest test.workspace_thread_e2e_test
```
