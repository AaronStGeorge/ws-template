"""Launch a workspace thread: a Codex thread in its own worktree of this workspace.

The design record is README.md beside this file. The package is one launcher
with three entries: ``workspace-thread new`` (cli.py) for a human at a
terminal, the ``create_workspace_thread`` MCP tool (mcp.py) for an agent, and
the functions in launch.py for an Imp. All three run the same steps, from
naming the workspace thread to creating its Codex thread on the daemon; only
the ending differs.
"""

from .launch import (
    UNATTENDED_SETTINGS,
    WorkspaceThread,
    TurnResult,
    attach,
    opening_prompt,
    prepare,
    run_turn,
    start_turn,
)
from .workspace import main_checkout

__all__ = [
    "UNATTENDED_SETTINGS",
    "WorkspaceThread",
    "TurnResult",
    "attach",
    "main_checkout",
    "opening_prompt",
    "prepare",
    "run_turn",
    "start_turn",
]
