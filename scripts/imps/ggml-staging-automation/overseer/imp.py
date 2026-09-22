#!/usr/bin/env python3
"""Imp: check on the ggml-bump loop and keep the human's one thread
about it current.

Paired with the condition.py beside it; `scripts/imps/README.md` is the
record of the pair. `BRIEF` below is the *brief*: the loop's own
judgment, saying how its pieces fit together and what counts as needing
a human. This overseer is specific to the ggml-bump loop — the loop
name, brief, and imp names are constants. A generic overseer can wait
until there are more loops and it is clear what is generic.

Launched once per daily slot with:

    imp.py --model M --effort E --run-id ID

# Language

The *thread* is the human's channel for this loop: a Claude Code
session (`claude --bg --remote-control`, Fable, auto permissions) that
digs into what a check found, pushes one headline to the human's
phone, and waits to be directed from there. It changes nothing until
told to. There is at most one thread per loop; it is named after the
Run that opened it (`oversee-ggml-bump-<date>-<HHMM>`) and found by
that prefix in `claude agents --json --all`, newest `startedAt` first.

# Each check

1. Find the newest thread, if any, and extract its readable turns from
   the transcript Claude Code keeps on disk into a temp file: the
   prompts and the human's messages, the thread's replies, and its push
   headlines — tool calls and results dropped. That transcript format
   is undocumented, so an unreadable one means this imp is broken —
   most likely Claude Code updated under it. That is not a check to
   run with less context; it is a failed Run: the thread is resumed
   with a "fix me" prompt so the human hears about it, and the Run
   exits nonzero.
2. The *check*: `codex exec` (pinned `--model`, reasoning `--effort`),
   schema-forced to `{needs_human, headline, findings, same_issue}`,
   with the brief, the live state (`impctl runs`, `.imp/`, `gh`), and
   the thread extract. It runs at every slot whether or not anything
   is wrong, so it is the cheap one.
3. Act on the verdict against the thread:
   - no thread, needs a human → open a new thread;
   - thread, needs a human, same issue → resume it with the new verdict
     (`claude stop` if alive, then `--bg --resume`, which keeps the
     same id, name and Remote Control; the thread compares, re-verifies,
     pushes one status line, and honors what the human told it earlier);
   - thread, needs a human, different issue → archive it, open a new one;
   - thread, no human needed → the issue is resolved: archive it,
     silently (absence of pings says the same as a closing one would);
   - nothing and nothing → nothing.

*Archive* is `claude stop` + `claude rm`: the session leaves the active
list, its transcript stays on disk and resumable by id, and the id is
in the Run log. No thread state is kept anywhere else — the session
list and the transcript are the memory across days.

Deliberately no check for whether the human is mid-conversation with
the thread when a slot runs: the odds are slim, and the resumed thread
can tell from its own context where things stand.

# Other decisions

A thread's push must not be dropped as redundant. Claude Code skips a
push when the user looks present — its rule is "last interaction under
60 s ago" — which is always true seconds after a thread's prompt lands,
and there is no terminal for that output to reach anyway. The thread is
therefore launched with the presence check disabled through
`--settings` (an env setting; a plain environment variable does not
reach a `--bg` session, and `--settings` is among the options a bare
resume restores).

The check's sandbox is bypassed like the other imps'. codex's read-only
sandbox was tried and blocks network (DNS fails), which `gh` needs; the
change-nothing rule is therefore prose in the prompt, not a fence.

A lapsed codex login is guarded specially: it is the loop's most likely
failure (codex goes weeks between uses) and it would blind the check
itself, so `codex login status` runs first and a failure is escalated
as a verdict of its own — Claude is still available when codex is not.

Exit code: 0 whenever the check completed, whatever its verdict —
`needs_human` is not a failure. Nonzero only when the check or the
thread handling itself broke (codex or claude failing, a schema breach);
the next slot's check sees that failed Run in `impctl runs`. If the
Daemon or the ticker dies, nothing tells anyone — accepted.

Under impd stdout is discarded and stderr is the Run's log, so every
child's stdout is redirected onto stderr.
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
LOOP_README = HERE.parent / "README.md"
IMP_NAMES = "`fix-llama-bump`, `repoint-llama-bump`"
THREAD_PREFIX = "oversee-ggml-bump-"
THREAD_SETTINGS = json.dumps(
    {"env": {"CLAUDE_CODE_DISABLE_NOTIFICATION_PRESENCE_CHECK": "1"}}
)
IMP_DIR = ROOT / ".imp"
IMPCTL = ROOT / "build" / "bin" / "impctl"

# Where Claude Code keeps session transcripts: one jsonl per session,
# under a directory named for the cwd with every non-alphanumeric
# character flattened to "-".
TRANSCRIPTS = (
    Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(ROOT))
)

# The brief: everything loop-specific the check needs to judge with.
# Prose, not code, because it is read by an agent; edit it as the loop's
# failure modes become better known.
BRIEF = """\
# Overseer brief: the ggml-bump loop

This brief is the overseer's judgment for the `ggml-bump` loop. The
loop's mechanics are in `scripts/imps/ggml-staging-automation/README.md`
(workspace-relative); read it if a detail here is not enough.

## What the loop does

An automation in ROCm/ggml-staging-automation opens bump PRs (head
branch `users/automation/bump-submodules`) that move the llama.cpp and
hrx-system submodule pins. When such a PR's CI is red, the standing
sensor launches `fix-llama-bump` (Run Id `fix-bump-pr-<N>`), which
repairs the break in llama.cpp and drives the PR green. If the repair
needed a llama.cpp change upstream still lacks, that Run pushes the fix
to a personal fork, retargets the bump PR at the fork, opens an upstream
llama.cpp PR (AMD-Ecosystem/llama.cpp), and arms a clearing watch on it.
When the upstream PR merges, `repoint-llama-bump` (Run Id
`fix-bump-pr-<N>-repoint`) repoints the bump PR at the merged upstream
and watches its CI.

Each bump PR gets one automatic fix, ever: the Run Id derives from the
PR number, so a PR red again after its fix is silently rejected as a
duplicate launch. Nothing in the loop reports that case.

## Needs a human

- An upstream llama.cpp PR opened by a fix Run is open and waiting for
  review or merge. Find it in the fix Run's log (the handoff names it)
  or in the pending clearing watch's argv. The human must chase the
  review or merge it.
- A bump PR is green and ready to merge (after a fix Run, or after a
  repoint). The loop never merges; the human does.
- A Run failed. Quote the tail of its log. The common cases: codex login
  lapsed (the Run says so before cloning anything); a break that could
  not be fixed in llama.cpp alone (the handoff describes the hrx-system
  change needed); a red PR after a repoint (the Run log holds a
  diagnosis).
- A pending clearing watch whose upstream PR is CLOSED without merging.
  The watch will pend forever; the human decides.
- A bump PR that is red and already has a `fix-bump-pr-<N>` Run — red
  again after its fix. This is the silent case above.

## Fine to ignore

- A Run in state `running`. Fix Runs can take hours.
- A pending clearing watch whose upstream PR is OPEN with recent review
  activity. Say so in the findings, but it is not a summons.
- A bump PR that is red with no fix Run yet and the standing sensor's
  last tick recent: the loop will pick it up.
- An empty `impctl runs` right after a Daemon restart: Run state is
  in-memory. The logs under `.imp/runs/` are the record.
"""

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
        "same_issue": {
            "type": "boolean",
            "description": "When a prior thread was shown and needs_human "
            "is true: whether this is the issue that thread is about. "
            "False otherwise.",
        },
    },
    "required": ["needs_human", "headline", "findings", "same_issue"],
    "additionalProperties": False,
}

CHECK_PROMPT = """\
You are checking on the `ggml-bump` imp loop in the workspace at `{root}`.
Your job is to decide whether a human's attention is needed. You CHANGE
NOTHING: no commits, no pushes, no PR edits or merges, no relaunches, no
arming. You only read and judge.

# The loop's brief

{brief}

# Where to look

- This loop's imps: {imp_names}. Their Runs: `{impctl} runs` (one JSON
  document per line; states succeeded/failed/running). Run state is
  in-memory only, so a Daemon restart empties it — the logs survive.
- Run logs: `{imp_dir}/runs/<run-id>.log`.
- Armed watches: `{imp_dir}/watches.jsonl` (`clear: true` rows are
  one-shot waits armed by imps; standing rows re-fire every tick).
  Their diagnostics: `{imp_dir}/watch.log`.
- GitHub state: `gh pr list`, `gh pr view`, `gh pr checks`.
- Earlier overseer Runs of this loop are `oversee-ggml-bump-*`; a failed
  one is itself a finding.

# The human's current thread about this loop

{thread_section}

# Your answer

Answer with the verdict schema: `needs_human`; a `headline` under 200
characters that says what the human would act on (empty if nothing);
`findings` — a plain-text summary of what you checked and what you
found, with URLs, Run Ids and log paths, written for a second agent who
will pick up from it; and `same_issue` — true only if a thread was shown
above, a human is needed, and what needs them is the issue that thread
is about (its status may have moved; it is still the same issue).
"""

NO_THREAD = "None: there is no open thread. `same_issue` must be false."

THREAD_SHOWN = """\
The thread `{name}` was opened on an earlier check and is still open.
Its readable turns so far (prompts, the human's messages, the thread's
replies, the headlines it pushed to the human's phone) are in
`{extract}`. Read it before judging `same_issue`. If the human said
something there about how to treat this issue, take it into account.
"""

BROKEN_PROMPT = """\
The overseer imp for the `ggml-bump` loop is broken and needs a human:
it could not read this thread's transcript ({reason}). The likely cause
is a Claude Code update that changed the session transcript format the
imp reads (`scripts/imps/ggml-staging-automation/overseer/imp.py`,
`extract_transcript`). No check of the loop was run. Send ONE push
notification with the PushNotification tool saying the overseer is
broken and why, then wait for the human. CHANGE NOTHING until they tell
you to.
"""

OPEN_PROMPT = """\
You are the overseer of the `ggml-bump` imp loop in this workspace. A
scheduled check has concluded a human is needed. Your job: verify and
dig into what it found, then send ONE push notification with the
PushNotification tool — a headline under 200 characters that says what
the human would act on — and wait. The human will continue this session
from their phone. CHANGE NOTHING (no commits, pushes, PR edits, merges,
relaunches, arming) until they tell you to.

# The check's verdict

Headline: {headline}

Findings:
{findings}

The check's own Run log is `{run_log}`.

# The loop's brief

{brief}

# Where to look

- This loop's imps: {imp_names}. Their Runs: `{impctl} runs`. Logs:
  `{imp_dir}/runs/<run-id>.log`. Armed watches: `{imp_dir}/watches.jsonl`;
  their diagnostics: `{imp_dir}/watch.log`. GitHub: `gh`.
- `{readme}` describes the loop's pieces.
"""

UPDATE_PROMPT = """\
A new scheduled check of the `ggml-bump` loop has run and judged that the
issue this thread is about still needs a human. Compare its verdict
below with what you were opened for, re-verify the current state, then
send ONE push notification with the PushNotification tool — a status
headline under 200 characters — and wait. Honor anything the human told
you earlier in this thread about how to handle it. CHANGE NOTHING until
they tell you to.

# The check's verdict

Headline: {headline}

Findings:
{findings}
"""


def run_to_log(argv, **kwargs):
    return subprocess.run(argv, stdout=sys.stderr.fileno(), **kwargs)


def codex_is_usable():
    try:
        return run_to_log(["codex", "login", "status"]).returncode == 0
    except FileNotFoundError:
        return False


def newest_thread():
    """The newest session named with the thread prefix, alive or stopped,
    or None. `--all` is what includes stopped sessions; a stopped thread
    is still the human's thread until it is archived."""
    listing = subprocess.run(
        ["claude", "agents", "--json", "--all", "--cwd", str(ROOT)],
        check=True, stdout=subprocess.PIPE, text=True,
    ).stdout
    threads = [
        session for session in json.loads(listing)
        if str(session.get("name", "")).startswith(THREAD_PREFIX)
    ]
    if not threads:
        return None
    return max(threads, key=lambda session: session.get("startedAt", 0))


def thread_is_alive(thread):
    """Alive means its process is up (busy or idle); `--all` also lists
    stopped ones, which report no status."""
    return thread.get("status") is not None


def extract_transcript(session_id, out_path):
    """Write the thread's readable turns to out_path; the reason on
    failure, else None. Keeps user text (prompts and the human), the
    thread's text replies, and its PushNotification headlines; every
    other tool call and all tool results are noise for this purpose."""
    transcript = TRANSCRIPTS / f"{session_id}.jsonl"
    if not transcript.exists():
        return f"no transcript at {transcript}"
    lines = []
    try:
        for raw in transcript.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = record.get("type")
            if kind not in ("user", "assistant"):
                continue
            content = record.get("message", {}).get("content")
            if isinstance(content, str):
                lines.append(f"[{kind}] {content}")
                continue
            for block in content or []:
                block_type = block.get("type")
                is_push = (
                    block_type == "tool_use"
                    and block.get("name") == "PushNotification"
                )
                if block_type == "text":
                    lines.append(f"[{kind}] {block['text']}")
                elif is_push:
                    lines.append(f"[push] {block['input'].get('message', '')}")
    except (OSError, KeyError, TypeError, AttributeError) as exc:
        return f"transcript unreadable: {exc!r}"
    if not lines:
        return "transcript held no readable turns (format changed?)"
    out_path.write_text("\n\n".join(lines), encoding="utf-8")
    return None


def run_check(args, brief, thread_section, tmp):
    """The codex check, schema-forced to the verdict."""
    schema_path = tmp / "verdict-schema.json"
    verdict_path = tmp / "verdict.json"
    schema_path.write_text(json.dumps(VERDICT_SCHEMA), encoding="utf-8")
    prompt = CHECK_PROMPT.format(
        root=ROOT,
        brief=brief,
        imp_names=IMP_NAMES,
        impctl=IMPCTL,
        imp_dir=IMP_DIR,
        thread_section=thread_section,
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


def open_thread(args, brief, verdict):
    name = args.run_id
    prompt = OPEN_PROMPT.format(
        headline=verdict["headline"],
        findings=verdict["findings"],
        run_log=IMP_DIR / "runs" / f"{args.run_id}.log",
        brief=brief,
        imp_names=IMP_NAMES,
        impctl=IMPCTL,
        imp_dir=IMP_DIR,
        readme=LOOP_README,
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


def short_id(thread):
    """`claude stop` and `claude rm` take the short id; `--resume` takes
    the full session id. The listing carries both."""
    return thread.get("id") or thread["sessionId"][:8]


def stop_thread(thread):
    if thread_is_alive(thread):
        run_to_log(["claude", "stop", short_id(thread)], check=True)


def resume_thread(thread, prompt, why):
    """Resume the thread with a new prompt. A resume must not carry
    flags — those would start a copy; bare, it wakes the same session
    with its saved name, model, permission mode and Remote Control."""
    stop_thread(thread)
    run_to_log(
        ["claude", "--bg", "--resume", thread["sessionId"], prompt],
        cwd=ROOT,
        check=True,
    )
    print(f"thread {thread['name']!r} resumed ({why})", file=sys.stderr)


def update_thread(thread, verdict):
    prompt = UPDATE_PROMPT.format(
        headline=verdict["headline"], findings=verdict["findings"]
    )
    resume_thread(thread, prompt, "update")


def archive_thread(thread, why):
    """Out of the active list, transcript kept: `claude rm` deletes only
    the background-session entry, and the session stays resumable by
    the id printed here."""
    stop_thread(thread)
    run_to_log(["claude", "rm", short_id(thread)], check=True)
    print(
        f"thread {thread['name']!r} archived ({why}); transcript resumable "
        f"as {thread['sessionId']}",
        file=sys.stderr,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="codex model for the check")
    parser.add_argument("--effort", required=True, help="codex reasoning effort")
    parser.add_argument("--run-id", required=True, help="this Run's Id")
    args = parser.parse_args()

    brief = BRIEF
    tmp = Path(tempfile.mkdtemp(prefix=f"{args.run_id}-"))

    # The thread's readable history is the check's memory across days.
    thread = newest_thread()
    extract = None
    if thread is None:
        thread_section = NO_THREAD
    else:
        extract = tmp / "thread.txt"
        reason = extract_transcript(thread["sessionId"], extract)
        # Unreadable is "this imp is broken", not "less context": the
        # thread hears about it and the Run fails.
        if reason is not None:
            print(f"thread {thread['name']!r}: {reason}; overseer is broken",
                  file=sys.stderr)
            resume_thread(thread, BROKEN_PROMPT.format(reason=reason), "broken overseer")
            raise SystemExit(1)
        thread_section = THREAD_SHOWN.format(name=thread["name"], extract=extract)

    # A lapsed login is the one check failure that must not be silent:
    # it would fail the check itself, and nothing else would report it.
    # With no check to judge same_issue, a thread already about the
    # login (its extract mentions it) is the same issue; any other thread
    # is superseded rather than resumed with an unrelated verdict.
    if not codex_is_usable():
        thread_is_about_login = (
            thread is not None
            and "codex login" in extract.read_text(encoding="utf-8")
        )
        print("codex is not usable; escalating without a check", file=sys.stderr)
        verdict = {
            "needs_human": True,
            "headline": "ggml-bump: codex login lapsed — run `codex login` "
            "in the workspace container",
            "findings": "`codex login status` failed (or codex is not on PATH) "
            "before the scheduled check could run. Every imp in this loop "
            "runs codex, so the loop is stalled until the login is renewed.",
            "same_issue": thread_is_about_login,
        }
    else:
        verdict = run_check(args, brief, thread_section, tmp)

    # The verdict against the thread — the five cases in the header.
    needs_human = verdict["needs_human"]
    has_thread = thread is not None
    same_issue = has_thread and needs_human and verdict["same_issue"]
    superseded = has_thread and needs_human and not verdict["same_issue"]
    resolved = has_thread and not needs_human

    if same_issue:
        update_thread(thread, verdict)
    elif superseded:
        archive_thread(thread, "superseded by a different issue")
        open_thread(args, brief, verdict)
    elif resolved:
        archive_thread(thread, "resolved")
    elif needs_human:
        open_thread(args, brief, verdict)
    else:
        print("all clear; no thread", file=sys.stderr)


if __name__ == "__main__":
    main()
