# imp design record

This is the record of imp as one component: why it exists, what it must
do, the Language every other imp document inherits, the contracts between
its parts, and the decisions in force. Its two children have their own
records, the runtime in [tools/README.md](../tools/README.md) and the
loops in [scripts/imps/README.md](../scripts/imps/README.md); both assume
this one's Language.

## Why

There's an odd mix of motivations for this project.
I find often I want to spin off a task that I know an agent will do at least OK at and have it cook in the background somewhere.
Today, definitely, there's a lot of stuff that you can just say "do it" and you get back a reasonable result while you're freed up to go do something else -- that's nice 😊.
There's also this whole notion of loop engineering; you have a string of agents pass a particular piece of work through an agent "state machine" and get a better result (or at least I think that's what it is).
Good enough loops count as a "lights out" software factories: you stand at the front door and do a rain dance, and at the back door commercially viable software products are gracefully excreted.
And then there's me, I'm not really sure what "agent loops" are, but I'm desperately scared that after they fire everyone in the software factory I won't get a job as one of the rain dancers.
So this project: my attempt to learn how to be a loop engineer while experiencing emotions swinging wildly between a pleasant sense of pragmatism and gut-wrenching stabs of fear.

imp is a deliberately small per-workspace runtime for delegating work to
code-defined agent Imps. It exists to let useful agent work carry on after
I have stopped watching, and to make loop engineering concrete: a loop
here is a few small programs and a Manifest, not a platform.

## Requirements

imp's requirements are one concrete story. A feature is added when the
story needs it, never ahead of it.

### The story: the bump loop

An integration staging repo's automation opens submodule bump PRs around
the clock. When a dependency moves incompatibly, a bump PR's CI goes red
and stays red until someone repairs the staging scripts and, often, an
affected submodule.

1. A bump PR goes red. I do nothing. Soon after, a fix run exists, visible
   alongside everything else in flight.
2. The run repairs the breakage and drives the bump PR green without
   waiting on any human. Where the repair required submodule changes, the
   run validates them on the bump PR immediately *and* submits them to the
   submodule's upstream for review, described well enough, including
   which upstream change necessitated them, to be judged without
   re-deriving the diagnosis.
3. The fix run ends. One piece of pending work is now visible: waiting on
   the upstream review. That wait may last days and survives me closing
   every terminal. It does not survive the Daemon restarting; re-arming it
   is a human step, accepted while simplicity is worth more than
   robustness, and not a permanent exclusion.
4. Upstream merges on its own schedule. Without me doing anything,
   follow-up work updates the bump PR to consume the merged upstream and
   drives it green again, leaving no dependency on my personal validation
   fork.
5. My remaining jobs are judgment calls only: review the upstream
   submission, merge the green bump PR, and step in when a run reports
   itself stuck, with enough context in the run's log to pick up where it
   stopped.

Every promise in the story binds imp: work that starts without me, stays
visible while I'm gone, waits out external events for days, continues past
its own run's end with no one bridging the stages, and leaves a log worth
acting on when it stops.

### Constraints the story runs under

- One trusted user on their own Linux machine, with no appetite for
  accounts, tokens, or hardening: each workspace's control plane is a unix
  socket only its user can reach; the work itself connects outward freely.
- The loop must be code I can read, change, and rerun, never a black box.
- Bringing a workspace up is a command whose whole job is start, wait for
  ready, and declare the starting state. Nothing in the start-up path
  selects, merges, reconciles, or orders anything.
- One workflow builds the tools and runs the automated check.
- Failure behavior, recovery, cancellation, cleanup, scheduling, retries,
  and resource limits may remain undefined without becoming permanent
  exclusions. Races are not in that deferral: racy code is a correctness
  bug and gets fixed.

Non-goals: authentication and authorization; remote or multi-user
operation; any built-in ticket concept, since external work-tracking is
bridged by Clients, never modeled.

## Language

**Daemon** — the workspace's one long-lived process, `impd`. It holds
every Sigil, Watch, and Run in memory, runs Ticks, and launches Imps.

**Imp** — an executable the Daemon launches under the Imp process
contract below. Its interior is its own business.

**Sigil** — a name bound to an executable path, under which the Daemon
launches an Imp. Verbs: *inscribe*, *erase*. A name is a slug (see Run Id).

**Run** — one Daemon-owned execution of one Imp under one Run Id, with a
state (`starting`, `running`, `succeeded`, `failed`) and a log, outliving
every Client connection and every Sigil change.

**Run Id** — the Client-chosen slug that identifies a Run: lowercase ASCII
letters and digits, hyphen-joined (`^[a-z0-9]+(-[a-z0-9]+)*$`), at most 64
characters. A Run Id has at most one Run within one Daemon's life.

**Launch** — the JSON that asks for a Run: `sigil`, `id`, `args`. What a
Client POSTs to `/v1/runs` and what a Sensor emits, one per line.

**Sensor** — an executable whose whole contract is its stdout: zero or
more Launches, one JSON document per line. Emitting nothing means "not
yet". A Watch names one.

**Watch** — a Sensor argv the Daemon runs every Tick, carrying a
Daemon-assigned integer id. A *standing* Watch is never dropped by the
Daemon. A *one-shot* Watch is dropped after the first Tick in which it
emitted anything. Verbs: *watch*, *unwatch*.

**Tick** — one pass over every Watch: run each Sensor and launch
what it emits. Ticks happen on the Daemon's fixed interval and on request.

**Manifest** — a checked-in JSON file declaring one Loop's Sigils and
standing Watches. A Client applies it, additively. The Daemon never reads
one.

**Loop** — a directory under `scripts/imps/loops/` holding the Imps,
Sensors, and Manifest that together form one automation.

**Client** — any transient participant talking to the Daemon over its
socket: `impctl`, an Imp arming a Watch mid-Run, a human with curl.

The terms form two symmetric rows. A Watch is to a Sensor what a Sigil is
to an Imp: a binding the Daemon holds, naming an executable it runs. A
Tick's output is the Launches the Daemon then turns into Runs.

| Binding the Daemon holds | Executable it names | Act | Result |
|---|---|---|---|
| Sigil | Imp | launch | Run |
| Watch | Sensor | tick | Launch |

## Design

### Three components and one discovery rule

imp is a Daemon, a Client, and the Loops they run, and the current working
directory is how they find each other. Everything runs from the workspace
root, the directory holding the `.imp/` the Daemon writes; there is no
global home, no config, no registry. An Imp inherits that cwd, which is
why `impctl` works bare from inside a Run.

The Daemon is asked to be dumb: hold Sigils, Watches, and Runs, exec what
it is told, stream stderr into logs, turn exit codes into Run states, and
interpret nothing else. The Client is asked to be the only door and the
Daemon's supervisor: start and stop its process, turn a Manifest into the
calls it understands, print its documents unchanged. A Loop is asked to
supply the moving parts, Imps that do work, Sensors that decide
when, and a Manifest naming the standing ones, kept in the workspace that
owns them, paired (a Sensor exists to launch its Imp), grouped by
automation.

### The contracts between them

**The Imp process contract.** An Imp is exec'd directly (no shell) at its
Sigil's path with the Launch's `args` as argv, cwd the workspace root,
stdin `/dev/null`, environment the Daemon's own. The path must name an
executable file; a binary is as good as anything else, since the Daemon
runs no interpreter. Its stderr streams into
the Run's log; its stdout is discarded; its exit code is the outcome, zero
`succeeded` and anything else `failed`, meaning only "this Run needs
attention". There is no message channel and no structured result: richer
artifacts live in the domain (PR bodies, logs), where the human already
looks.

**The Sensor contract.** A Sensor is exec'd directly
with its Watch's argv, same cwd and environment as an Imp, and must be an
executable file for the same reason. Its stdout is read after it exits: every nonempty line must be a Launch. Its stderr
and exit code land in the watch log and carry no meaning to the Daemon; a
Sensor that emits a line and then crashes has still fired.

**Launch and Run Id.** A Launch names a Sigil, a Run Id, and an
argument list:

```json
{"sigil": "fix-llama-bump", "id": "fix-bump-pr-46", "args": ["https://github.com/ROCm/ggml-staging-automation/pull/46"]}
```

The Run Id is chosen by the emitter and must be deterministic in the
condition that produced it. That is what makes the system idempotent: a
Sensor re-emits the same Launch every Tick for as long as
its condition holds, and every emission after the first is a rejected
duplicate, never a second Run and, deliberately, never a retry. A failed
Run occupies its Id; relaunching is a human judgment call under a fresh
Id, through `impctl launch`.

**The Manifest.** A Manifest has two keys and nothing else:

```json
{
  "sigils": {"<name>": "<path from the workspace root>"},
  "watches": [["<sensor path>", "<arg>", "..."]]
}
```

Only standing Watches belong in a Manifest. One-shot Watches are armed by
Imps mid-Run with arguments that exist only then, so they have no place in
a checked-in file. Paths and argv are sent to the Daemon verbatim and
resolve from the workspace root.

**The durable record.** Run state lives in the Daemon's memory; the files
under `.imp/`, laid out in [tools/README.md](../tools/README.md), are the
record that outlives it, and a Loop that needs to look back (the bump
loop's overseer does) reads them.

### Decisions in force

- One Daemon per workspace, and only one process. The Watches and their
  Ticks live inside the Daemon rather than in a separate `impwatch`:
  that process existed so Watches could live in a file and outlive any
  process, and once Watches were decided to be in-memory a second process
  bought two sockets, two locks, and a start-order constraint for a
  boundary a source file preserves just as well. (That replaced a
  registration-era design with a user-global `~/.ws2`, recorded in the
  archived repo.)
- The Daemon starts empty and reads no configuration file. The Manifest is
  a batch of imperative calls issued by `impctl apply`, so the Daemon stays
  ignorant of files and `apply` stays a loop over two Client calls.
- `apply` is additive: it ensures each declared Sigil and Watch exists and
  removes nothing, so a Sigil or Watch added by hand for an experiment
  survives the next `up`. Declarative pruning is a flag to add if wanted,
  never the default.
- All Daemon state is in memory; the logs are the durable record. No
  database and no rows file, because neither has a customer until
  something must persist, and Run persistence is a later decision with
  its own questions (what is a `running` Run after the process that ran it
  is gone?). A restart forgets every Run Id, so a Sensor that
  re-emits a past Id after a restart re-runs it; accepted.
- Run observation is poll-only; the Daemon never pushes.
- Re-applying a Manifest is free because inscribe and watch are
  idempotent: the same name at the same path is a no-op (a different path
  replaces, and the response says so), and the same argv with the same
  one-shot flag returns the existing Watch.
- A Run is never affected by a Sigil change. The Sigil is consulted only
  at launch; the Run's record carries the path it actually ran.
- A one-shot Watch's emission and the Daemon's answer are one in-process
  event, so the lost-launch case that once forced a start order between
  the Watches and the Daemon no longer exists.
- Ticks happen on a fixed global interval, a compile-time default, plus on
  request, with no startup pass since the Daemon starts empty. Per-Watch
  schedules are not a thing: time-slot logic belongs in the Sensor,
  as the bump loop's overseer shows.
- A Watch is matched on exact argv plus the one-shot flag, so editing a
  standing Watch's argv in a Manifest and re-running `up` adds a second
  Watch beside the old one, removed by hand. Reconciling by executable path
  was cut until manual removal proves to be a chore.
- Sigil names follow the Run Id slug pattern, because a name is a URL path
  segment and a looser name could be inscribed but never erased.
- `impd` never detaches and `impctl` is its supervisor, the
  client-managed-daemon idiom; the protocol between them is interior to
  the runtime and recorded there.
- "Which loops does this workspace run" is answered by which Manifests
  `up` applies, not by a config layer. The echo loop is the reference
  example and the one the chain check drives.
