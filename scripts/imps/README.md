# Imps

Everything imp in this workspace lives here. A Loop is one directory
holding the Imps, Sensors, and Manifest that form one automation; a
standalone Imp is one a human launches by hand, with a Sigil but no
Sensor. The runtime that runs them is [tools/README.md](../../tools/README.md)
and the Language and contracts they honor are
[docs/imp-design.md](../../docs/imp-design.md). This README is how imps
are laid out, brought up, and written.

## Running this workspace's imps

```sh
impctl up --manifest scripts/imps/ggml-staging-automation/loops/bump-automation/bump-loop.json \
          --manifest scripts/imps/ggml-staging-automation/standalone-imps.json
impctl status
impctl down
```

`up` is the whole answer to "what does this workspace run": start the
Daemon if needed, wait for it, apply each named Manifest. Rerunning it is
free, and a Sigil or Watch added by hand survives it. `down` stops the
Daemon and with it every Watch, including a one-shot armed mid-Run;
re-arming one is a human step. Each loop's README lists what its Imps
need logged in or on `PATH`.

## How imps are laid out

Imps are grouped by the project they act on: one directory per project
under `scripts/imps/`, such as
[ggml-staging-automation/](ggml-staging-automation/). Inside it, `loops/`
holds one directory per loop, and each standalone Imp has its own
directory beside `loops/`. Scripts shared by a project's Imps sit at the
project level. Two things act on no project and stay directly under
`scripts/imps/`: [loops/echo/](loops/echo/), the reference example, and
[overseer/](overseer/), which serves every loop.

A loop is directory-per-imp: each Imp's entry point is its `imp.py`, the
Sensor that launches it is the `sensor.py` beside it, and
its private resources sit alongside. Scripts two of a loop's Imps share
sit one level up, in the loop directory. Each Imp resolves shared scripts
relative to its own file and copies them into its run workspace; Imps
never import across directories.

Each loop's Manifest is a JSON file named for the loop, such as
`bump-loop.json`, per the design record's shape. A project's standalone
Imps share one Manifest, `standalone-imps.json`, with Sigils and no
Watches, so `impctl launch` can name them. A loop that wants overseeing
adds two ordinary entries to its Manifest, as
[Overseeing a loop](#overseeing-a-loop) describes.

Editing a standing Watch's argv in a Manifest and re-running `up` adds a
second Watch beside the old one, because the Daemon matches on exact
argv. Remove the old one with `impctl unwatch ID` (the id is in
`impctl watches`), or take the Daemon through `down` and `up`.

## Writing a loop: the echo example

[loops/echo/](loops/echo/) is the smallest complete loop and the one the
chain check drives. Its Manifest binds one Sigil and declares one standing
Watch:

```json
{
  "sigils": {"echo": "scripts/imps/loops/echo/imp.py"},
  "watches": [["scripts/imps/loops/echo/sensor.py"]]
}
```

`imp.py` writes its argv to stderr and exits 0: the whole Imp process
contract in one line. `sensor.py` has no condition to test and prints
the same Launch on every Tick, under the fixed Run Id `echo-standing`:

```json
{"sigil": "echo", "id": "echo-standing", "args": ["standing-token"]}
```

Bringing it up and ticking it by hand shows every piece working, and what
"the design working" looks like on the second Tick:

```
$ impctl up --manifest scripts/imps/loops/echo/imps.json
impd ready in 12ms (pid 41234)
created echo -> scripts/imps/loops/echo/imp.py
watch 1 standing (created): scripts/imps/loops/echo/sensor.py
$ impctl tick
{"watches":1,"launched":1,"duplicates":0,"dropped":0}
$ impctl runs
{"sigil":"echo","id":"echo-standing","path":"scripts/imps/loops/echo/imp.py","started":"2026-09-29T15:04:05.123456789Z","state":"succeeded","error":null}
$ cat .imp/runs/echo-standing.log
standing-token
$ impctl tick
{"watches":1,"launched":0,"duplicates":1,"dropped":0}
$ impctl up --manifest scripts/imps/loops/echo/imps.json
impd already running (pid 41234)
unchanged echo -> scripts/imps/loops/echo/imp.py
watch 1 standing (existing): scripts/imps/loops/echo/sensor.py
```

A real loop differs from echo in three ways. Its Sensor has a
condition: it queries the world (`gh`, the clock) and emits nothing when
the answer is "not yet". Its Run Ids derive from the thing discovered
(`fix-bump-pr-<N>` from a PR number, `oversee-<sigil>-<date>-<HHMM>` from
a slot's configured time, never the Tick's), so the same discovery is the
same Id on every Tick and never tracked. And its Imp may arm a follow-on
wait before it exits, which a Tick days later fires with no process having
waited in between:

```sh
impctl watch --once -- /abs/path/to/next/sensor.py <args that exist only now>
```

Rules the existing loops learned:

- Stdout is the contract, in both directions. An Imp's stdout is
  discarded, so redirect every child's stdout onto stderr or it vanishes
  from the Run log. A Sensor's stdout must carry Launches
  only, so capture children's stdout (`stdout=PIPE`, never
  `capture_output`, so their stderr still reaches the watch log).
- argv is the provenance boundary. The Daemon relays Launch arguments
  verbatim and humans launch by hand too, so validate arguments at the top
  of the executable and trust them everywhere below.
- Name run workspaces and branches after the Run Id. An Imp is never told
  its Run Id, but a slug derived the same way the Sensor derives
  the Id traces everything the Run made back to it.
- Arm one-shot Watches with an absolute path to the Sensor. Nothing
  shares a cwd with anything except the Daemon.
- Imps and Sensors are executables. Set the exec bit and let git track
  the mode; `impctl apply`, `inscribe`, and `watch` all refuse a file that
  is not executable, so the mistake surfaces where you typed the path
  rather than as a failed Run or a watch-log line every Tick.

## Overseeing a loop

An *overseer* is an Imp whose job is to judge whether a human is needed
and, if so, get their attention. The workspace has one,
[overseer/](overseer/), and it serves every loop: it is told a Sigil,
finds the newest Run under it, and checks on that Run. Its
[header](overseer/imp.py) is the record of how, and of what it cannot
see.

A loop asks to be overseen with two entries in its Manifest. The Sigil
line is the same in every Manifest, because Sigil names are global in the
Daemon and re-inscribing the same path is a no-op. The Watch names the
Sigil to oversee, the weekday slot, and the check's codex knobs:

```json
{
  "sigils": {"overseer": "scripts/imps/overseer/imp.py"},
  "watches": [["scripts/imps/overseer/sensor.py",
               "--sigil", "fix-llama-bump",
               "--at", "09:00", "--tz", "America/Boise",
               "--model", "gpt-6-astra", "--effort", "medium"]]
}
```

The overseer asks two things of the loop in return. The overseen Imp's
header names the loop's README by path, because the Imp's path is all the
check is given. And that README has a section saying when the loop needs
a human, because that judgment is the loop's and the overseer holds none
of it. The bump loop's
[README](ggml-staging-automation/loops/bump-automation/README.md) is the
example.

## The imps

- [loops/echo/](loops/echo/): the reference example above. It runs no
  automation.
- [overseer/](overseer/): the workspace's overseer, described above.
- [ggml-staging-automation/](ggml-staging-automation/): the imps that act
  on ROCm/ggml-staging-automation. Its
  [bump-automation](ggml-staging-automation/loops/bump-automation/) loop is
  the one the design record's story is about, and it is overseen. Its
  standalone `fix-llama-perplexity` Imp investigates one perplexity
  report entry on demand.
