#!/usr/bin/env python3
"""Imp: check on the newest Run of one imp and open a thread for the
human when it needs one.

Paired with the sensor.py beside it. This header is the record of the
pair; `scripts/imps/README.md` says how a loop asks to be overseen.

Launched once per weekday slot with:

    imp.py --sigil S --model M --effort E --run-id ID

# Language

The *overseen Sigil* is `--sigil`: the imp being checked on. It is not
`overseer`, the Sigil this Imp itself runs under.

The *root Run* is the newest Run under the overseen Sigil, by the Run
document's `started`. Everything the check looks at is reached from it.

The *check* is one `codex exec` (pinned `--model`, reasoning `--effort`),
schema-forced to `{needs_human, headline, findings}`. It runs at every
slot that has a root Run, whether or not anything is wrong, so it is the
cheap one.

A *thread* is the human's channel for one summons: a Claude Code session
(`claude --bg --remote-control`, Fable, auto permissions) that digs into
what a check found, pushes one headline to the human's phone, and waits
to be directed from there. It changes nothing until told to. A thread is
named after the Run that opened it (`oversee-<sigil>-<date>-<HHMM>`).

# Requirements

The loops leave the human the judgment calls: a PR to merge, a review to
chase, a failure to look into. Something has to notice when one is due,
because a Run that needs attention says so only in its own log.

One source serves every loop. Sigil names are global in the Daemon, so
there is one `overseer` Sigil, and what differs per loop arrives as
`--sigil`.

It never summons for a Run that is not the overseen imp's, though the
Daemon holds every imp in the workspace.

# Spec

Each launch ends in one of three ways.

No Run under the overseen Sigil: the log says so and the Run succeeds.
No check runs and no thread opens.

codex is not usable: a thread opens with a verdict saying the login
lapsed and naming the root Run that went unchecked.

Otherwise the check runs. `needs_human` opens a thread; anything else
opens nothing.

The check is handed the root Run's document, the path of its log, the
path of the imp's source, and the time. It reads the source's header and
the README that header names, reads the log, and follows what they name:
a follow-on Run, a PR, a Watch.

What this asks of a loop: the overseen imp's header names its README by
path, and that README has a section saying when the loop needs a human.
Without them the check falls back on rules true of any imp, and says so
in its findings.

Exit code: 0 whenever the launch reached one of the three endings,
whatever the verdict; `needs_human` is not a failure. Nonzero only when
the listing, the check, or the thread opening itself broke (impctl, codex
or claude failing, a schema breach).

# What it cannot see

Each of these is accepted.

A Daemon restart. Run state is in-memory, so the listing is empty
afterwards. The overseer finds no root Run and checks nothing until the
overseen imp runs again.

Work with no Run behind it. A red PR the loop's Sensor never launched on
is not reachable from any Run.

An older Run's unfinished business. Once a newer Run exists under the
Sigil, the one before it is no longer the root, and a review it is still
waiting on is not looked at.

Its own failures. A failed overseer Run is reported by nothing; `impctl
status` is the manual check. The same goes for a dead Daemon.

Overseeing `overseer` itself is not supported: the root Run would be the
Run doing the looking.

# Design

The check is rooted in one Run, chosen here in Python, and is never asked
to enumerate. `impctl runs` lists the whole workspace, so a check told to
survey it has to be told what to ignore, and that list changes whenever
an imp is added. A root and "follow what it names" does not change.

`started` is parsed, never compared as text. Go trims trailing zeros from
the fraction, so text order is not time order.

The root Run is looked up before codex is tested. The login guard exists
because a lapse would blind the check. With no root Run there is no check
to blind, and a guard run first would summon on every restart day.

The workspace root is the process's cwd. The Imp process contract makes
it so, and it is the same rule by which bare `impctl` finds the Daemon
whose Runs are listed. Counting parent directories up from this file was
the earlier way, and it broke when the file moved.

A standing issue gets a fresh thread and a fresh push every weekday until
it is resolved. The overseer never looks for, reads, messages, resumes,
or removes an existing thread, and old threads sit in `claude agents`
until the human prunes them with `claude rm`.

A thread's push must not be dropped as redundant. Claude Code skips a
push when the user looks present — its rule is "last interaction under
60 s ago" — which is always true seconds after a thread's prompt lands,
and there is no terminal for that output to reach anyway. The thread is
therefore launched with the presence check disabled through
`--settings` (an env setting; a plain environment variable does not
reach a `--bg` session).

The check's sandbox is bypassed like the other imps'. codex's read-only
sandbox was tried and blocks network (DNS fails), which `gh` needs. Both
the change-nothing rule and the fence around the root Run are therefore
prose in the prompt, not enforced.

A lapsed codex login is guarded specially. codex goes weeks between uses,
and a lapse would fail the check itself with nothing else to report it,
so `codex login status` runs first and a failure is escalated as a
verdict of its own. Claude is still available when codex is not.

Under impd stdout is discarded and stderr is the Run's log, so every
child's stdout is redirected onto stderr. The Run listing is the one
exception: it is data, and is captured.

# Roads not taken

Thread management. Until Sept 2026 this imp managed one thread across
days: it found the newest thread by name prefix, extracted its transcript
from the jsonl Claude Code keeps on disk, had the check judge whether a
new verdict was the same issue, updated the thread by `claude stop` + a
bounded wait + a bare `claude --bg --resume` (with a guard against the
resume forking a copy), and archived it by `claude stop` + `claude rm`.
It was removed because each piece cost more than it returned: the stop
disconnected the human's phone twice a day; Claude Code stops an idle
unattached session's process after about an hour regardless, so most
updates woke a stopped session anyway; `claude rm` failed whenever no
session daemon was up, which broke five Runs in a row; and the transcript
format is undocumented, so every Claude Code update could break the
read. The human's judgment: not worth it.

A brief. Until Sept 2026 this imp was the bump loop's own and carried a
*brief*: a constant holding that loop's story and its lists of what
needs a human. The loop's README told the same story, and the two had
drifted: the README named a case the brief lacked. The judgment now
lives in the loop's README alone, beside the imps it is about.

A survey. The check used to be told to run `impctl runs` and read
`.imp/runs/` for itself. On 2026-09-28 it read a standalone imp's failed
Runs and summoned the human for them. A prose rule to ignore other
Sigils held afterwards, but it had to name what to ignore.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# The Imp process contract makes cwd the workspace root. Bare `impctl`
# resolves the Daemon from the same cwd, so the Runs listed and the paths
# built here always belong to one workspace.
ROOT = Path.cwd()
RUNS_DIR = ROOT / ".imp" / "runs"
WATCH_LOG = ROOT / ".imp" / "watch.log"
THREAD_SETTINGS = json.dumps(
    {"env": {"CLAUDE_CODE_DISABLE_NOTIFICATION_PRESENCE_CHECK": "1"}}
)

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "needs_human": {"type": "boolean"},
        "headline": {
            "type": "string",
            "description": "One line, under 200 characters: what the human "
            "would act on. Empty when needs_human is false.",
        },
        "findings": {
            "type": "string",
            "description": "Plain-text summary of everything checked and "
            "what was found, with PR URLs, Run Ids and log paths.",
        },
    },
    "required": ["needs_human", "headline", "findings"],
    "additionalProperties": False,
}

# What both prompts are told about the root Run. Everything else the check
# and the thread look at is reached from these three things.
ROOT_RUN = """\
- Its document: `{run_json}`
- Its log: `{run_log}`
- The imp's source: `{source}`"""

# Rules true of any imp, from the Imp process contract alone. What a given
# loop leaves waiting on a human is that loop's judgment and lives in its
# README, which outranks these.
GENERAL_RULES = """\
- A Run in state `failed` needs a human. Quote the tail of its log.
- A Run in state `starting` or `running` is not a finding. Say how long
  it has been going.
- A Run in state `succeeded` that left nothing waiting on a human is
  clear.
- A follow-on Run the log led you to, in state `failed`, needs a human.
- A one-shot Watch the log says was armed, now absent from
  `impctl watches` with no Run to show for it, needs a human: the wait
  was lost, and re-arming it is a human step."""

CHECK_PROMPT = """\
You are checking on one Run of the imp `{sigil}` in the workspace at
`{root}`, to decide whether a human's attention is needed. You CHANGE
NOTHING: no commits, no pushes, no PR edits or merges, no relaunches, no
arming. You only read and judge.

# The Run

{root_run}

It is the newest Run under the Sigil `{sigil}`. The time is now {now}
UTC; the document's `started` is when the Run began.

# How to check

1. Read the header of the imp's source, then the README that header
   names. Look in that README for a section on when a human is needed:
   it is the judgment of the people who own this imp.
2. Read the Run's log. It can run to hundreds of kilobytes: read the end
   first, then search it.
3. Follow what the log and the README name, and only that: a follow-on
   Run by its Run Id (`impctl runs`; its log is
   `{runs_dir}/<run-id>.log`), a pull request through `gh`, a Watch by
   its argv (`impctl watches`; Sensor diagnostics in `{watch_log}`).

`impctl runs` and `impctl watches` list every imp in the workspace. A Run
or Watch that this Run's log and README do not lead you to is not yours:
do not read its log and do not report it.

# What needs a human

The README's section on when a human is needed decides. Where it is
silent, these rules hold for any imp:

{general_rules}

If the source is missing, or its header names no README, or the README
has no such section, judge by the rules above and say which was missing
in your findings.

# Your answer

Answer with the verdict schema: `needs_human`; a `headline` under 200
characters that says what the human would act on (empty if nothing); and
`findings` — a plain-text summary of what you checked and what you
found, with URLs, Run Ids and log paths, written for a second agent who
will pick up from it.
"""

OPEN_PROMPT = """\
You are the overseer's thread for one Run of the imp `{sigil}` in this
workspace. A scheduled check has concluded a human is needed. Your job:
verify and dig into what it found, then send ONE push notification with
the PushNotification tool — a headline under 200 characters that says
what the human would act on — and wait. The human will continue this
session from their phone. CHANGE NOTHING (no commits, pushes, PR edits,
merges, relaunches, arming) until they tell you to.

# The check's verdict

Headline: {headline}

Findings:
{findings}

The check's own Run log is `{check_log}`.

# The Run that was checked

{root_run}

# Where to look

- The header of the imp's source names its README, which describes the
  imp, the loop it belongs to, and when that loop needs a human.
- Follow-on Runs: `impctl runs`, logs at `{runs_dir}/<run-id>.log`.
  Armed Watches: `impctl watches`; Sensor diagnostics in `{watch_log}`.
  GitHub: `gh`.
- Both listings cover every imp in the workspace. Keep to what this
  Run's log and README lead to.
"""


def run_to_log(argv, **kwargs):
    return subprocess.run(argv, stdout=sys.stderr.fileno(), **kwargs)


def newest_run_under(sigil):
    """The root Run: the newest Run under `sigil`, or None if it has none."""
    # The listing is data, so it is captured. The default redirect would
    # copy every other imp's Run into this Run's log.
    listing = subprocess.run(
        ["impctl", "runs"], check=True, stdout=subprocess.PIPE, text=True
    ).stdout
    runs = [json.loads(line) for line in listing.splitlines() if line.strip()]
    runs_under_sigil = [run for run in runs if run["sigil"] == sigil]
    if not runs_under_sigil:
        return None
    print(f"{len(runs_under_sigil)} Run(s) under {sigil!r}", file=sys.stderr)
    # Parsed, never compared as text: Go trims trailing zeros from the
    # fraction, so "…05.1Z" sorts after "…05.12Z" though it is earlier.
    return max(
        runs_under_sigil,
        key=lambda run: datetime.fromisoformat(run["started"]),
    )


def describe_root_run(run):
    # The document's `path` is whatever the Sigil held at launch, relative
    # to the workspace root or absolute; joining keeps an absolute path.
    return ROOT_RUN.format(
        run_json=json.dumps(run),
        run_log=RUNS_DIR / f"{run['id']}.log",
        source=ROOT / run["path"],
    )


def codex_is_usable():
    try:
        return run_to_log(["codex", "login", "status"]).returncode == 0
    except FileNotFoundError:
        return False


def codex_lapsed_verdict(sigil, run):
    """The verdict for a check that could not run, written here because
    codex is what would have written it."""
    return {
        "needs_human": True,
        "headline": f"overseer {sigil}: codex login lapsed — run "
        "`codex login` in the workspace container",
        "findings": "`codex login status` failed (or codex is not on PATH) "
        f"before the scheduled check of Run `{run['id']}` could run, so "
        f"that Run (state `{run['state']}`, started {run['started']}) went "
        "unchecked. Any imp in this workspace that runs codex fails the "
        "same way until the login is renewed.",
    }


def run_check(args, root_run, tmp):
    """The codex check, schema-forced to the verdict."""
    schema_path = tmp / "verdict-schema.json"
    verdict_path = tmp / "verdict.json"
    schema_path.write_text(json.dumps(VERDICT_SCHEMA), encoding="utf-8")
    prompt = CHECK_PROMPT.format(
        sigil=args.sigil,
        root=ROOT,
        root_run=root_run,
        now=f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M}",
        runs_dir=RUNS_DIR,
        watch_log=WATCH_LOG,
        general_rules=GENERAL_RULES,
    )
    run_to_log(
        [
            "codex", "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "-C", str(ROOT),
            "--model", args.model,
            "-c", f"model_reasoning_effort={json.dumps(args.effort)}",
            "--output-schema", str(schema_path),
            "--output-last-message", str(verdict_path),
            prompt,
        ],
        check=True,
    )
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    print(f"verdict: {json.dumps(verdict, indent=2)}", file=sys.stderr)
    return verdict


def open_thread(args, root_run, verdict):
    name = args.run_id
    prompt = OPEN_PROMPT.format(
        sigil=args.sigil,
        headline=verdict["headline"],
        findings=verdict["findings"],
        check_log=RUNS_DIR / f"{args.run_id}.log",
        root_run=root_run,
        runs_dir=RUNS_DIR,
        watch_log=WATCH_LOG,
    )
    # --bg prints the session's short id and returns; that id in the Run
    # log is how a human attaches from a terminal (`claude attach`).
    run_to_log(
        [
            "claude", "--bg",
            "--remote-control", name,
            "--name", name,
            "--model", "fable",
            "--permission-mode", "auto",
            "--settings", THREAD_SETTINGS,
            prompt,
        ],
        cwd=ROOT,
        check=True,
    )
    print(f"thread {name!r} opened", file=sys.stderr)


def main():
    # Parse argv → find the root Run (none: done) → test codex (lapsed: a
    # verdict without a check) → otherwise check → act on the verdict.
    parser = argparse.ArgumentParser()
    parser.add_argument("--sigil", required=True, help="the Sigil to oversee")
    parser.add_argument("--model", required=True, help="codex model for the check")
    parser.add_argument("--effort", required=True, help="codex reasoning effort")
    parser.add_argument("--run-id", required=True, help="this Run's Id")
    args = parser.parse_args()
    print(f"overseeing sigil {args.sigil!r}", file=sys.stderr)

    # Before the codex guard, on purpose: with nothing to check there is
    # no check for a lapsed login to blind.
    run = newest_run_under(args.sigil)
    if run is None:
        print(
            f"no Run under sigil {args.sigil!r}; nothing to check",
            file=sys.stderr,
        )
        return
    root_run = describe_root_run(run)
    print(f"root Run:\n{root_run}", file=sys.stderr)

    # A lapsed login is the one check failure that must not be silent:
    # it would fail the check itself, and nothing else would report it.
    if not codex_is_usable():
        print("codex is not usable; escalating without a check", file=sys.stderr)
        verdict = codex_lapsed_verdict(args.sigil, run)
    else:
        tmp = Path(tempfile.mkdtemp(prefix=f"{args.run_id}-"))
        verdict = run_check(args, root_run, tmp)

    if verdict["needs_human"]:
        open_thread(args, root_run, verdict)
    else:
        print("all clear; no thread", file=sys.stderr)


if __name__ == "__main__":
    main()
