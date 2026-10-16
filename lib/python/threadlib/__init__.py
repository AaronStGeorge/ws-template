"""Launch a Codex thread in its own worktree of this workspace.

The design record is README.md beside this file. The package is one launcher
with three doors: ``thread new`` (cli.py) for a human or an agent at a shell,
and the functions in launch.py for an Imp. All three run the same steps, from
naming the thread to creating it on Codex's daemon; only the last step differs.
"""

from .launch import (
    IMP_DOOR_SETTINGS,
    Thread,
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
    "Thread",
    "TurnResult",
    "attach",
    "load_sourceset",
    "main_checkout",
    "prepare",
    "read_sourceset",
    "run_turn",
    "start_turn",
]
