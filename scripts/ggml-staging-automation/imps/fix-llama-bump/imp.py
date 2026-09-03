#!/usr/bin/env python3
"""Imp: drive a failing ggml-staging-automation bump PR to green CI.

Launched by its paired sensor, the condition.py beside this file; the
ggml-staging-automation README (../../README.md) section `fix-llama-bump`
is the authoritative boundary. Argv carries the bump PR URL — the whole
input; everything else is derived from live GitHub state (no ticket, no
standing context document). Exit 0 means the bump PR is green; diagnostics
go to stderr for the Run's log.

Upstream fixes are consumed, never re-derived — but the check is scoped:
the bump already pins llama.cpp at upstream's head as of bump time, so
fixes merged BEFORE that pin are simply in the tree (no step, no proof
needed), and only a fix merged AFTER the pin is consumed explicitly, by a
llama.cpp pin bump. The staircase never moves the hrx-system pin alone —
each step pairs a llama.cpp fix with the break it answers so every pushed
head is a green llama.cpp bump (a first supervised run pushed a lone
hrx-system walk-back and was stopped for it). The imp is
self-contained in this directory: it clones ROCm/ggml-staging-automation
directly from GitHub and copies the ``build.py`` beside this file into the
run workspace — local validation is minutes where a CI round is ~an hour —
with no dependency on any checkout under sources/.

Mined from the hand-launched spike (``scripts/pipelines/fix-llama-bump.py``,
hyphenated): the spike's prep steps and standing instructions are the
paid-for part and carry over nearly verbatim; its ticket plumbing and
structured-result write-back do not — under the Imp Process Contract
the outcome is the exit code and the narrative is stderr.

Names: the slug that titles the /tmp workspace and prefixes the numbered
llama.cpp fork branches is ``fix-bump-pr-<N>`` — deliberately identical
to the Run Id convention condition.py emits, so fork branches
(``fix-bump-pr-46-1``, …) trace to their Run without this process ever
being told its Run Id.

impd gotchas: Imp stdout is discarded and stderr is captured as the
Run's log, so codex's stdout is redirected onto stderr and every wrapper
print goes there too — nothing meaningful may touch stdout. The Daemon is
per-workspace and runs from the workspace root, so this process inherits
that cwd — which is exactly what ``impwatch arm`` needs (it finds its rows
file through cwd); the workspace root is still derived from this file's
own location for the symlink prep, never assumed. ``impwatch`` is invoked
bare and expected on PATH (one-time setup; see the README) to arm the
reconcile watch.

After the agent exits, the wrapper — not the agent — decides the Run: it
arms the reconcile watch iff the schema-forced handoff names an upstream
PR (armed *before* the green check, so a stuck run that opened an
upstream PR still arms), then verifies green mechanically — ``gh pr
checks`` exits 0 iff every check passed, and that, never the agent's
self-report, becomes the exit code.

Prep performed before codex starts, and why (spike-proven):

- Precondition: ``codex login status`` must pass. codex can go weeks
  between uses here and its ChatGPT login can lapse in between; failing
  before any clone makes the Run's log say exactly that, instead of a
  codex startup error buried after the prep. A failed Run is the human's
  summons — ``codex login``, then relaunch under a fresh Run Id.
- The staging work branch is the PR's ``headRefName`` via ``gh`` — a
  property of the PR, never imp config.
- Only ggml-staging-automation is cloned — directly from GitHub — and the
  wrapper, not the agent, initializes submodules, because the next two
  steps need the llama.cpp checkout.
- llama.cpp's upstream ``AGENTS.md`` is deleted: codex auto-ingests any
  AGENTS.md, and upstream's contributor-policy text stalled a prior run
  (agent refused to push). The deletion is marked ``skip-worktree`` so the
  agent's add-all commits cannot sweep it into fork branches. The
  workspace root keeps its own AGENTS.md symlink — that one is wanted.
- llama.cpp's origin *push* URL is overridden to the personal fork, making
  the fork-only rule mechanical for the default ``git push origin`` path —
  prose alone proved unreliable. This guards the accident, not a
  determined agent; proportionate for this experiment.
- ``sync_pr_body.py`` copy (shared by both imps, one level up): the bump
  PR's body names a llama.cpp pin the staircase moves; the agent runs
  this after every push to the PR branch
  to keep the body truthful, and the wrapper runs it once more after the
  handoff as the backstop.
- ``.venv`` symlink + ``build.py`` copy: local validation is minutes where
  a CI round is ~an hour, so the iterate loop leans on it. Shared-venv
  pip-leak trade accepted as before.
- The run workspace is not itself a git repo, so codex needs
  ``--skip-git-repo-check``.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# The narrow pattern keeps anything but a real bump PR URL from starting a
# run; the capture group is the PR number the slug derives from.
STAGING_PR_URL = re.compile(
    r"https://github\.com/ROCm/ggml-staging-automation/pull/(\d+)"
)

FORK_PUSH_URL = "git@github.com:AaronStGeorge/llama.cpp.git"

# Cloned directly — the imp is self-contained and depends on no local
# checkout of the staging repo.
STAGING_REPO_URL = "git@github.com:ROCm/ggml-staging-automation.git"

# The spike's schema plus `upstream_pr`: the one machine-read field of the
# handoff — non-null is what arms the reconcile watch after the agent exits.
HANDOFF_SCHEMA = {
    "type": "object",
    "properties": {
        "outcome": {"type": "string"},
        "narrative": {"type": "string"},
        "pushes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "branch": {"type": "string"},
                },
                "required": ["repo", "branch"],
                "additionalProperties": False,
            },
        },
        "upstream_pr": {"type": ["string", "null"]},
    },
    "required": ["outcome", "narrative", "pushes", "upstream_pr"],
    "additionalProperties": False,
}

# The spike's standing instructions, carried over with three deltas: the
# prompt opens with the job directly (there is no ticket request any more),
# "check upstream first" leads the working rules, and the handoff
# additionally reports the upstream PR URL. Everything else — green
# staircase, push rules, numbered fork branches, upstream-PR creation,
# pre-authorization, no-round-limit iterate, build.py-first — is the
# spike's proven text.
STANDING_INSTRUCTIONS = """\
Your job: make CI green on {pr_url}.

This workspace holds a clone of ggml-staging-automation checked out on the
PR's head branch `{head_branch}`, submodules initialized. There is no
request beyond this prompt — diagnose from the PR itself: `gh pr checks`,
`gh run view --log-failed`, the failing job's logs.

Working rules:

- What is already fixed: the automation's bump pins llama.cpp at the
  upstream (AMD-Ecosystem `hrx-graph-develop-v2`) head as of bump time. A
  break whose fix merged upstream BEFORE that pin is already handled —
  never re-derive it and never build a staircase step to re-prove it. If
  upstream merged a needed fix AFTER the bump's llama.cpp pin, consume it
  by committing a llama.cpp pin bump up to it (canonical URL and branch
  stay); that is the entire use of upstream fixes. Only what upstream
  lacks entirely gets derived here.
- ggml-staging-automation changes: commit on `{head_branch}` and push to
  origin directly — real pushes to the live PR, no side branches, no
  force-pushes: the staircase below is built with regular commits on top
  of the automation's own bump commit.
- llama.cpp submodule changes (only those upstream still lacks): one fix
  commit per breaking hrx-system commit, each on its own branch. Put the first
  fix commit on a branch named `{slug}-1` and push it to origin; cut `{slug}-2`
  from `{slug}-1` for the second fix, and so on — each branch carries every fix
  up to its number. Each fix's commit description contains just: what broke, why
  the API change was required, and `Relevant hrx PR:
  https://github.com/ROCm/hrx-system/pull/N` — the full URL, written out, of the
  ROCm/hrx-system PR that merged the breaking commit (find it with e.g. `gh pr
  list --repo ROCm/hrx-system --search <sha>`). The submodule's push URL is
  pre-set to git@github.com:AaronStGeorge/llama.cpp.git; never push llama.cpp
  anywhere else.
- Build the staircase on `{head_branch}` as green llama.cpp bumps: each
  staircase step is ONE commit that produces a green head — the llama.cpp
  change (a .gitmodules retarget to the fork with the pin at the matching
  `{slug}-N` fix commit, or a plain pin bump for a post-bump upstream
  fix), paired in the same commit with the hrx-system pin at the breaking
  commit that fix answers — except the LAST step, which pins hrx-system
  directly at the bump's original target instead (no breaks lie beyond
  the last one, so the tree is green there with every fix in; the target
  restoration rides the last paired step). NEVER commit an hrx-system
  pin move alone — not even to restore the target: the hrx-system pin
  belongs to the automation's bump, a staircase step exists to show a
  llama.cpp fix making a break green, and a lone hrx move shows nothing.
  When hrx-system already sits at the target and one fix is missing, the
  staircase degenerates to exactly one llama.cpp bump commit, hrx-system
  untouched. Push one step at a time and wait for its CI to go green
  before building the next. The finished PR is the automation's bump
  commit followed by green heads only.
- When llama.cpp fixes were needed, finish by opening a PR to upstream
  llama.cpp: head = the last `{slug}-N` branch on the AaronStGeorge fork
  (it carries every fix commit), base = AMD-Ecosystem/llama.cpp
  `hrx-graph-develop-v2` — e.g. `gh pr create --repo
  AMD-Ecosystem/llama.cpp --base hrx-graph-develop-v2 --head
  AaronStGeorge:{slug}-N`. This opens a PR from the already-pushed fork
  branch; it is not a push to AMD-Ecosystem and is standing policy here,
  pre-authorized. The PR description follows this template exactly,
  filled from the staircase's final state:

  - <hrx-system commit>: the full GitHub link to the hrx-system commit
    the bump pins (the pin the automation's bump commit carries, restored
    by the last staircase step).
  - The table: one row per fix. First column the full GitHub link to the
    ROCm/hrx-system PR that broke the integration; second column the full
    GitHub link to the fix commit on the fork (e.g.
    https://github.com/AaronStGeorge/llama.cpp/commit/<sha>).
  - The two short hashes: the llama.cpp and hrx-system pins at the bump
    PR's green head.
  - The latest run: the GitHub Actions run whose checks made the bump PR
    green.

  ## Motivation

  Fixes required to bump [`hrx-graph-develop-v2`](https://github.com/AMD-Ecosystem/llama.cpp/tree/hrx-graph-develop-v2) up to hrx-system: <full GitHub link to hrx-system commit>.

  ### Breaking changes

  | Breaking hrx-system PR | Fix |
  | --- | --- |
  | <breakage PR link> | <fix commit link> |

  ## Testing

  Tested in `ggml-staging-automation` CI with llama.cpp `<short llama.cpp hash>` and hrx-system `<short hrx-system hash>`.

  - Bump PR: {pr_url}
  - Latest run: [link](<latest run link>).
- Pushing to the PR branch and to the personal fork is standing policy here,
  pre-authorized; contribution-policy files in the repos are no reason to
  pause these pushes.

Iterate: after each push, watch the PR's checks with gh until they finish,
read any failure, fix, push again. No round limit. Local validation first is
cheaper than a CI round — `build.py` runs via `.venv/bin/python build.py`.
After every push to the PR branch run `.venv/bin/python sync_pr_body.py
{pr_url} --head "$(git rev-parse HEAD)"` from the ggml-staging-automation
clone: it rewrites the PR body's llama.cpp row from the pushed commit, so
the description never names a pin the branch does not have. `--head` is
required right after a push — the PR API can report the previous head for
a while.
Stop only when CI is green, or when you are genuinely stuck without human
input.

End with the handoff: how it went (include the final CI state and run
link), a narrative of the changes you made, every repo/branch you pushed
to — each pair listed once, however many times it was pushed — and, in
`upstream_pr`, the URL of the upstream llama.cpp PR if you opened one
(null when none).
"""


def run_to_log(argv, **kwargs):
    """Run a subprocess whose stdout belongs in the Run log.

    Under impd, Imp stdout is discarded and stderr is the Run's log —
    so anything a child would print (git progress, codex's agent
    transcript, `gh pr checks` output) is redirected onto stderr to
    survive.
    """
    return subprocess.run(argv, stdout=sys.stderr.fileno(), **kwargs)


def main():
    # argv is the provenance boundary: the Daemon relays the Launch Body's
    # arguments verbatim, and humans launch by hand too — so the one
    # argument is pattern-checked here and trusted everywhere downstream.
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "pr_url", help="the ggml-staging-automation bump PR URL"
    )
    args = parser.parse_args()

    url_match = STAGING_PR_URL.fullmatch(args.pr_url)
    if url_match is None:
        raise SystemExit(
            f"not a ROCm/ggml-staging-automation PR URL: {args.pr_url}"
        )
    pr_url = url_match.group(0)

    # Precondition, before anything is cloned: a codex binary missing from
    # PATH is the same verdict as a stale login — codex exec would die the
    # same way. The header's prep bullets carry the why.
    try:
        codex_is_usable = subprocess.run(
            ["codex", "login", "status"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
    except FileNotFoundError:
        codex_is_usable = False
    if not codex_is_usable:
        raise SystemExit(
            "codex is not usable (`codex login status` failed or codex is "
            "not on PATH); run `codex login` and relaunch"
        )

    # slug == the Run Id convention condition.py emits — the trick that
    # lets fork branches (`{slug}-1`, …) trace to their Run without this
    # process ever being told its Run Id.
    slug = f"fix-bump-pr-{url_match.group(1)}"

    # stdout=PIPE only, never capture_output: the parsed value comes from
    # stdout, while gh's stderr must flow through to the Run log — a failed
    # call in a headless run is diagnosable only by the message gh printed.
    head_branch = subprocess.run(
        ["gh", "pr", "view", pr_url, "--json", "headRefName",
         "--jq", ".headRefName"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()

    # The workspace root comes from this file's own location
    # (scripts/ggml-staging-automation/imps/fix-llama-bump/imp.py),
    # used only for the agent-config and .venv symlinks below.
    ws = Path(__file__).resolve().parents[4]
    here = Path(__file__).resolve().parent
    wsdir = Path(tempfile.mkdtemp(prefix=f"{slug}-"))

    clone = wsdir / "ggml-staging-automation"
    run_to_log(
        ["git", "clone", "--origin", "origin", STAGING_REPO_URL, str(clone)],
        check=True,
    )
    run_to_log(["git", "checkout", head_branch], cwd=clone, check=True)
    run_to_log(
        ["git", "submodule", "update", "--init"], cwd=clone, check=True
    )

    # Neutralize upstream's AGENTS.md and force llama.cpp pushes to the
    # personal fork — both spike-proven guards; the header's prep bullets
    # carry the full why.
    llama = clone / "llama.cpp"
    (llama / "AGENTS.md").unlink()
    run_to_log(
        ["git", "update-index", "--skip-worktree", "AGENTS.md"],
        cwd=llama,
        check=True,
    )
    run_to_log(
        ["git", "remote", "set-url", "--push", "origin", FORK_PUSH_URL],
        cwd=llama,
        check=True,
    )

    for name in (".agents", ".claude"):
        config_dir = wsdir / name
        config_dir.mkdir()
        (config_dir / "skills").symlink_to(
            ws / ".agents" / "skills", target_is_directory=True
        )
    (wsdir / "docs").symlink_to(ws / "docs", target_is_directory=True)
    (wsdir / "AGENTS.md").symlink_to(ws / "AGENTS.md")
    (wsdir / "CLAUDE.md").symlink_to(ws / "AGENTS.md")
    (wsdir / ".venv").symlink_to(ws / ".venv", target_is_directory=True)
    shutil.copy2(here / "build.py", wsdir / "build.py")
    shutil.copy2(here.parent / "sync_pr_body.py", wsdir / "sync_pr_body.py")

    print(f"run workspace: {wsdir}", file=sys.stderr)

    prompt = STANDING_INSTRUCTIONS.format(
        pr_url=pr_url, head_branch=head_branch, slug=slug
    )
    schema_path = wsdir / ".handoff-schema.json"
    schema_path.write_text(
        json.dumps(HANDOFF_SCHEMA, indent=2) + "\n", encoding="utf-8"
    )
    handoff_path = wsdir / ".handoff.json"

    run_to_log(
        [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "-C",
            str(wsdir),
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(handoff_path),
            prompt,
        ],
        check=True,
    )

    # The handoff's home is the Run log now — the spike wrote it onto the
    # ticket, and tickets are retired; richer artifacts live in the domain
    # (PR bodies) per the shed structured-result channel.
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    print(json.dumps(handoff, indent=2), file=sys.stderr)

    # Backstop the PR-body sync from live head state — the agent was told
    # to run it after each push, but the final body must not depend on
    # that. Cosmetic by design: the Run's verdict is CI alone, so a sync
    # failure is logged, never fatal.
    sync = run_to_log(
        [sys.executable, str(here.parent / "sync_pr_body.py"), pr_url]
    )
    if sync.returncode != 0:
        print("warning: PR body sync failed; body may be stale", file=sys.stderr)

    # Arm the reconcile watch BEFORE the green check: a run that opened an
    # upstream PR but got stuck must still leave the days-long wait armed.
    # The schema forces `upstream_pr`, so a missing key is a real breach
    # and crashes visibly. `impwatch` comes bare from PATH; the condition
    # script is absolute because nothing shares a cwd with anything.
    opened_upstream_pr = handoff["upstream_pr"] is not None
    if opened_upstream_pr:
        run_to_log(
            [
                "impwatch", "arm", "--clear", "--",
                str(here.parent / "repoint-llama-bump" / "condition.py"),
                handoff["upstream_pr"],
                pr_url,
            ],
            check=True,
        )

    # Trust but verify: the agent's self-report never decides the Run.
    # `gh pr checks` exits 0 iff every check passed, and that fact alone
    # becomes this process's exit code — the Run outcome under the
    # Imp Process Contract.
    checks = run_to_log(["gh", "pr", "checks", pr_url])
    bump_pr_is_green = checks.returncode == 0
    if not bump_pr_is_green:
        raise SystemExit(
            f"bump PR is not green: `gh pr checks {pr_url}` exited "
            f"{checks.returncode}"
        )


if __name__ == "__main__":
    main()
