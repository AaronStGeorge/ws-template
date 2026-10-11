#!/usr/bin/env python3
"""Load a sourceset into the main checkout's ``sources/``, by hand.

    python scripts/ws_load.py llama-cpp

This is the by-hand door for the main checkout's ``sources/``, kept for
review and human-in-the-loop work outside any thread. Thread worktrees are
filled by threadlib (``lib/python/threadlib/README.md``), which also owns the
loading itself; this script only empties ``sources/`` first and refuses when
anything there exists only locally, because here, and only here, a load can
destroy work.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from threadlib import load_sourceset, read_sourceset

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES = REPO_ROOT / "sources"


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", help="Directory name under sourcesets/.")
    args = parser.parse_args()

    try:
        sourceset = read_sourceset(REPO_ROOT, args.name)
    except FileNotFoundError as error:
        print(f"ws_load: {error}", file=sys.stderr)
        return 1

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

    load_sourceset(sourceset, REPO_ROOT, sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
