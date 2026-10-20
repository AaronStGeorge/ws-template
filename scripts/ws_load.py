#!/usr/bin/env python3
"""Load a sourceset into the main checkout's ``sources/``, by hand.

    python scripts/ws_load.py llama-cpp [--sourcesets-dir DIR]

This is the by-hand door for the main checkout's ``sources/``, kept for
review and human-in-the-loop work outside any workspace thread. The
loading itself is the sourceset package's
(``lib/python/sourceset/README.md``); this script only empties ``sources/``
first and refuses when anything there exists only locally, because here,
and only here, a load can destroy work.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from sourceset import SOURCESETS_DIR, SOURCES_DIR, load_sourceset, read_sourceset

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES = REPO_ROOT / SOURCES_DIR


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
    parser.add_argument("name", help="Directory name under the sourcesets directory.")
    parser.add_argument(
        "--sourcesets-dir",
        type=Path,
        help="directory to read the sourceset from; default: the workspace's sourcesets/",
    )
    args = parser.parse_args()
    given_a_directory = args.sourcesets_dir is not None
    sourcesets_dir = args.sourcesets_dir if given_a_directory else REPO_ROOT / SOURCESETS_DIR

    try:
        sourceset = read_sourceset(sourcesets_dir, args.name)
    except (ValueError, FileNotFoundError) as error:
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
