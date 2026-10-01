#!/usr/bin/env python3
"""Load a workspace from ``workspaces/<name>/`` into ``sources/``.

A workspace is a ``workspace.json`` naming the repositories to clone, plus an
``assets/`` directory of files that belong beside the clones (usually a
``build.py``)::

    {"repos": [{"url": "git@github.com:org/repo.git", "ref": "branch-tag-or-sha"}]}

``ref`` is optional; without it the clone stays on the remote's default branch.

Loading replaces whatever ``sources/`` holds, so it first refuses if anything
there exists only locally. Assets are symlinked rather than copied so that edits
made through ``sources/`` land in the tracked ``assets/`` directory.

    python scripts/ws_load.py llama-cpp
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES = REPO_ROOT / "sources"
WORKSPACES = REPO_ROOT / "workspaces"


def git_output(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def loaded_entries() -> list[Path]:
    return sorted(entry for entry in SOURCES.iterdir() if entry.name != ".gitignore")


def unsaved_work(entry: Path) -> list[str]:
    """Reasons deleting ``entry`` would lose something, empty when it is safe."""
    # Removing a symlink never removes what it points to, whether that is an
    # asset or something a build linked in.
    if entry.is_symlink():
        return []
    if not (entry / ".git").exists():
        return ["not a git repository or symlink"]

    reasons = []
    if git_output(entry, "status", "--porcelain"):
        reasons.append("uncommitted or untracked files")
    if git_output(entry, "log", "--oneline", "--branches", "HEAD", "--not", "--remotes"):
        reasons.append("commits not on any remote")
    return reasons


def clone(repo: dict[str, str]) -> None:
    url = repo["url"]
    destination = SOURCES / url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    subprocess.run(["git", "clone", url, str(destination)], check=True)
    if "ref" in repo:
        # A remote branch name becomes a local tracking branch; a tag or SHA
        # detaches. Both are git's own defaults.
        subprocess.run(["git", "-C", str(destination), "checkout", repo["ref"]], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", help="Directory name under workspaces/.")
    args = parser.parse_args()

    workspace = WORKSPACES / args.name
    manifest = json.loads((workspace / "workspace.json").read_text(encoding="utf-8"))

    entries = loaded_entries()
    blocked = {entry: reasons for entry in entries if (reasons := unsaved_work(entry))}
    if blocked:
        print("refusing to load: sources/ holds work that would be lost", file=sys.stderr)
        for entry, reasons in blocked.items():
            print(f"  {entry.name}: {', '.join(reasons)}", file=sys.stderr)
        return 1

    for entry in entries:
        if entry.is_symlink():
            entry.unlink()
        else:
            shutil.rmtree(entry)

    for repo in manifest["repos"]:
        clone(repo)

    assets = workspace / "assets"
    if assets.is_dir():
        for asset in sorted(assets.iterdir()):
            # Relative, so the links survive the repository being moved or mounted
            # at another path.
            (SOURCES / asset.name).symlink_to(os.path.relpath(asset, SOURCES))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
