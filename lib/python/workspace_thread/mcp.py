"""One MCP launch tool, backed by the same launcher as ``workspace-thread new``.

The server is bound to a main workspace at startup, independent of which
Codex thread calls it. Only creation belongs here: Codex's built-in thread tools
own reading, messaging, and waiting. All launcher progress goes to stderr
so stdout remains the MCP stdio transport.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing_extensions import TypedDict

from mcp.server.fastmcp import FastMCP

from . import assignment, launch
from .workspace import main_checkout


class CreatedWorkspaceThread(TypedDict):
    thread_id: str
    turn_id: str
    name: str
    worktree: str


def create(
    workspace: Path,
    *,
    bead_id: str | None = None,
    name: str | None = None,
    sourceset: str | None = None,
    prompt: str | None = None,
    parent_thread_id: str | None = None,
) -> CreatedWorkspaceThread:
    task = assignment.resolve(
        workspace, bead_id=bead_id, name=name, sourceset=sourceset, prompt=prompt
    )
    opening = launch.opening_prompt(task.prompt)
    if parent_thread_id is not None:
        opening += (
            f"\n\nYour orchestrator is Codex thread {parent_thread_id}. "
            "Use the built-in send_message_to_thread tool to report progress, "
            "blockers, and completion to that thread. Include your bead/name "
            "and concrete result or artifact references. Do not close your "
            "bead yourself; the orchestrator reviews and closes it."
        )
        # The app attaches its native thread tools when it starts a turn on
        # an idle daemon-created thread. Keep this first turn idle-ready so
        # an orchestrator's first native message can perform that handoff.
        opening = (
            "Workspace thread startup handshake. The assignment below is for "
            "your NEXT turn, after the orchestrator attaches native thread tools. "
            "For this turn, do not run commands or begin the assignment. Reply "
            "exactly WORKSPACE_THREAD_READY and finish. The orchestrator will "
            "wait for this turn to finish and send a native follow-up to begin.\n\n"
            f"Assignment for the next turn:\n{opening}"
        )
    thread, client = launch.prepare(task.name, task.sourceset, workspace=workspace)
    try:
        turn_id = launch.start_turn(client, thread, opening)
    except Exception as error:
        raise RuntimeError(
            f"workspace thread {thread.thread_id} was created at {thread.worktree}, "
            f"but its opening turn failed: {error}"
        ) from error
    finally:
        client.close()
    return CreatedWorkspaceThread(
        thread_id=thread.thread_id,
        turn_id=turn_id,
        name=thread.name,
        worktree=str(thread.worktree),
    )


def make_server(workspace: Path) -> FastMCP:
    server = FastMCP("workspace_threads")

    @server.tool()
    def create_workspace_thread(
        bead_id: str | None = None,
        name: str | None = None,
        sourceset: str | None = None,
        prompt: str | None = None,
        parent_thread_id: str | None = None,
    ) -> CreatedWorkspaceThread:
        """Start authorized work in an isolated workspace thread and return its IDs.

        Give bead_id OR name. A bead supplies its sourceset label and opening
        assignment; a named launch requires prompt and may specify sourceset.
        name is a free-text title; its slug names the worktree, and an
        existing slug is refused.
        Pass the calling thread's ID as parent_thread_id for replies. Uses
        ordinary Codex defaults. With a parent, the first turn is a short
        startup handshake: wait for it to finish, then use the built-in
        send_message_to_thread to begin the assignment with native tools.
        Use built-in thread tools to read, message, and wait afterward.
        An existing worktree is refused, never replaced or reused.
        """
        return create(
            workspace,
            bead_id=bead_id,
            name=name,
            sourceset=sourceset,
            prompt=prompt,
            parent_thread_id=parent_thread_id,
        )

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    make_server(main_checkout(args.workspace)).run(transport="stdio")


if __name__ == "__main__":
    main()
