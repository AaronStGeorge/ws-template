#!/usr/bin/env python3
"""Pipeline: drive a failing ggml-staging-automation bump PR to green CI.

A *pipeline* is a ticket-shape-specific runner: it assumes a particular kind
of ticket and encodes the whole job for that kind. This is the first one; a
future orchestrator will match tickets to pipelines, but today a human
invokes it. It replaces the generic ``scripts/run_ticket.py`` (see git
history) whose two flights showed the recurring job is exactly this one.

The job: the submodule-bump automation opens PRs like
https://github.com/ROCm/ggml-staging-automation/pull/46 whose CI breaks
because hrx-system moved and the staging repo / llama.cpp submodule didn't.
Repair lands in two places with different push rules:

- ggml-staging-automation fixes are committed on the PR's head branch and
  pushed to origin directly — real pushes to the live PR.
- llama.cpp fixes are pushed ONLY to the personal fork
  ``git@github.com:AaronStGeorge/llama.cpp.git`` (never a ROCm remote), on a
  branch named by the ticket slug, then consumed by a staging commit that
  retargets ``.gitmodules`` (branch + pin) so CI can fetch them.

The codex agent iterates — push, watch the PR's checks, read failing logs,
fix, push again — with no round cap, exiting when CI is green or it is
genuinely stuck; the handoff records the final CI state either way.

Terms
  ws        this repo's root (``scripts/pipelines/../..``).
  ticket    ``tickets/NXXX_<slug>.json`` + sibling ``.md``; hand-authored
            (the to-tickets tooling was retired pending a pipeline-era
            rework). This file is the authority on the shape consumed:
            ``{title, description → .md}`` where the .md text — the
            *request* — contains the staging PR URL and may be nothing else.
  slug      slugified ticket title; names the /tmp workspace and the
            llama.cpp fork branch (NOT the staging branch, which belongs to
            the PR).
  handoff   codex's schema-forced final message, written onto the ticket
            JSON as ``result`` (plus a wrapper-added ``workspace``);
            re-running overwrites it.

Usage (blocking; run under nohup/tmux yourself to detach)::

    scripts/pipelines/fix-llama-bump.py tickets/N003_x.json

Prep the wrapper performs before codex starts, and why:

- The request is parsed for exactly one
  ``github.com/ROCm/ggml-staging-automation/pull/N`` URL — the narrow
  pattern keeps reference PRs cited in prose from hijacking the run; zero or
  several such URLs is a crash, per the no-validation-beyond-crashing tier.
- The staging work branch is the PR's ``headRefName`` via ``gh`` — a
  property of the ticket's PR, never pipeline config.
- Only ggml-staging-automation is cloned  and the wrapper — not the agent —
  initializes submodules, because the next two steps need the llama.cpp
  checkout.
- llama.cpp's upstream ``AGENTS.md`` is deleted: codex auto-ingests any
  AGENTS.md, and upstream's contributor-policy text stalled a prior run
  (agent refused to push). The deletion is marked ``skip-worktree`` so the
  agent's add-all commits cannot sweep it into fork branches. The workspace
  root keeps its own AGENTS.md symlink — that one is wanted.
- llama.cpp's origin *push* URL is overridden to the personal fork, making
  the fork-only rule mechanical for the default ``git push origin`` path —
  prose alone proved unreliable. This guards the accident, not a determined
  agent; proportionate for this experiment.
- ``.venv`` symlink + ``build.py`` copy: local validation is minutes where a
  CI round is ~an hour, so the iterate loop leans on it. Shared-venv
  pip-leak trade accepted as before.

Gotcha: the workspace root is not itself a git repo, so codex needs
``--skip-git-repo-check``.
"""

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

STAGING_PR_URL = re.compile(
    r"https://github\.com/ROCm/ggml-staging-automation/pull/\d+"
)

FORK_PUSH_URL = "git@github.com:AaronStGeorge/llama.cpp.git"

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
    },
    "required": ["outcome", "narrative", "pushes"],
    "additionalProperties": False,
}

STANDING_INSTRUCTIONS = """

---

Your job: make CI green on {pr_url}.

This workspace holds a clone of ggml-staging-automation checked out on the
PR's head branch `{head_branch}`, submodules initialized. The request above
may be no more than the PR URL — diagnose from the PR itself: `gh pr
checks`, `gh run view --log-failed`, the failing job's logs.

Push rules:

- ggml-staging-automation changes: commit on `{head_branch}` and push to
  origin directly — real pushes to the live PR, no side branches.
- llama.cpp submodule changes: commit on a branch named `{slug}` and push it
  to origin. The submodule's push URL is pre-set to
  git@github.com:AaronStGeorge/llama.cpp.git; never push llama.cpp anywhere
  else. Then make a corresponding ggml-staging-automation commit updating
  .gitmodules (branch = `{slug}`, submodule pin = your new commit) so CI can
  fetch your work, and push that to `{head_branch}` too.
- Pushing to the PR branch and to the personal fork is standing policy here,
  pre-authorized; contribution-policy files in the repos are no reason to
  pause these pushes.

Iterate: after each push, watch the PR's checks with gh until they finish,
read any failure, fix, push again. No round limit. Local validation first is
cheaper than a CI round — `build.py` runs via `.venv/bin/python build.py`.
Stop only when CI is green, or when you are genuinely stuck without human
input.

End with the handoff: how it went (include the final CI state and run link),
a narrative of the changes you made, and every repo/branch you pushed to —
each pair listed once, however many times it was pushed.
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ticket", type=Path)
    args = parser.parse_args()

    ticket_path = args.ticket.resolve()
    ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
    request = (ticket_path.parent / ticket["description"]).read_text(
        encoding="utf-8"
    )

    pr_urls = sorted(set(STAGING_PR_URL.findall(request)))
    exactly_one_pr = len(pr_urls) == 1
    if not exactly_one_pr:
        raise SystemExit(
            f"expected exactly one staging PR URL in the request, got {pr_urls}"
        )
    pr_url = pr_urls[0]

    head_branch = subprocess.run(
        ["gh", "pr", "view", pr_url, "--json", "headRefName",
         "--jq", ".headRefName"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    slug = re.sub(r"[^a-z0-9]+", "-", ticket["title"].lower()).strip("-")
    ws = Path(__file__).resolve().parent.parent.parent
    wsdir = Path(tempfile.mkdtemp(prefix=f"{slug}-"))

    src = ws / "sources" / "ggml-staging-automation"
    origin_url = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=src,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    clone = wsdir / "ggml-staging-automation"
    subprocess.run(
        ["git", "clone", "--origin", "origin", origin_url, str(clone)],
        check=True,
    )
    subprocess.run(["git", "checkout", head_branch], cwd=clone, check=True)
    subprocess.run(
        ["git", "submodule", "update", "--init"], cwd=clone, check=True
    )

    llama = clone / "llama.cpp"
    (llama / "AGENTS.md").unlink()
    subprocess.run(
        ["git", "update-index", "--skip-worktree", "AGENTS.md"],
        cwd=llama,
        check=True,
    )
    subprocess.run(
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
    shutil.copy2(ws / "sources" / "build.py", wsdir / "build.py")

    print(wsdir)

    prompt = request + STANDING_INSTRUCTIONS.format(
        pr_url=pr_url, head_branch=head_branch, slug=slug
    )
    schema_path = wsdir / ".handoff-schema.json"
    schema_path.write_text(
        json.dumps(HANDOFF_SCHEMA, indent=2) + "\n", encoding="utf-8"
    )
    handoff_path = wsdir / ".handoff.json"

    subprocess.run(
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

    result = json.loads(handoff_path.read_text(encoding="utf-8"))
    result["workspace"] = str(wsdir)
    ticket["result"] = result
    ticket_path.write_text(
        json.dumps(ticket, indent=2) + "\n", encoding="utf-8"
    )
    print(ticket_path)


if __name__ == "__main__":
    main()
