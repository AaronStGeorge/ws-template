"""Sourcesets: the manifests that say which sources a piece of work needs.

A sourceset is ``<name>/sourceset.json`` under a sourcesets directory, the
workspace's ``sourcesets/`` unless a caller says otherwise, naming the
repositories to clone into a checkout's ``sources/``, plus an optional
``assets/`` directory of files that belong beside the clones::

    {"repos": [{"url": "git@github.com:org/repo.git", "ref": "branch-tag-or-sha"}]}

``ref`` is optional; without it the clone stays on the remote's default
branch. Assets are symlinked rather than copied so that edits made through
``sources/`` land in the tracked ``assets/`` directory; what an asset is
for is its sourceset's business, not this package's.

Two callers load one: the workspace thread launcher fills a new worktree's
``sources/`` (lib/python/workspace_thread), and ``scripts/ws_load.py``
fills the main checkout's by hand. Loading always targets an empty
``sources/``: a worktree starts with one, and the by-hand door empties the
main checkout's before calling in here. Clones are full clones every time;
a cache is a later addition that would live inside ``clone`` alone. The
design record is README.md beside this file.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# The default sourcesets directory, relative to the workspace. Every caller
# can be told another; the loader itself only ever takes a directory.
SOURCESETS_DIR = "sourcesets"
SOURCES_DIR = "sources"

# A sourceset name selects a directory under sourcesets/ and arrives from
# outside: a bead's label, a flag, a tool parameter. Lowercase letters and
# digits, hyphen-joined, so it can name nothing but one such directory.
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

__all__ = ["SOURCESETS_DIR", "SOURCES_DIR", "Sourceset", "load_sourceset", "read_sourceset"]


@dataclass(frozen=True)
class Sourceset:
    name: str
    repos: list[dict[str, str]]
    assets: Path | None


def validate_name(name: str) -> str:
    """Return ``name`` if it is a sourceset name, else raise ValueError."""
    if NAME.fullmatch(name) is None:
        raise ValueError(
            f"not a sourceset name: {name!r} (lowercase letters and digits, hyphen-joined)"
        )
    return name


def read_sourceset(sourcesets_dir: Path, name: str) -> Sourceset:
    """The sourceset ``name`` under ``sourcesets_dir``.

    The name is checked here, the one place every caller passes through:
    ValueError when it is not a sourceset name, FileNotFoundError when
    there is no such sourceset. The manifest is repository content, so its
    shape is trusted once the file exists.
    """
    directory = sourcesets_dir / validate_name(name)
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
