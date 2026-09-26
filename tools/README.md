# The imp runtime: impd and impctl

The runtime is the pair of binaries every imp workspace runs: `impd`, the
Daemon, and `impctl`, the Client. This README is what the pair commits to
at its boundary and how it is built inside. The Language and the contracts
with Imps and Sensors are the design record's,
[docs/imp-design.md](../docs/imp-design.md); nothing here redefines them.

## What the runtime commits to

### Where it lives

Everything runs from the workspace root, and the Daemon owns everything
under `.imp/` there:

| Path | What it is |
|---|---|
| `.imp/daemon.lock` | The single-instance lock; its contents are the running Daemon's pid. |
| `.imp/daemon.sock` | The control socket, the only listener. Directory mode 0700 is the same-user boundary. |
| `.imp/impd.out` | The Daemon's own stdout and stderr when `impctl up` spawned it. |
| `.imp/runs/<run-id>.log` | One Imp's stderr, created at launch, streamed until exit. |
| `.imp/watch.log` | Every Sensor's stderr, appended per Tick, each followed by a trailer `<argv>: exit N`. |

Imps and Sensors inherit the Daemon's cwd and environment. The
Daemon's environment is that of whoever started it, so the `PATH` in
effect at `impctl up` is the `PATH` every Run sees for the Daemon's life;
the workspace `.envrc` puts `build/bin` on it, which is how an Imp calls
`impctl` bare.

### The CLI

```
impctl up [--manifest PATH]...   start the Daemon if needed, wait for ready, apply each Manifest
impctl down                      SIGTERM the Daemon and wait for its lock to free
impctl status                    down | up | starting-or-wedged, then sigils, watches, runs, log tail
impctl apply PATH                inscribe and watch everything the Manifest declares; removes nothing
impctl sigils | inscribe NAME PATH | erase NAME
impctl watches | watch [--once] -- ARGV... | unwatch ID
impctl runs | launch SIGIL RUN_ID [ARGS...]
impctl tick
```

Every socket verb prints exactly the Daemon's documents, one JSON object
per line, with no presentation of its own; `erase` and `unwatch` print
nothing on success. A Daemon error goes to stderr and exits 1 with the
Daemon's own message (`POST /runs: 400 Bad Request: unknown sigil`), a 409
as `run id already occupied`, a 404 as `not found`. A usage error exits 2.
There is no single-Run verb: poll with `impctl runs`, or `GET /v1/runs/{id}`
over the socket with `curl --unix-socket`.

`launch` is the manual door for judgment-call relaunches under a fresh Run
Id. `watch --once` is how an Imp arms a follow-on wait mid-Run; the `--`
ends flag parsing so a Sensor argument beginning with `-` passes
through, and `argv[0]` must be an executable file or the verb refuses
before reaching the Daemon. `tick` exists for tests and for "I edited a Sensor,
fire it now".

### The lifecycle verbs

`up` leaves a Daemon accepting on the socket, then applies each
`--manifest` in order. Lock free: it spawns `impd`, waits up to ten
seconds for the socket, and prints `impd ready in 12ms (pid 41234)`. Lock
held: it prints `impd already running (pid 41234)` and waits the same ten
seconds. A spawned Daemon that exits first fails `up` with its reason and
points at `.imp/impd.out`; a held lock whose socket never accepts fails
`up` naming the pid. Rerunning `up` is free.

`down` SIGTERMs the pid in the lock file and waits up to ten seconds for
the lock to free, printing `impd stopped (pid 41234)`, or `impd not
running` when it was already free. Nothing is removed. Runs still
`running` are orphaned and keep running; `status` shows them first.

`status` prints one of `impd: down`, `impd: up (pid N)`, or
`impd: starting-or-wedged (pid N)`, the last meaning the lock is held but
the socket does not accept; a second `status` a moment later tells which.
When up, it follows with every Sigil, Watch, and Run and the last twenty
lines of `.imp/watch.log`.

`apply` prints one line per Manifest entry naming what the Daemon found:
`created echo -> scripts/imps/loops/echo/imp.py` (or `unchanged`,
`replaced`) and `watch 1 standing (created): scripts/imps/loops/echo/sensor.py`
(or `existing`). A Manifest with an unknown key, a non-slug name, an empty
path, an empty argv, or a Sigil path or Sensor that is not an executable
file is rejected with the file named, before anything reaches the Daemon.
`inscribe` and `watch` make the same check on the path they are given. The
rule is that `impctl` checks every executable it names and the Daemon
never stats a path: the check belongs where a human is looking when they
mistype a path or forget an exec bit, and left to the Daemon an Imp's
mistake is a failed Run while a Sensor's is a watch-log line every Tick
with nothing else saying so.

### The socket API

The Daemon serves HTTP over `.imp/daemon.sock`. HTTP input is the
provenance boundary: Run Ids, Sigil names, paths, and argvs are validated
here and trusted downstream.

| Call | Success | Failures | Commitments |
|---|---|---|---|
| `GET /v1/sigils` | 200, `[{"name","path"}]` name-sorted | | An empty list is `[]`, never null (true of every list). |
| `POST /v1/sigils` `{"name","path"}` | 201 `created`; 200 `unchanged` or `replaced` | 400 non-slug name or empty path | Body is `{"name","path","outcome","previous"}`; `previous` is the displaced path, null unless `replaced`. The Daemon does not check the path: a bad path is a failed Run. Runs in flight are never affected. |
| `DELETE /v1/sigils/{name}` | 204 | 404 | Runs launched under the name are untouched. |
| `GET /v1/watches` | 200, `[{"id","argv","once"}]` id-sorted | | Ids start at 1 and reset with the process. |
| `POST /v1/watches` `{"argv","once"}` | 201 new; 200 the existing Watch | 400 empty argv | Matched on exact argv plus `once`. `argv[0]` is not resolved: an unrunnable Sensor shows up in the watch log. |
| `DELETE /v1/watches/{id}` | 204 | 400 non-integer, 404 | A Watch removed mid-Tick finishes that pass and is absent from the next. |
| `GET /v1/runs` | 200, `[run]` in no particular order | | |
| `POST /v1/runs` Launch | 202 with the Run document and a `Location` header | 400 invalid Run Id or unknown Sigil; 409 Run Id occupied | 202 is returned even when the Run has already `failed` to start (bad path): the document says so. |
| `GET /v1/runs/{id}` | 200 | 404 | The poll target. |
| `POST /v1/tick` | 200, `{"watches","launched","duplicates","dropped"}` | | Synchronous: answers when the pass is done. |

The Run document is both the in-memory record and the wire format:

```json
{"sigil": "echo", "id": "echo-standing", "path": "scripts/imps/loops/echo/imp.py", "state": "succeeded", "error": null}
```

`state` moves `starting` → `running` → `succeeded` | `failed`; `error` is
null or the reason (`exit status 1`, or the exec error for a path that
could not be started). `path` is captured at launch and never rewritten.

### What a Tick does

A Tick runs every Watch that existed when the pass began, in id order,
one after another; `impctl tick` therefore blocks for the sum of the
Sensors' durations. Ticks run every five minutes (a compile-time default)
and on request; passes never overlap, and there is no pass at startup
because the Daemon starts empty, so the first is one interval in, or
`impctl tick` sooner.

Each Sensor's stdout is parsed after it exits. Every nonempty line goes
through the same launch path as `POST /v1/runs`: a launch counts in
`launched`, a 409 counts in `duplicates` and is never logged as a fault,
and an unparseable line or a rejected one (unknown Sigil, invalid Id) is
written to the watch log as `watch <id>: ...` and counted nowhere.

A one-shot Watch is dropped after the pass in which it first wrote any
nonempty line, whatever became of the line, so it can never fire twice.
`dropped` counts them.

### Startup, shutdown, and what a restart forgets

A Daemon starts empty and reads no file. A second `impd` in the same
workspace exits with `another impd already serves this workspace`.

On SIGTERM or SIGINT the Daemon closes the socket, removes the socket
file, and exits at once. Runs in flight are orphaned and keep streaming
to their logs; a Sensor mid-pass is abandoned and anything it
emits is lost; the lock file stays, its flock released by the exit.

A restart forgets every Sigil, Watch, and Run, including pending one-shot
Watches, and Watch ids restart at 1. The logs remain. Re-arming a lost
one-shot is a human step.

## How it is built

### Two processes, one lock

`impd` is a foreground process that never detaches, and `impctl` is its
supervisor in the manner of Bazel, adb, and watchman. Self-daemonizing
was rejected as a re-exec dance Go makes awkward for one caller; a
shutdown endpoint as redundant once `down` needs a pid anyway.

Liveness is the Daemon's `flock` on `.imp/daemon.lock`, probed nonblocking,
and nothing else. The kernel releases an flock on any process death,
SIGKILL included, so a probe that acquires the lock is proof the Daemon is
gone, where a pid or cmdline check could be fooled by a recycled pid. The
probe closes its fd the moment it acquires, or `impctl` would itself be the
Daemon nobody can start, and the Daemon retries its own flock briefly (ten
attempts, twenty milliseconds apart) so a probe's microsecond hold is never
mistaken for a rival.

The pid lives inside the lock file, written by the Daemon right after it
takes the lock, and exists only so `down` has something to SIGTERM, which
also makes a hand-started Daemon stoppable. One file with one writer means
no "lock held but no pid" state and nothing for `impctl` ever to remove.
Roads not taken: `impctl` owning a pidfile (a hand-started Daemon becomes
unstoppable) and a separate pidfile (two writers, a window between them).

Readiness is a completed dial of the socket, because a killed Daemon
leaves its socket file behind; with the lock held, a leftover socket file
can only be a corpse, so the Daemon removes it before listening.

### Inside the Daemon

The Daemon's state is two halves with a mutex each, mirroring the two
rows of the Language table: the daemon proper holds Sigils and Runs, and
`watches` holds the Watches and runs the Ticks. Each mutex is held only
for a map operation. The state is disjoint and the dependency runs one way
(`watches` calls the daemon's launch door; the daemon holds no reference
back), so no lock cycle is possible. Tick passes are serialized by
`watches`'s single goroutine, not by a lock: the interval and Client
requests are both events it selects on, so a requested pass waits its
turn. The pass snapshots the Watches under the Watch mutex, releases it,
runs the Sensors with it free (they call `gh` and take seconds; the
handlers must stay responsive), and re-takes it only to drop the one-shots
that fired. That snapshot lock guards against the handlers adding and
removing Watches during a pass, never against another pass.

Every Run goes through one launch door, whether the Launch came over
HTTP or out of a Sensor's stdout. The Sigil is read and the Run
Id claimed under one hold, so an unknown name never burns an Id; from the
claim onward the Run exists no matter what, and every failure path marks
it `failed` rather than leaving it `starting` with no process behind it.
A Daemon whose accept loop dies exits rather than lingering with the lock
held, since a wedged Daemon would block every future `up`.

### Inside the Client

The wire format is defined once, in `lib/go/wire`, and both binaries
import it. That package holds the document types (Sigil, Watch, Run,
Launch, and the inscribe and tick results) and the `.imp/` paths,
and nothing with behavior. Because `impd` and `impctl` compile against the
same declarations, a field added to a document exists on both ends of the
socket in the same build, and the CLI prints the Daemon's documents by
construction rather than by two struct definitions happening to agree.

`lib/go/client` is the transport on top of that: the socket dial, one
method per verb, and the mapping of Daemon statuses to Go errors. Two
statuses become sentinel errors rather than failures, 409 and 404, because
a caller re-firing a condition or removing something already gone treats
them as outcomes.

`impctl` is the client package's only importer. It is a package rather
than a file inside `impctl` so that a second Go program talking to the
Daemon would import it instead of copying it.

`up` finds `impd` beside its own executable, because the two are built
together into one bin directory, spawns it in its own session so a
terminal's SIGHUP never reaches it, and reaps it so a Daemon that dies
before accepting ends the wait with its reason. `apply` validates the
Manifest file as the provenance boundary for a human's edits, then issues
one inscribe per Sigil (name-sorted, for stable output) and one watch per
standing argv.

## Build and check

```sh
cmake -S . -B build -G Ninja
cmake --build build --parallel
ctest --test-dir build --output-on-failure   # the end-to-end chain check
```

The chain check (`test/e2e_chain_test.py`; its docstring is its record)
walks the whole lifecycle against the echo loop in a temp directory
standing in for a workspace root. The workspace `.envrc` runs the build on
every directory entry and puts `build/bin` on `PATH`.
