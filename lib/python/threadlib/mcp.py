"""One MCP launch tool, backed by the same launcher as ``thread new``.

The server is bound to a main workspace at startup, independent of which
thread calls it. Only creation belongs here: Codex's built-in thread tools
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


class CreatedThread(TypedDict):
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
) -> CreatedThread:
    task = assignment.resolve(
        workspace, bead_id=bead_id, name=name, sourceset=sourceset, prompt=prompt
    )
    if not task.prompt:
        raise ValueError("named launches require a nonempty prompt")
    opening = task.prompt
    if parent_thread_id is not None:
        opening += (
            f"\n\nYour orchestrator is Codex thread {parent_thread_id}. "
            "Use the built-in send_message_to_thread tool to report progress, "
            "blockers, and completion to that thread. Include your bead/name "
            "and concrete result or artifact references. Do not close your "
            "bead yourself; the orchestrator reviews and closes it."
        )
    thread, client = launch.prepare(task.name, task.sourceset, workspace=workspace)
    try:
        turn_id = launch.start_turn(client, thread, opening)
    except Exception as error:
        raise RuntimeError(
            f"thread {thread.thread_id} was created at {thread.worktree}, "
            f"but its opening turn failed: {error}"
        ) from error
    finally:
        client.close()
    return CreatedThread(
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
    ) -> CreatedThread:
        """Start authorized work in an isolated workspace thread and return its IDs.

        Give bead_id OR name. A bead supplies its sourceset label and opening
        assignment; a named launch requires prompt and may specify sourceset.
        Pass the calling thread's ID as parent_thread_id for replies. Uses
        ordinary Codex defaults. After creation, use Codex's built-in thread
        tools to read, message, and wait; creation does not wait for completion.
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
