"""``workspace-thread``: the human and agent doors onto a new workspace thread.

::

    workspace-thread new <bead-id>                          # named after the bead; sourceset from its label
    workspace-thread new --name <title> [--sourceset <s>]   # no bead
    workspace-thread new ... --prompt TEXT                  # agent door: start the first turn and return

Without ``--prompt`` the command ends as an interactive ``codex`` on the new
workspace thread, inside its worktree. With it, the thread's first turn
starts with that prompt and the command prints its Codex thread id and
returns; follow up with ``codex queue --thread <id>`` or by opening the
thread. The Imp door
is the library (launch.py), not this command.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from . import assignment, launch
from .workspace import main_checkout


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="workspace-thread", description=__doc__.splitlines()[0])
    verbs = parser.add_subparsers(dest="verb", required=True)
    new = verbs.add_parser("new", help="launch a new workspace thread in its own worktree")
    new.add_argument("bead", nargs="?", help="bead id; names the workspace thread and picks its sourceset")
    new.add_argument(
        "--name", help="title when there is no bead; its slug names the worktree"
    )
    new.add_argument("--sourceset", help="sourceset to load when there is no bead")
    new.add_argument(
        "--prompt",
        help="agent door: start the first turn with this prompt and return",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    workspace = main_checkout()
    try:
        task = assignment.resolve(
            workspace, bead_id=args.bead, name=args.name,
            sourceset=args.sourceset, prompt=args.prompt,
        )
        thread, client = launch.prepare(task.name, task.sourceset, workspace=workspace)
    except (ValueError, LookupError, FileNotFoundError, FileExistsError) as error:
        print(f"workspace-thread new: {error}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f"workspace-thread new: {' '.join(map(str, error.cmd))} exited {error.returncode}", file=sys.stderr)
        return 1

    is_agent_door = args.prompt is not None
    if is_agent_door:
        try:
            launch.start_turn(client, thread, task.prompt)
        finally:
            client.close()
        # The id, not the title: a title can be reused once its worktree is
        # removed, and the id is what `codex queue --thread` and `codex
        # resume` both take without ambiguity.
        print(thread.thread_id)
        return 0

    client.close()
    launch.attach(thread, task.prompt)
    return 0  # unreachable: attach replaces the process


if __name__ == "__main__":
    raise SystemExit(main())
