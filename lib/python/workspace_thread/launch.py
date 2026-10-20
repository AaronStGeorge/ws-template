"""The launcher: the steps every entry runs, and the three endings.

``prepare`` is steps 1 to 5 of README.md: name the workspace thread, cut its
worktree, load its sourceset, create and name its Codex thread on the
daemon. The entry then picks an ending: ``attach`` replaces the process
with an interactive ``codex`` on the thread; ``start_turn`` (detach) sends
the opening prompt and returns; ``run_turn`` (wait) sends it and follows the
turn into a log until it completes. ``opening_prompt`` is the one check the
two turn-starting endings share: there must be something to send.

A bead enters only as a name and a label: ``prepare`` takes the sourceset
name already resolved, so the bead lookup stays in bead.py and a caller
without a bead passes a name and a sourceset of its own.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sourceset

from . import codex, worktree
from .codex import Client, TurnResult
from .workspace import main_checkout, slugify

# The settings an Imp's thread runs under: no approvals, no sandbox, as the
# imps ran under `codex exec --dangerously-bypass-approvals-and-sandbox`. No
# one will answer an approval prompt in a Run, so a thread that paused for
# one would hang, and the imp design record's constraints ask for no
# hardening. The executable and the MCP tool pass nothing and get the user's
# own Codex defaults.
UNATTENDED_SETTINGS: dict[str, Any] = {
    "approvalPolicy": "never",
    "sandbox": "danger-full-access",
}


@dataclass(frozen=True)
class WorkspaceThread:
    name: str  # the title, as Codex shows it
    slug: str  # the worktree directory name under .workspace-threads/
    worktree: Path
    thread_id: str  # the Codex thread id


def prepare(
    name: str,
    sourceset_name: str | None,
    settings: dict[str, Any] | None = None,
    workspace: Path | None = None,
    log=sys.stderr,
    sourcesets_dir: Path | None = None,
) -> tuple[WorkspaceThread, Client]:
    """Steps 1 to 5; returns the workspace thread and the daemon connection that made it.

    ``name`` is the workspace thread's title, free text that Codex shows as
    is; its slug names the worktree. For a bead id or an imp Run Id the two
    are equal. ``sourceset_name`` None leaves ``sources/`` empty, for
    workspace threads that work on the workspace itself.
    ``sourcesets_dir`` is where the sourceset is read from, the workspace's
    ``sourcesets/`` unless the entry was told otherwise. ``log`` receives git
    and daemon-start output. The connection is returned so an ending that
    starts a turn can send it; attach closes it unused.
    """
    # The title's provenance boundary: strip once for the Codex name, and
    # slugify once for the worktree, before anything is cut.
    name = name.strip()
    slug = slugify(name)
    ws = workspace if workspace is not None else main_checkout()
    if sourcesets_dir is None:
        sourcesets_dir = ws / sourceset.SOURCESETS_DIR
    chosen = sourceset.read_sourceset(sourcesets_dir, sourceset_name) if sourceset_name else None

    print(f"workspace thread {name}: cutting worktree", file=log)
    path = worktree.create(ws, name, slug, log)
    if chosen is not None:
        print(f"workspace thread {name}: loading sourceset {chosen.name}", file=log)
        sourceset.load_sourceset(chosen, path, log)

    codex.ensure_daemon(log)
    client = Client.connect()
    document = client.start_thread(path, settings or {})
    client.set_thread_name(document["id"], name)
    # Written to disk before any ending begins, so `codex resume <id>` works
    # on a Codex thread whose first turn has not happened yet (attach's).
    client.persist_thread(document["id"])
    print(f"workspace thread {name}: {document['id']} in {path}", file=log)
    return WorkspaceThread(name, slug, path, document["id"]), client


def attach(thread: WorkspaceThread, prompt: str | None = None) -> None:
    """The attach ending: become an interactive codex on the workspace thread.

    ``prompt``, when given, is typed for the user as the first turn, so a
    workspace thread opened by a human starts the same way a detached one
    would.
    """
    os.chdir(thread.worktree)
    argv = ["codex", "resume", thread.thread_id]
    if prompt is not None:
        argv.append(prompt)
    os.execvp("codex", argv)


def opening_prompt(prompt: str | None) -> str:
    """The prompt an ending that starts a turn sends, else ValueError.

    Attach never calls this: with nothing to send, the human types the
    first turn. A bead's assignment always has a prompt; a named one has
    it only if the caller gave it.
    """
    if not prompt:
        raise ValueError("named launches require a nonempty prompt")
    return prompt


def start_turn(
    client: Client, thread: WorkspaceThread, prompt: str, output_schema: dict[str, Any] | None = None
) -> str:
    """The detach ending: send the opening prompt, return the turn id."""
    return client.start_turn(thread.thread_id, prompt, output_schema)


def run_turn(
    client: Client,
    thread: WorkspaceThread,
    prompt: str,
    output_schema: dict[str, Any] | None = None,
    log=sys.stderr,
) -> TurnResult:
    """The wait ending: send the prompt and follow the turn to its end."""
    turn_id = start_turn(client, thread, prompt, output_schema)
    return client.wait_for_turn(thread.thread_id, turn_id, log)
