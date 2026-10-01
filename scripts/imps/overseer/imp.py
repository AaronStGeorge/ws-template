#!/usr/bin/env python3
"""Check the newest Run under one Sigil and summon a human when needed.

scripts/imps/README.md#overseeing-a-loop owns the integration contract: each
loop supplies a README with its human-attention judgment and a source header
pointing to it. sensor.py owns the weekday schedule. Direct invocation is:

    imp.py --sigil S --model M --effort E --run-id ID

The root Run is the newest Run by started under the overseen Sigil (--sigil),
not under the overseer's own Sigil. Python selects it before invoking Codex;
the check follows only the Run's log, source, README, and references. An older
version asked the agent to survey all Runs and summoned for an unrelated
standalone failure. Selecting one root avoids a growing list of exclusions.

With no root Run, the Imp succeeds without a check. Otherwise a Codex check
returns needs_human, headline, and findings. A positive verdict opens a Claude
Code thread named after --run-id, instructed to investigate, push one headline,
and await direction without making changes. Unusable Codex opens a thread
about the unchecked Run instead. Exit 0 means the check or escalation completed,
not that the root Run needs no attention; listing, check, and thread failures
exit nonzero.

A standing issue opens a fresh thread and push each weekday. Threads remain
in claude agents until the human removes them with claude rm. Reusing threads
was rejected: stopping sessions disconnected the phone, idle sessions stopped
anyway, removal failed without a session daemon, and transcript parsing relied
on an undocumented format. The loop's judgment stays in its README because a
former copy in this Imp drifted from it.

The check cannot see work without a Run, unfinished work under older Runs,
or Runs forgotten by a Daemon restart. It also cannot report its own failures
or a dead Daemon; use impctl status manually. Overseeing overseer itself is
unsupported because the root would be the Run doing the checking.

Change-nothing and root-only restrictions are prompt instructions, not sandbox
guarantees. The Codex sandbox is bypassed because the read-only sandbox tried
here blocked the network access gh needs.
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
# A new background session appears recently active, suppressing its push.
# Disable that presence check through --settings; a plain environment variable
# does not reach the background session.
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
    """Keep child output in the Run log; impd discards Imp stdout."""
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
