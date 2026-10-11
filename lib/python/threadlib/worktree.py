"""A thread's worktree: a detached checkout of the workspace at ``.threads/<name>``.

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


def create(workspace: Path, name: str, log) -> Path:
    """Add the worktree for thread ``name`` and return its path.

    A worktree that already exists is an error rather than a reuse: a thread
    name is also a Codex thread name, and two threads sharing a checkout
    would share each other's half-done work.
    """
    path = threads_dir(workspace) / name
    if path.exists():
        raise FileExistsError(f"thread worktree already exists: {path}")
    path.parent.mkdir(exist_ok=True)
    subprocess.run(
        ["git", "-C", str(workspace), "worktree", "add", "--detach", str(path), "HEAD"],
        check=True,
        stdout=log,
        stderr=log,
    )
    return path
