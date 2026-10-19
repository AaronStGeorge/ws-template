"""``workspace-thread``: the human's entry onto a new workspace thread.

::

    workspace-thread new <bead-id> [--prompt TEXT]                        # named after the bead; sourceset from its label
    workspace-thread new --name <title> [--sourceset <s>] [--prompt TEXT]  # no bead
    workspace-thread new ... --detach                                     # start the first turn, print the Codex thread id, return

Either form names an assignment; ``--detach`` picks the ending. Without it
the command attaches: it ends as an interactive ``codex`` on the new
workspace thread, inside its worktree, with the opening prompt typed as the
first turn when there is one. With it the thread's first turn starts with
that prompt, the Codex thread id is printed, and the command returns;
follow up with ``codex queue --thread <id>`` or by opening the thread. The
other entries are the MCP tool (mcp.py) for an agent and the library
(launch.py) for an Imp.
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
        help="opening prompt for the first turn; appended to a bead's own",
    )
    new.add_argument(
        "--detach",
        action="store_true",
        help="start the first turn with the opening prompt, print the Codex thread id and return; "
        "without it, attach as an interactive codex",
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
        # Detach sends the opening prompt as the first turn, so there must be
        # one; checked here so a refused detach leaves no worktree behind.
        opening = launch.opening_prompt(task.prompt) if args.detach else task.prompt
        thread, client = launch.prepare(task.name, task.sourceset, workspace=workspace)
    except (ValueError, LookupError, FileNotFoundError, FileExistsError) as error:
        print(f"workspace-thread new: {error}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f"workspace-thread new: {' '.join(map(str, error.cmd))} exited {error.returncode}", file=sys.stderr)
        return 1

    if args.detach:
        try:
            launch.start_turn(client, thread, opening)
        except Exception as error:
            print(
                f"workspace-thread new: workspace thread {thread.thread_id} was created at "
                f"{thread.worktree}, but its opening turn failed: {error}",
                file=sys.stderr,
            )
            return 1
        finally:
            client.close()
        # The id, not the title: a title can be reused once its worktree is
        # removed, and the id is what `codex queue --thread` and `codex
        # resume` both take without ambiguity.
        print(thread.thread_id)
        return 0

    client.close()
    launch.attach(thread, opening)
    return 0  # unreachable: attach replaces the process


if __name__ == "__main__":
    raise SystemExit(main())
