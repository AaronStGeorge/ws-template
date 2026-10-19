"""Where things are in a workspace, and how a workspace thread's title becomes its slug.

A workspace is a clone of this repository. Its workspace threads are worktrees under
``.workspace-threads/`` in the main checkout, and its sourcesets are checked in under
``sourcesets/``, so both are reachable from any worktree by first finding the
main checkout through git's common dir.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# A workspace thread's title is free text for Codex; its slug is the worktree directory
# name, and that is where this shape comes from. A bead id and an imp Run Id
# are already slugs and are their own; the bead id is still checked strictly
# where it is read, because it goes verbatim into `br show`.
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NAME_MAX_LENGTH = 128

# Only ASCII letters and digits survive into a slug, collapsed first and
# lowercased after, so "no slug" means exactly "no ASCII letter or digit"
# in the title (str.lower can mint ASCII from some non-ASCII letters).
NON_SLUG_RUN = re.compile(r"[^A-Za-z0-9]+")

WORKSPACE_THREADS_DIR = ".workspace-threads"
SOURCESETS_DIR = "sourcesets"
SOURCES_DIR = "sources"


def validate_name(name: str) -> str:
    """Return ``name`` if it is already a slug, else raise ValueError.

    Called where a slug-shaped id enters from outside: a bead id, which goes
    verbatim into ``br show``, and a sourceset name, which selects a
    checked-in directory.
    """
    is_slug = NAME.fullmatch(name) is not None
    fits = len(name) <= NAME_MAX_LENGTH
    if not (is_slug and fits):
        raise ValueError(
            f"not a workspace thread name: {name!r} (lowercase letters and digits, "
            f"hyphen-joined, at most {NAME_MAX_LENGTH} characters)"
        )
    return name


def slugify(title: str) -> str:
    """The worktree directory name for a workspace thread title, else ValueError.

    Called once, in launch.prepare, the funnel every door goes through. A
    bead id or an imp Run Id is already this shape and comes back unchanged,
    which is what lets an Imp find its worktree at ``.workspace-threads/<run-id>``.
    An empty slug is refused here because ``.workspace-threads/`` joined with "" is
    ``.workspace-threads`` itself, which is not a place to cut a worktree.
    """
    collapsed = NON_SLUG_RUN.sub("-", title).lower().strip("-")
    # Truncation can leave the cut edge on a hyphen; strip it again.
    slug = collapsed[:NAME_MAX_LENGTH].rstrip("-")
    if not slug:
        raise ValueError(
            f"not a workspace thread title: {title!r} (needs at least one ASCII letter or digit)"
        )
    return slug


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


def workspace_threads_dir(workspace: Path) -> Path:
    return workspace / WORKSPACE_THREADS_DIR


def sourcesets_dir(workspace: Path) -> Path:
    return workspace / SOURCESETS_DIR
