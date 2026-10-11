"""``thread``: the human and agent doors onto a new thread.

::

    thread new <bead-id>                          # named after the bead; sourceset from its label
    thread new --name <slug> [--sourceset <s>]    # no bead
    thread new ... --prompt TEXT                  # agent door: start the first turn and return

Without ``--prompt`` the command ends as an interactive ``codex`` on the new
thread, inside its worktree. With it, the thread's first turn starts with
that prompt and the command prints the thread's name and returns; follow up
with ``codex queue --thread <name>`` or by opening the thread. The Imp door
is the library (launch.py), not this command.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from . import bead, launch
from .workspace import main_checkout, validate_name

BEAD_PROMPT = "Work on bead {bead_id}. Read it with `br show {bead_id} --json` first."


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="thread", description=__doc__.splitlines()[0])
    verbs = parser.add_subparsers(dest="verb", required=True)
    new = verbs.add_parser("new", help="launch a new thread in its own worktree")
    new.add_argument("bead", nargs="?", help="bead id; names the thread and picks its sourceset")
    new.add_argument("--name", help="thread name when there is no bead")
    new.add_argument("--sourceset", help="sourceset to load when there is no bead")
    new.add_argument(
        "--prompt",
        help="agent door: start the first turn with this prompt and return",
    )
    args = parser.parse_args(argv)

    has_bead = args.bead is not None
    has_name = args.name is not None
    if has_bead == has_name:
        new.error("give a bead id or --name, not both and not neither")
    if has_bead and args.sourceset is not None:
        new.error("--sourceset comes from the bead's label when a bead is given")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    workspace = main_checkout()
    try:
        if args.bead is not None:
            name = validate_name(args.bead)
            sourceset_name = bead.sourceset_label(workspace, name)
            prompt = BEAD_PROMPT.format(bead_id=name)
        else:
            name = validate_name(args.name)
            sourceset_name = args.sourceset
            prompt = None
        if args.prompt is not None:
            prompt = args.prompt if prompt is None else f"{prompt}\n\n{args.prompt}"

        thread, client = launch.prepare(name, sourceset_name, workspace=workspace)
    except (ValueError, LookupError, FileNotFoundError, FileExistsError) as error:
        print(f"thread new: {error}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f"thread new: {' '.join(map(str, error.cmd))} exited {error.returncode}", file=sys.stderr)
        return 1

    is_agent_door = args.prompt is not None
    if is_agent_door:
        launch.start_turn(client, thread, prompt)
        client.close()
        print(thread.name)
        return 0

    client.close()
    launch.attach(thread, prompt)
    return 0  # unreachable: attach replaces the process


if __name__ == "__main__":
    raise SystemExit(main())
