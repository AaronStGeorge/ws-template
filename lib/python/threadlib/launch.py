"""The launcher: the steps every door runs, and the three endings.

``prepare`` is steps 1 to 5 of README.md: name the thread, cut its worktree,
load its sourceset, create and name the thread on Codex's daemon. The door
then picks an ending: ``attach`` (human) replaces the process with an
interactive ``codex`` on the thread; ``start_turn`` (agent) sends the
opening prompt and returns; ``run_turn`` (Imp) sends it and follows the turn
into a log until it completes.

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

from . import codex, sourceset as sourcesets, worktree
from .codex import Client, TurnResult
from .workspace import main_checkout, slugify

# The Imp door's thread settings: no approvals, no sandbox, as the imps ran
# under `codex exec --dangerously-bypass-approvals-and-sandbox`. A thread
# that paused for approval would hang with no one watching, and the imp
# design record's constraints ask for no hardening. The human door passes
# nothing and gets the user's own Codex defaults.
IMP_DOOR_SETTINGS: dict[str, Any] = {
    "approvalPolicy": "never",
    "sandbox": "danger-full-access",
}


@dataclass(frozen=True)
class Thread:
    name: str  # the title, as Codex shows it
    slug: str  # the worktree directory name under .threads/
    worktree: Path
    thread_id: str


def prepare(
    name: str,
    sourceset_name: str | None,
    settings: dict[str, Any] | None = None,
    workspace: Path | None = None,
    log=sys.stderr,
) -> tuple[Thread, Client]:
    """Steps 1 to 5; returns the thread and the daemon connection that made it.

    ``name`` is the thread's title, free text that Codex shows as is; its
    slug names the worktree. For a bead id or an imp Run Id the two are
    equal. ``sourceset_name`` None leaves ``sources/`` empty, for threads
    that work on the workspace itself (the overseer's checks). ``log``
    receives git and daemon-start output. The connection is returned so a
    door can send the opening turn on it; the human door closes it unused.
    """
    # The title's provenance boundary: strip once for the Codex name, and
    # slugify once for the worktree, before anything is cut.
    name = name.strip()
    slug = slugify(name)
    ws = workspace if workspace is not None else main_checkout()
    chosen = sourcesets.read_sourceset(ws, sourceset_name) if sourceset_name else None

    print(f"thread {name}: cutting worktree", file=log)
    path = worktree.create(ws, name, slug, log)
    if chosen is not None:
        print(f"thread {name}: loading sourceset {chosen.name}", file=log)
        sourcesets.load_sourceset(chosen, path, log)

    codex.ensure_daemon(log)
    client = Client.connect()
    document = client.start_thread(path, settings or {})
    client.set_thread_name(document["id"], name)
    print(f"thread {name}: {document['id']} in {path}", file=log)
    return Thread(name, slug, path, document["id"]), client


def attach(thread: Thread, prompt: str | None = None) -> None:
    """The human door's ending: become an interactive codex on the thread.

    ``prompt``, when given, is typed for the user as the first turn, so a
    bead thread opened by a human starts the same way an agent's would.
    """
    os.chdir(thread.worktree)
    argv = ["codex", "resume", thread.thread_id]
    if prompt is not None:
        argv.append(prompt)
    os.execvp("codex", argv)


def start_turn(
    client: Client, thread: Thread, prompt: str, output_schema: dict[str, Any] | None = None
) -> str:
    """The agent door's ending: send the opening prompt, return the turn id."""
    return client.start_turn(thread.thread_id, prompt, output_schema)


def run_turn(
    client: Client,
    thread: Thread,
    prompt: str,
    output_schema: dict[str, Any] | None = None,
    log=sys.stderr,
) -> TurnResult:
    """The Imp door's ending: send the prompt and follow the turn to its end."""
    turn_id = start_turn(client, thread, prompt, output_schema)
    return client.wait_for_turn(thread.thread_id, turn_id, log)
