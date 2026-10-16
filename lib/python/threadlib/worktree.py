"""A thread's worktree: a detached checkout of the workspace at ``.threads/<slug>``.

The worktree is cut from the main checkout's HEAD and takes no branch: the
workspace itself is not what a thread works on, its ``sources/`` are, and
those are the sourceset's clones with branches of their own. ``.threads/``
is gitignored, like ``.imp/``, so a thread's worktree never shows up as
untracked in the main checkout, and ``br`` run inside it finds the main
checkout's ``.beads/`` on its own (README.md, step 4).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .workspace import threads_dir


def create(workspace: Path, name: str, slug: str, log) -> Path:
    """Add the worktree for the thread titled ``name`` at ``slug``; return its path.

    A worktree that already exists is an error rather than a reuse: two
    titles with the same slug would share a checkout, and with it each
    other's half-done work. The message names the slug because that is what
    collided, not the title the caller typed.
    """
    path = threads_dir(workspace) / slug
    if path.exists():
        raise FileExistsError(
            f"title {name!r} slugifies to {slug!r}, and that slug already has a "
            f"worktree: {path}; remove it with `git worktree remove {path}` to "
            f"use the slug again"
        )
    path.parent.mkdir(exist_ok=True)
    subprocess.run(
        ["git", "-C", str(workspace), "worktree", "add", "--detach", str(path), "HEAD"],
        check=True,
        stdout=log,
        stderr=log,
    )
    return path
