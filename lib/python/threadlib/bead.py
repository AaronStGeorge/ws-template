"""What the launcher reads from a bead: its sourceset label.

A bead names the sourceset its thread needs with the label
``sourceset:<name>``, set when the bead is created. That is the only thing
the launcher takes from a bead; the thread's opening prompt names the bead
and the rest of the bead process is deliberately unspecified (README.md).

``br`` is run from the main checkout so it finds the workspace's store
without any environment; from a thread's worktree it would find the same
store, but the launcher runs before the worktree exists.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

LABEL_PREFIX = "sourceset:"


def sourceset_label(workspace: Path, bead_id: str) -> str:
    """The sourceset name a bead's label carries.

    Raises LookupError when the bead has no ``sourceset:`` label, or more
    than one; a bead ``br`` does not know fails with its own message
    through CalledProcessError.
    """
    shown = json.loads(
        subprocess.run(
            ["br", "show", bead_id, "--json"],
            cwd=workspace,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout
    )
    # `br show --json` answers a list, one entry per id asked for.
    labels = shown[0].get("labels") or []
    names = [
        label.removeprefix(LABEL_PREFIX)
        for label in labels
        if label.startswith(LABEL_PREFIX)
    ]
    has_one_sourceset = len(names) == 1
    if not has_one_sourceset:
        raise LookupError(
            f"bead {bead_id} needs exactly one {LABEL_PREFIX}<name> label, "
            f"found {names or 'none'}"
        )
    return names[0]
