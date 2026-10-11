"""Where things are in a workspace, and what a thread may be called.

A workspace is a clone of this repository. Its threads are worktrees under
``.threads/`` in the main checkout, and its sourcesets are checked in under
``sourcesets/``, so both are reachable from any worktree by first finding the
main checkout through git's common dir.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# The same shape as an imp Run Id: a thread name is a worktree directory
# name, a Codex thread name and often a bead id, and the strictest of those
# is the path segment.
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NAME_MAX_LENGTH = 64

THREADS_DIR = ".threads"
SOURCESETS_DIR = "sourcesets"
SOURCES_DIR = "sources"


def validate_name(name: str) -> str:
    """Return ``name`` if it can name a thread, else raise ValueError.

    Called where a name enters from outside: the command line, or a bead id
    read back from ``br``.
    """
    is_slug = NAME.fullmatch(name) is not None
    fits = len(name) <= NAME_MAX_LENGTH
    if not (is_slug and fits):
        raise ValueError(
            f"not a thread name: {name!r} (lowercase letters and digits, "
            f"hyphen-joined, at most {NAME_MAX_LENGTH} characters)"
        )
    return name


def main_checkout(start: Path | None = None) -> Path:
    """The workspace's main checkout, from inside it or any of its worktrees.

    ``git rev-parse --git-common-dir`` answers ``<main>/.git`` from a linked
    worktree and ``.git`` from the main checkout itself; either way its
    parent is the main checkout.
    """
    cwd = start if start is not None else Path.cwd()
    common_dir = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return Path(common_dir).parent


def threads_dir(workspace: Path) -> Path:
    return workspace / THREADS_DIR


def sourcesets_dir(workspace: Path) -> Path:
    return workspace / SOURCESETS_DIR
