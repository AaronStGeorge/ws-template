"""Sourcesets: the manifests that fill a checkout's ``sources/``.

A sourceset is ``sourcesets/<name>/sourceset.json`` naming the repositories
to clone, plus an optional ``assets/`` directory of files that belong beside
the clones (usually a ``build.py``)::

    {"repos": [{"url": "git@github.com:org/repo.git", "ref": "branch-tag-or-sha"}]}

``ref`` is optional; without it the clone stays on the remote's default
branch. Assets are symlinked rather than copied so that edits made through
``sources/`` land in the tracked ``assets/`` directory.

Loading always targets an empty ``sources/``: a workspace thread's worktree starts
with one, and the by-hand door (``scripts/ws_load.py``) empties the main
checkout's before calling in here. Clones are full clones every time; a
cache is a later addition that would live inside ``clone`` alone.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .workspace import SOURCES_DIR, sourcesets_dir


@dataclass(frozen=True)
class Sourceset:
    name: str
    repos: list[dict[str, str]]
    assets: Path | None


def read_sourceset(workspace: Path, name: str) -> Sourceset:
    """The sourceset ``name`` as checked into ``workspace``.

    Raises FileNotFoundError when there is no such sourceset; the manifest
    is repository content, so its shape is trusted once the file exists.
    """
    directory = sourcesets_dir(workspace) / name
    manifest = directory / "sourceset.json"
    if not manifest.is_file():
        raise FileNotFoundError(
            f"no sourceset {name!r}: {manifest} does not exist"
        )
    repos = json.loads(manifest.read_text(encoding="utf-8"))["repos"]
    assets = directory / "assets"
    return Sourceset(name, repos, assets if assets.is_dir() else None)


def clone_destination(sources: Path, url: str) -> Path:
    return sources / url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")


def clone(repo: dict[str, str], sources: Path, log) -> Path:
    """Clone one sourceset repo into ``sources``, child output to ``log``."""
    destination = clone_destination(sources, repo["url"])
    subprocess.run(
        ["git", "clone", repo["url"], str(destination)],
        check=True,
        stdout=log,
        stderr=log,
    )
    if "ref" in repo:
        # A remote branch name becomes a local tracking branch; a tag or SHA
        # detaches. Both are git's own defaults.
        subprocess.run(
            ["git", "-C", str(destination), "checkout", repo["ref"]],
            check=True,
            stdout=log,
            stderr=log,
        )
    return destination


def load_sourceset(sourceset: Sourceset, checkout: Path, log) -> Path:
    """Fill ``<checkout>/sources/`` from ``sourceset``; returns that directory.

    ``log`` is where git's progress goes: an Imp passes its stderr so the
    clones show in the Run log.
    """
    sources = checkout / SOURCES_DIR
    sources.mkdir(exist_ok=True)
    for repo in sourceset.repos:
        clone(repo, sources, log)
    if sourceset.assets is not None:
        for asset in sorted(sourceset.assets.iterdir()):
            # Relative, so the links survive the repository being moved or
            # mounted at another path.
            (sources / asset.name).symlink_to(os.path.relpath(asset, sources))
    return sources
