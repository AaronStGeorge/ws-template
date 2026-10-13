"""Exercise MCP launch contracts with real local Git worktrees and clones.

Only the Codex service and bead lookup are substituted. The stdio test also
starts the actual MCP server, so transport output and tool discovery are
checked independently of direct Python calls.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from threadlib import assignment
from threadlib.mcp import create


def git(path: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), *args], text=True, stderr=subprocess.DEVNULL
    ).strip()


class LaunchTests(unittest.TestCase):
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
            git(repo, "config", "user.name", "Thread test")
            (repo / "README.md").write_text("fixture\n")
            git(repo, "add", "README.md")
            git(repo, "commit", "-m", "fixture")
        sourceset = self.workspace / "sourcesets" / "sample"
        sourceset.mkdir(parents=True)
        (sourceset / "sourceset.json").write_text(json.dumps({
            "repos": [{"url": str(self.source)}]
        }))
        git(self.workspace, "add", "sourcesets")
        git(self.workspace, "commit", "-m", "sourceset")
        self.client = Mock()
        self.client.start_thread.return_value = {"id": "thread-id"}
        self.client.start_turn.return_value = "turn-id"
        self.addCleanup(patch.stopall)
        patch("threadlib.launch.codex.ensure_daemon").start()
        patch("threadlib.launch.Client.connect", return_value=self.client).start()

    def test_named_launch_clones_and_returns_addressable_ids(self) -> None:
        result = create(
            self.workspace, name="named-task", sourceset="sample",
            prompt="Do the task", parent_thread_id="parent-id",
        )
        worktree = Path(result["worktree"])
        self.assertEqual(result["thread_id"], "thread-id")
        self.assertEqual(result["turn_id"], "turn-id")
        self.assertEqual(result["name"], "named-task")
        self.assertEqual((worktree / "sources/sample/README.md").read_text(), "fixture\n")
        self.client.start_thread.assert_called_once_with(worktree, {})
        opening = self.client.start_turn.call_args.args[1]
        self.assertIn("Do the task", opening)
        self.assertIn("parent-id", opening)
        self.client.close.assert_called_once()

    def test_bead_supplies_sourceset_and_prompt(self) -> None:
        with patch("threadlib.assignment.bead.sourceset_label", return_value="sample"):
            result = create(self.workspace, bead_id="ws-123", prompt="Report evidence")
        self.assertEqual(result["name"], "ws-123")
        opening = self.client.start_turn.call_args.args[1]
        self.assertIn("br show ws-123 --json", opening)
        self.assertIn("Report evidence", opening)

    def test_invalid_inputs_do_not_create_worktrees(self) -> None:
        cases = [
            {}, {"bead_id": "ws-123", "name": "both"},
            {"bead_id": "ws-123", "sourceset": "sample"},
            {"name": "no-prompt"}, {"name": "empty-prompt", "prompt": ""},
            {"name": "../outside", "prompt": "bad"},
            {"name": "escape", "sourceset": "../sample", "prompt": "bad"},
        ]
        for inputs in cases:
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                create(self.workspace, **inputs)
        self.assertFalse((self.workspace / ".threads").exists())

    def test_missing_sourceset_fails_before_worktree_creation(self) -> None:
        with self.assertRaises(FileNotFoundError):
            create(self.workspace, name="missing", sourceset="missing", prompt="test")
        self.assertFalse((self.workspace / ".threads").exists())

    def test_duplicate_preserves_existing_files(self) -> None:
        first = create(self.workspace, name="duplicate", prompt="test")
        marker = Path(first["worktree"]) / "work.txt"
        marker.write_text("preserve me")
        with self.assertRaises(FileExistsError):
            create(self.workspace, name="duplicate", prompt="test")
        self.assertEqual(marker.read_text(), "preserve me")
        self.client.start_thread.assert_called_once()

    def test_turn_failure_reports_recovery_location_and_closes_connection(self) -> None:
        self.client.start_turn.side_effect = RuntimeError("service unavailable")
        with self.assertRaisesRegex(RuntimeError, "thread-id.*opening turn failed"):
            create(self.workspace, name="failed", prompt="test")
        self.assertTrue((self.workspace / ".threads/failed").is_dir())
        self.client.close.assert_called_once()

    def test_cli_assignment_still_allows_interactive_named_launch(self) -> None:
        task = assignment.resolve(self.workspace, name="interactive")
        self.assertIsNone(task.prompt)

    def test_stdio_discovery_and_error_response(self) -> None:
        async def check() -> None:
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "threadlib.mcp", "--workspace", str(self.workspace)],
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    self.assertEqual([tool.name for tool in listed.tools], ["create_workspace_thread"])
                    result = await session.call_tool("create_workspace_thread", {"name": "invalid"})
                    self.assertTrue(result.isError)
                    self.assertIn("nonempty prompt", result.content[0].text)
        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
