# imp tools

A deliberately small runtime for delegating work to code-defined agent
Imps, one instance per workspace: each workspace runs its own `impd`
with whatever automations are useful to the work that happens there. The
full design record (the story these tools serve, the shared Language, the
decisions in force) is [docs/imp-design.md](../docs/imp-design.md); this
README is the architecture of the three tools and how they work together.

## The three tools

- **`impd`** — the Daemon. Started from the workspace root with an explicit
  config (`impd --config impd.json`; the file maps Imp names to
  executable paths, is gitignored, and `impd.json.example` shows the
  shape). It owns everything under the workspace's `.imp/`: its unix socket
  (`daemon.sock`, the only listener — no network port; directory perms are
  the same-user boundary), a single-instance lock, and per-Run logs
  (`runs/<run-id>.log`). Per Run it does exactly three things: exec the
  configured Imp with the Launch Body's arguments, stream stderr into
  the Run's log, and turn process exit into the terminal state
  (`succeeded`/`failed`). It interprets nothing. Run state is in-memory
  only; log files are the durable record.
- **`impctl`** — the door: a Go client library (`lib/go/client`) plus a
  thin CLI over it (`imps` | `runs` | `launch PIPELINE RUN_ID
  [ARGS...]`). `launch` is the manual path for judgment-call relaunches.
- **`impwatch`** — the sensor: durable Watch Rows (a Condition Script argv
  plus a clear-after-fire flag) in `.imp/watches.jsonl`, armed over the
  command line (`arm [--clear] -- ARGV...`), evaluated by a cron-driven
  one-shot `tick` that runs each row's script and POSTs every Launch Body
  it emits to the Daemon verbatim. Script stderr and exit codes land in
  `.imp/watch.log`; firing is defined by emission alone. Rows-file access
  serializes on a blocking flock (held across the whole Tick), and a
  Condition Script must never invoke `arm` — it would deadlock the Tick
  holding that lock.

## How they compose

Everything runs from the workspace root — cwd is the entire discovery
mechanism. A Imp is any executable honoring the process contract:
argv in, human-readable diagnostics on stderr, outcome in the exit code
(stdout is ignored; the working directory is the workspace root). A
Condition Script's contract is stdout: zero or more Launch Bodies
(`{"imp", "id", "args"}`), one JSON per line; emitting nothing means
"not yet". Client-chosen deterministic Run Ids make everything idempotent:
"a Run Id has at most one Run", so a re-fired condition is a rejected
duplicate (409), never duplicated work — and deliberately never a retry.

A loop is composed from these parts alone: a standing (non-clearing) row
discovers work and launches a Imp; the Imp does the work and may
arm a clearing row pairing a condition with a follow-on launch; days later
a tick fires it with no process having waited in between. The first real
loop lives in this workspace under `scripts/ggml-staging-automation/`
(see its README).

## Build and check

```sh
cmake -S . -B build -G Ninja
cmake --build build --parallel
ctest --test-dir build --output-on-failure   # the end-to-end chain check
```

## Running a workspace's daemon

```sh
cp impd.json.example impd.json   # then edit; impd.json is gitignored
./build/bin/impd --config impd.json
./build/bin/impwatch arm -- <workspace>/scripts/.../some_sensor.py
./build/bin/impwatch tick        # wire into cron for a live loop
./build/bin/impctl runs
tail -f .imp/runs/<run-id>.log
```
