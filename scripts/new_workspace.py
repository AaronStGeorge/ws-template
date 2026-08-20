#!/usr/bin/env python3
"""Create a fresh agent workspace under ``.workspaces/`` for one branch of work.

Terms
  ws          this repo's root (``scripts/..``); the "workspace root".
  sources/    one directory per repo being worked on, each a git checkout whose
              ``origin`` remote is the source of truth for cloning.
  workspace   ``.workspaces/<name>/``: a self-contained tree an agent can be
              pointed at, holding a clone of every source repo checked out on
              a branch of the same name, plus the shared agent config.
  ticket      optional JSON file whose ``branch_mapping`` (``{repo: branch}``)
              names the base branch to fork from per source repo; anything not
              listed forks from ``main``.

Usage::

    scripts/new_workspace.py Tiered CI --ticket tickets/tiered-ci.json
    # -> .workspaces/tiered-ci   (name is slugified: lowercase, [a-z0-9]+, '-')

Result::

    .workspaces/tiered-ci/
      <repo>/            fresh clone of sources/<repo>'s origin, on branch
                         tiered-ci = origin/<base>, untracked (--no-track)
      .agents/skills ->  ../../../.agents/skills   (shared with ws)
      .claude/skills ->  ../../../.agents/skills
      docs ->            ../../docs
      AGENTS.md ->       ../../AGENTS.md
      CLAUDE.md ->       ../../AGENTS.md
      build.py           copy of sources/build.py

Commitments: refuses (mkdir fails) rather than touching an existing workspace;
git errors abort with a non-zero exit; a bad ticket is rejected before anything
is created. Prints the workspace path on success.

Why clone, not worktree: each workspace is independent of the state of
``sources/`` (an earlier version used ``git worktree add`` off the sources
checkout). Why copy build.py rather than link it: it locates its workspace via
``Path(__file__).resolve()``, which would follow a symlink back to ``sources/``.
"""

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path


def load_branch_mapping(ticket_path):
    if ticket_path is None:
        return {}

    ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
    if not isinstance(ticket, dict):
        raise ValueError("Ticket must be a JSON object")

    branch_mapping = ticket.get("branch_mapping")
    if not isinstance(branch_mapping, dict):
        raise ValueError("Ticket branch_mapping must be a JSON object")

    for repo, branch in branch_mapping.items():
        if not isinstance(repo, str):
            raise ValueError("Ticket branch_mapping keys must be strings")
        if not isinstance(branch, str):
            raise ValueError("Ticket branch_mapping values must be strings")

    return branch_mapping


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace_name", nargs="+")
    parser.add_argument("--ticket", type=Path)
    return parser.parse_args()


def main():
    args = parse_args()
    branch_mapping = load_branch_mapping(args.ticket)
    workspace_branch = re.sub(
        r"[^a-z0-9]+", "-", " ".join(args.workspace_name).lower()
    ).strip("-")

    ws = Path(__file__).resolve().parent.parent
    wsdir = ws / ".workspaces" / workspace_branch
    wsdir.mkdir(parents=True)

    sources = sorted(p for p in (ws / "sources").iterdir() if p.is_dir())
    for src in sources:
        origin_url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=src,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        clone = wsdir / src.name
        subprocess.run(
            [
                "git",
                "clone",
                "--origin",
                "origin",
                origin_url,
                str(clone),
            ],
            check=True,
        )

        base_branch = branch_mapping.get(src.name, "main")
        subprocess.run(
            [
                "git",
                "checkout",
                "--no-track",
                "-B",
                workspace_branch,
                f"origin/{base_branch}",
            ],
            cwd=clone,
            check=True,
        )

    shared_skills_target = Path("..", "..", "..", ".agents", "skills")
    for name in (".agents", ".claude"):
        config_dir = wsdir / name
        config_dir.mkdir()
        (config_dir / "skills").symlink_to(
            shared_skills_target, target_is_directory=True
        )

    (wsdir / "docs").symlink_to(
        Path("..", "..", "docs"), target_is_directory=True
    )
    (wsdir / "AGENTS.md").symlink_to(Path("..", "..", "AGENTS.md"))
    (wsdir / "CLAUDE.md").symlink_to(Path("..", "..", "AGENTS.md"))

    shutil.copy2(ws / "sources" / "build.py", wsdir / "build.py")

    print(wsdir)


if __name__ == "__main__":
    main()
