# imp design

This document carries the why, the requirements, and the shared language;
how the tools themselves work and fit together lives in
[tools/README.md](../tools/README.md).

## Why

There's an odd mix of motivations for this project.
I find often I want to spin off a task that I know an agent will do at least OK at and have it cook in the background somewhere.
Today, definitely, there's a lot of stuff that you can just say "do it" and you get back a reasonable result while you're freed up to go do something else -- that's nice 😊.
There's also this whole notion of loop engineering; you have a string of agents pass a particular piece of work through an agent "state machine" and get a better result (or at least I think that's what it is).
Good enough loops count as a "lights out" software factories: you stand at the front door and do a rain dance, and at the back door commercially viable software products are gracefully excreted.
And then there's me, I'm not really sure what "agent loops" are, but I'm desperately scared that after they fire everyone in the software factory I won't get a job as one of the rain dancers.
So this project: my attempt to learn how to be a loop engineer while experiencing emotions swinging wildly between a pleasant sense of pragmatism and gut-wrenching stabs of fear.

imp is a deliberately small per-workspace runtime for delegating work to code-defined agent Imps. It exists both to let useful agent work continue in the background and to make loop engineering concrete enough to explore and understand.

## Requirements

imp's requirements are defined using concrete user stories. Features grow when
concrete use cases require them.

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
   submodule's upstream for review, described well enough — including
   which upstream change necessitated them — to be judged without
   re-deriving the diagnosis.
3. The fix run ends. One piece of pending work is now visible: waiting on
   the upstream review. That wait may last days, and it survives me
   closing every terminal and thinking about something else entirely.
4. Upstream merges on its own schedule. Without me doing anything,
   follow-up work updates the bump PR to consume the merged upstream and
   drives it green again, leaving no dependency on my personal validation
   fork.
5. My remaining jobs are judgment calls only: review the upstream
   submission, merge the green bump PR, and step in when a run reports
   itself stuck — with enough context in the run's log to pick up where it
   stopped.

Every promise in the story binds imp: work that starts without me, stays
visible while I'm gone, waits out external events for days with every
terminal closed, continues past its own run's end with no one bridging the
stages, and leaves a log worth acting on when it stops.

### Conditions the story runs under

- One trusted user on their own Linux machine, with no appetite for
  accounts, tokens, or hardening: each workspace's control plane is a unix
  socket only its user can reach; the work itself connects outward freely.
- The loop must be code I can read, change, and rerun — never a black box.
- One workflow builds the tools and runs the automated check.
- Failure behavior, recovery, cancellation, cleanup, scheduling, retries,
  and resource limits may remain undefined without becoming permanent
  exclusions. Races are not in that deferral: racy code is a correctness
  bug and gets fixed.

Non-goals: authentication/authorization; remote or multi-user operation;
any built-in ticket concept — external work-tracking is bridged by
Clients, never modeled.

## Language

**Run Id** — Client-supplied slug identifying a Run: lowercase ASCII, ≤64
chars, hyphen-joined. A Run Id has at most one Run.
**Run** — a Daemon-owned execution of one Imp under one Run Id,
independent of every Client connection.
**Daemon** — the workspace's long-lived coordinator (`impd`).
**Imp** — an independently executable program: argv in, diagnostics
on stderr, outcome in the exit code; interior structure its own business.
**Launch Body** — the JSON that launches a Run: imp name, Run Id,
argument list.
**Client** — a transient participant that launches and observes Runs
(`impctl`, `impwatch`, humans with curl).
**Watch Row** — a durable impwatch entry: Condition Script argv plus a
clear-after-fire flag.
**Condition Script** — the executable a Watch Row names; its whole
contract is stdout: zero or more Launch Bodies, one JSON per line; stderr
and exit code are diagnostics only.
**Tick** — one cron-driven pass running every Watch Row's script and
POSTing what it emits.

## Decisions in force

- One Daemon per workspace, config-based: `impd --config` reads a
  gitignored name→path map, state lives in the workspace's `.imp/`, and
  cwd is the whole discovery mechanism. (This reverted a registration-era
  design with a user-global `~/.ws2`; see the archived repo for why it
  existed and why it lost.)
- Exec-with-arguments is the entire Imp contract; stderr streams to a
  per-Run log file; stdout is ignored; exit code is the outcome. No
  messages, no structured-result channel — richer artifacts live in the
  domain (PR bodies), where the human already looks.
- Run observation is poll-only; the Daemon never pushes.
- Deterministic Client-chosen Run Ids give idempotent launches: a
  double-fired condition is a rejected duplicate (409), and — deliberately —
  never a retry. A failed Run occupies its Id; relaunching is a human
  judgment call (restart the daemon or pick a fresh Id).
- Firing is defined by emission alone: a clear row that emitted is dropped
  regardless of delivery errors or the script's exit code.
- Happy-path only, but races are correctness bugs: the rows file is
  serialized by a blocking flock held across the whole Tick (an arm waits
  instead of being lost), the Daemon enforces single-instance with a
  nonblocking flock, and a Condition Script must never `arm` (deadlock
  against the Tick's lock).
- Condition Scripts and Imps live in the workspace that owns them,
  paired (a sensor exists to launch its imp); imp keeps only the echo
  fixture the chain check drives.
