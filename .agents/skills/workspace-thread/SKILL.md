---
name: workspace-thread
description: Launch authorized bead-owned or named work in an isolated worktree of this workspace, with its sourceset, and coordinate it using Codex thread messaging. Use for delegating repository tasks through the workspace launcher.
---

# Workspace threads

Use the `workspace_threads` MCP server's `create_workspace_thread` tool.
It runs the same launcher as `thread new`; each thread receives a detached
workspace worktree and fresh clones of its sourceset repositories.

- For a bead, pass `bead_id`. Its `sourceset:<name>` label selects the
  sourceset. The child is told to read the bead; `prompt` adds context.
- Without a bead, pass `name` and `prompt`, plus `sourceset` if needed.
- Pass this conversation's thread ID as `parent_thread_id` for replies.
  The shell's `CODEX_THREAD_ID` identifies the calling conversation.
- Retain `thread_id`, `turn_id`, and `worktree` from the structured result.
  The tool returns when the opening turn starts, not when work finishes.

With `parent_thread_id`, the opening turn only replies
`WORKSPACE_THREAD_READY`. Wait for that turn to complete **before the first
native message**, then call `send_message_to_thread` with "Begin your assigned
work and report progress to the parent." The app attaches its native thread
tools when it starts this follow-up on the idle thread. Sending the first
native message while the daemon-created turn is still running did not make
those tools available in testing. Without a parent, the assignment starts
immediately and there is no coordination handshake.

Use the built-in `read_thread`, `send_message_to_thread`, and `wait_threads`
tools with the returned Codex thread ID. Bead IDs and Codex thread IDs are
different identifiers. Wait with the returned cursor to observe later turns
rather than repeatedly observing an old completion.

Create and update beads using the br skill. The orchestrator records the
worker's ID, reviews its evidence, and closes the bead. Workers report
progress, blockers, and results back to the supplied parent thread.

Fresh sourceset clones do not contain local uncommitted work. Make required
source changes available before launch. Workspace worktrees start at HEAD,
so commit required workspace instructions and assets first. Existing
worktree names are refused; inspect a failed launch before retrying rather
than removing its files blindly.

Server setup and launch guarantees live in
[threadlib's README](../../../lib/python/threadlib/README.md).
