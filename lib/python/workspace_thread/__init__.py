"""Launch a workspace thread: a Codex thread in its own worktree of this workspace.

The design record is README.md beside this file. The package is one launcher
with three doors: ``workspace-thread new`` (cli.py) for a human or an agent at
a shell, and the functions in launch.py for an Imp. All three run the same
steps, from naming the workspace thread to creating its Codex thread on the
daemon; only the last step differs.
"""

from .launch import (
    IMP_DOOR_SETTINGS,
    WorkspaceThread,
    TurnResult,
    attach,
    prepare,
    run_turn,
    start_turn,
)
from .sourceset import Sourceset, load_sourceset, read_sourceset
from .workspace import main_checkout

__all__ = [
    "IMP_DOOR_SETTINGS",
    "Sourceset",
    "WorkspaceThread",
    "TurnResult",
    "attach",
    "load_sourceset",
    "main_checkout",
    "prepare",
    "read_sourceset",
    "run_turn",
    "start_turn",
]
