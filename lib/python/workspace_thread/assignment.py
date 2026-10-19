"""Resolve the assignment shared by the CLI and MCP launch doors.

Beads supply a name, sourceset label, and opening instruction. Named work
supplies those directly; its name is a free-text title whose shaping into a
worktree slug belongs to launch.prepare. This boundary owns their mutually
exclusive inputs; launch.py continues to own creating the worktree and Codex
thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import bead
from .workspace import validate_name


@dataclass(frozen=True)
class Assignment:
    name: str
    sourceset: str | None
    prompt: str | None


def resolve(
    workspace: Path,
    *,
    bead_id: str | None = None,
    name: str | None = None,
    sourceset: str | None = None,
    prompt: str | None = None,
) -> Assignment:
    has_bead = bead_id is not None
    has_name = name is not None
    if has_bead == has_name:
        raise ValueError("give a bead id or name, not both and not neither")
    has_sourceset = sourceset is not None
    overrides_bead_sourceset = has_bead and has_sourceset
    if overrides_bead_sourceset:
        raise ValueError("sourceset comes from the bead's label when a bead is given")

    if bead_id is not None:
        resolved_name = validate_name(bead_id)
        sourceset = bead.sourceset_label(workspace, resolved_name)
        opening = f"Work on bead {resolved_name}. Read it with `br show {resolved_name} --json` first."
        prompt = opening if prompt is None else f"{opening}\n\n{prompt}"
    else:
        resolved_name = name
    if sourceset is not None:
        validate_name(sourceset)
    return Assignment(resolved_name, sourceset, prompt)
