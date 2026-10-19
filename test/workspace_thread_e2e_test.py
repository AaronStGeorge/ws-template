#!/usr/bin/env python3
"""End-to-end checks of workspace_thread's three doors against the real Codex daemon.

THIS TEST IS VERY EXPENSIVE: RUN IT ONLY AS A FINAL VERIFICATION. Two of the
three doors are defined by starting a turn, and a turn is a real model call
billed to the user's Codex account. It also creates real Codex threads on the
user's daemon, which show up in the command center while the test runs. It
is skipped unless ``WORKSPACE_THREAD_E2E=1`` is set, so nothing here runs by
accident; set it only once a change to the launcher is otherwise complete.

Everything is real: Codex's daemon (thread creation, naming, turns,
deletion), ``br`` (a bead with a ``sourceset:`` label), git (worktrees and
clones), and workspace discovery, since the test runs from the fixture
workspace as a shell would. Only the human door's final ``execvp`` of
``codex resume`` is intercepted, because the test cannot survive being
replaced by an interactive session; everything up to that point, including
the argv it would exec, is checked for real.

The workspace is a temporary clone with its own ``br`` store and a local
sample repository as its sourceset, so the repo's own ``.workspace-threads/`` and
``.beads/`` are never touched and the bead vanishes with the directory
(``br delete`` would only leave a tombstone). Every Codex thread the test makes
is deleted from the daemon in cleanup, by id, whether the test passed or
not; a thread with a running turn is waited on first so the delete is of
an idle thread.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from workspace_thread import IMP_DOOR_SETTINGS, cli, prepare, run_turn
from workspace_thread.codex import Client, socket_path

ONE_WORD_PROMPT = "Reply with exactly the word OK and do nothing else."
TURN_TIMEOUT_SECONDS = 300


def git(path: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), *args], text=True, stderr=subprocess.DEVNULL
    ).strip()


def br(path: Path, *args: str) -> str:
    return subprocess.check_output(["br", *args], cwd=path, text=True)


def read_thread(client: Client, thread_id: str) -> dict:
    """``thread/read`` with turns, riding out the daemon's rollout write.

    Right after ``turn/start`` the daemon can answer that the thread's
    rollout file is still empty; it is written moments later, so that one
    error is retried rather than reported.
    """
    deadline = time.monotonic() + TURN_TIMEOUT_SECONDS
    while True:
        try:
            return client.request("thread/read", {"threadId": thread_id, "includeTurns": True})["thread"]
        except RuntimeError as error:
            rollout_not_written_yet = "is empty" in str(error)
            out_of_time = time.monotonic() > deadline
            if not rollout_not_written_yet or out_of_time:
                raise
            time.sleep(1)


def wait_until_idle(client: Client, thread_id: str) -> dict:
    """Poll ``thread/read`` until the thread's turn is over; return the thread.

    The agent door returns while its turn runs and closes its connection,
    so the turn's notifications are not available to wait on; the thread's
    own status is.
    """
    deadline = time.monotonic() + TURN_TIMEOUT_SECONDS
    while True:
        thread = read_thread(client, thread_id)
        turns = thread.get("turns") or []
        turn_in_progress = any(turn.get("status") == "inProgress" for turn in turns)
        thread_active = thread["status"]["type"] == "active"
        still_running = thread_active or turn_in_progress
        if not still_running:
            return thread
        if time.monotonic() > deadline:
            raise TimeoutError(f"thread {thread_id} still running after {TURN_TIMEOUT_SECONDS}s")
        time.sleep(2)


@unittest.skipUnless(os.environ.get("WORKSPACE_THREAD_E2E") == "1", "set WORKSPACE_THREAD_E2E=1 to run against the real daemon")
@unittest.skipUnless(shutil.which("codex") and shutil.which("br"), "needs codex and br on PATH")
class DoorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.source = self.root / "sample"
        for repo in (self.workspace, self.source):
            repo.mkdir()
            git(repo, "init")
            git(repo, "config", "user.email", "test@example.invalid")
            git(repo, "config", "user.name", "Workspace thread e2e test")
            (repo / "README.md").write_text("fixture\n")
            git(repo, "add", "README.md")
            git(repo, "commit", "-m", "fixture")
        sourceset = self.workspace / "sourcesets" / "sample"
        sourceset.mkdir(parents=True)
        (sourceset / "sourceset.json").write_text(json.dumps({"repos": [{"url": str(self.source)}]}))
        git(self.workspace, "add", "sourcesets")
        git(self.workspace, "commit", "-m", "sourceset")
        br(self.workspace, "init", "--prefix", "e2e", "--quiet")
        # `workspace-thread new` finds its workspace from the current directory, as a
        # human's shell would, so run from the fixture rather than redirecting
        # discovery; attach() chdirs again into the worktree. Either way,
        # put the test back afterwards.
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.workspace)

    def delete_thread_in_cleanup(self, thread_id: str) -> None:
        def delete() -> None:
            client = Client.connect()
            try:
                wait_until_idle(client, thread_id)
                client.request("thread/delete", {"threadId": thread_id})
            finally:
                client.close()
        self.addCleanup(delete)

    def assert_thread_on_daemon(self, thread_id: str, name: str, worktree: Path) -> dict:
        client = Client.connect()
        try:
            thread = read_thread(client, thread_id)
        finally:
            client.close()
        self.assertEqual(thread["name"], name)
        self.assertEqual(Path(thread["cwd"]).resolve(), worktree.resolve())
        return thread

    def test_human_door_opens_a_bead_thread_and_resumes_it_by_id(self) -> None:
        created = json.loads(br(self.workspace, "create", "e2e: the [human] door", "-l", "sourceset:sample", "--json"))
        bead_id = created["id"]
        execs: list[list[str]] = []
        with patch("workspace_thread.launch.os.execvp", side_effect=lambda file, argv: execs.append(argv)):
            self.assertEqual(cli.main(["new", bead_id]), 0)
        self.assertEqual(len(execs), 1)
        codex, resume, thread_id, opening = execs[0]
        self.delete_thread_in_cleanup(thread_id)
        self.assertEqual((codex, resume), ("codex", "resume"))
        self.assertIn(f"br show {bead_id} --json", opening)
        worktree = self.workspace / ".workspace-threads" / bead_id
        self.assertEqual((worktree / "sources/sample/README.md").read_text(), "fixture\n")
        self.assertEqual(Path.cwd(), worktree)
        thread = self.assert_thread_on_daemon(thread_id, bead_id, worktree)
        self.assertEqual(thread.get("turns") or [], [], "the human door starts no turn")
        # `codex resume` reads the rollout from disk; with no turn yet, only
        # the launcher's persist step puts it there.
        rollout = Path(thread["path"] or "")
        self.assertTrue(rollout.is_file() and rollout.stat().st_size > 0, f"no rollout on disk for {thread_id}: {thread.get('path')}")

    def test_agent_door_runs_a_turn_on_a_free_form_title_and_prints_the_id(self) -> None:
        title = "e2e: the [agent] door, with spaces & punctuation"
        slug = "e2e-the-agent-door-with-spaces-punctuation"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = cli.main(["new", "--name", title, "--sourceset", "sample", "--prompt", ONE_WORD_PROMPT])
        self.assertEqual(result, 0)
        thread_id = stdout.getvalue().strip()
        self.delete_thread_in_cleanup(thread_id)
        worktree = self.workspace / ".workspace-threads" / slug
        self.assertTrue((worktree / "sources/sample/README.md").is_file())
        client = Client.connect()
        try:
            thread = wait_until_idle(client, thread_id)
        finally:
            client.close()
        statuses = [turn["status"] for turn in thread["turns"]]
        self.assertEqual(statuses, ["completed"], thread["turns"])
        self.assert_thread_on_daemon(thread_id, title, worktree)

    def test_imp_door_runs_a_turn_to_completion_under_imp_settings(self) -> None:
        run_id = f"e2e-imp-{os.getpid()}"
        # A real file, as an Imp's Run log is: prepare hands it to git as stdout.
        log_path = self.root / "run.log"
        with log_path.open("w") as log:
            thread, client = prepare(run_id, "sample", settings=IMP_DOOR_SETTINGS, workspace=self.workspace, log=log)
            self.delete_thread_in_cleanup(thread.thread_id)
            try:
                result = run_turn(client, thread, ONE_WORD_PROMPT, log=log)
            finally:
                client.close()
        run_log = log_path.read_text()
        self.assertEqual(thread.slug, run_id, "a Run Id is its own slug")
        self.assertEqual(thread.worktree, self.workspace / ".workspace-threads" / run_id)
        self.assertEqual(result.status, "completed", run_log)
        self.assertIsNone(result.error)
        self.assertIn("OK", result.last_agent_message or "", run_log)
        self.assertIn("turn completed", run_log)
        self.assert_thread_on_daemon(thread.thread_id, run_id, thread.worktree)


if __name__ == "__main__":
    unittest.main()
