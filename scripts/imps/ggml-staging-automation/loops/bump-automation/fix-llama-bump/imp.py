#!/usr/bin/env python3
"""Repair a bump PR in an isolated clone, then verify its CI verdict.

The paired sensor.py supplies the bump PR URL. ../README.md defines the
loop's boundaries and its "When this loop needs a human" judgment, which
the overseer reads through this header.

This wrapper prepares the agent's workspace and enforces the handoff order:
log the report, sync the PR body, arm any upstream-merge wait, then check CI.
STANDING_INSTRUCTIONS owns the repair procedure; the wrapper does not infer
success from the agent's report. Preparation rationale lives beside the
relevant operations below.

The clone uses GitHub state, independent of sources/. Repository-relative
paths locate shared tools and workspace configuration; the Daemon's inherited
cwd lets bare impctl find its socket. The temporary workspace is not a Git
repository, which is why codex receives --skip-git-repo-check.
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

# Not a URL at all: git fails to resolve it, so any `git push` from the
# hrx-system checkout dies before reaching a remote. The name is the
# error message the agent reads when it tries.
HRX_PUSH_URL = "hrx-system-is-never-pushed"

# Cloned directly — the imp is self-contained and depends on no local
# checkout of the staging repo.
STAGING_REPO_URL = "git@github.com:ROCm/ggml-staging-automation.git"

# upstream_pr is the handoff field that arms the reconcile Watch when non-null.
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

# The manifest-expectations rule answers fix-bump-pr-82: CI was red on
# perplexity XPASSes, and the Run dropped the stale `fail` expectations
# but left their paired lemonade-benchmark `skip`s, so the PR went green
# with three healthy models still unbenchmarked. Green CI cannot catch
# that — a skipped check never fails — so the prompt has to say it.
STANDING_INSTRUCTIONS = """\
Your job: make CI green on {pr_url}.

This workspace holds a clone of ggml-staging-automation checked out on the
PR's head branch `{head_branch}`, submodules initialized. There is no
request beyond this prompt — diagnose from the PR itself: `gh pr checks`,
`gh run view --log-failed`, the failing job's logs.

Working rules:

- HRX compatibility floor: llama.cpp's
  `ggml/src/ggml-hrx/hrx-minimum-commit.txt` records its required HRX ancestry.
  When a fix needs newer HRX APIs, Loom syntax, or numerical behavior (including
  removing a workaround after an HRX fix), raise the floor in that same llama.cpp
  commit. Choose the earliest demonstrated required HRX commit that descends
  from the previous floor; do not bump it for unrelated changes or merely to the
  latest tested HEAD. Record the required HRX commit or PR and why it is needed.
  Before validating every staircase step, run
  `python3 llama.cpp/ggml/src/ggml-hrx/tools/check_hrx_revision.py hrx-system`
  from the staging checkout. Staging uses installed packages and bypasses the
  HRX_SOURCE_DIR CMake gate: establish that the tested HRX and Loom libraries
  were built from the checked revision. Never lower the floor to fit a step;
  if the floor cannot be met within the staircase rules, stop and hand off.
  Historical llama.cpp revisions without the file have no recorded floor;
  introduce it with the required commit when a repair adds an HRX dependency.
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
- hrx-system is never changed: no edits to its tree, no commits, no
  pushes to ROCm/hrx-system. Only its submodule pin moves, and only as
  the staircase rule above describes. The bump exists to bring llama.cpp
  up to hrx-system, never the reverse.
- If CI cannot go green without an hrx-system change, stop and hand off.
  The handoff narrative names the problem: what breaks, the hrx-system
  commit or PR that introduced it, the change hrx-system would need, and
  why llama.cpp alone cannot absorb it. Do not push a workaround that
  hides the break. The Run fails on the red PR, and that failure marks
  the Run as needing attention.
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
- Manifest expectations: `benchmarks/hrx/model_manifest.json` records,
  per model under `hrx.expected_results`, the checks not expected to
  pass. A model whose perplexity is expected to fail carries
  `"perplexity": "fail"` together with `"lemonade-benchmark": "skip"`;
  the skip stands only while perplexity fails. When a model's perplexity
  now passes (CI reports it as XPASS), remove BOTH entries in the same
  commit, so lemonade-benchmark runs for that model again. Removing the
  `fail` alone turns CI green while the model stays unbenchmarked. An
  omitted check expects a pass: when the removal leaves `hrx` empty,
  delete the `hrx` block. The re-enabled lemonade-benchmark must pass in
  the PR's CI like any other check.
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
    # Direct human launches can supply URLs outside the staging repository.
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

    # Check login before cloning so a lapse is the visible failure, rather
    # than a startup error buried after workspace preparation.
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

    # slug == the Run Id convention sensor.py emits — the trick that
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
    # (scripts/imps/ggml-staging-automation/loops/bump-automation/fix-llama-bump/imp.py),
    # used only for the agent-config and .venv symlinks below. The project
    # directory (scripts/imps/ggml-staging-automation) holds the build.py
    # shared with the standalone imps; the loop directory holds sync_pr_body.py.
    ws = Path(__file__).resolve().parents[6]
    here = Path(__file__).resolve().parent
    project = here.parents[2]
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

    # Upstream contributor instructions stalled an earlier repair. Remove
    # them locally; skip-worktree keeps add-all commits from publishing the
    # deletion. The workspace's own AGENTS.md remains available below.
    llama = clone / "llama.cpp"
    (llama / "AGENTS.md").unlink()
    run_to_log(
        ["git", "update-index", "--skip-worktree", "AGENTS.md"],
        cwd=llama,
        check=True,
    )
    # Guard default origin pushes mechanically: prose alone proved unreliable.
    # An explicitly chosen remote can still bypass this accident guard.
    run_to_log(
        ["git", "remote", "set-url", "--push", "origin", FORK_PUSH_URL],
        cwd=llama,
        check=True,
    )

    # hrx-system is read-only for this imp; make that mechanical the same
    # way the fork-only rule is, so a push there fails instead of landing.
    run_to_log(
        ["git", "remote", "set-url", "--push", "origin", HRX_PUSH_URL],
        cwd=clone / "hrx-system",
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
    # Reuse the environment for fast local validation; package installs in
    # the Run can therefore affect the shared venv. This isolation tradeoff
    # is accepted.
    (wsdir / ".venv").symlink_to(ws / ".venv", target_is_directory=True)
    shutil.copy2(project / "build.py", wsdir / "build.py")
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

    # The overseer follows the upstream PR and Watch from this logged handoff.
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
    # and crashes visibly. `impctl` comes bare from PATH and finds the
    # Daemon through the inherited cwd; the Sensor is absolute
    # because nothing shares a cwd with anything.
    opened_upstream_pr = handoff["upstream_pr"] is not None
    if opened_upstream_pr:
        run_to_log(
            [
                "impctl", "watch", "--once", "--",
                str(here.parent / "repoint-llama-bump" / "sensor.py"),
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
