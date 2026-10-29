# Agent Guide

## Style guides

Before designing or writing any code change — including in plan mode — Read
[docs/style/always.md](docs/style/always.md) in this session. Do not work from
memory of it. Then read each linked guide that applies to the files being
touched:

- `.github/workflows/**`, or scripts invoked from workflows →
  [docs/style/github-actions.md](docs/style/github-actions.md)

When delegating to subagents (Explore, Plan, etc.), name the applicable guide
paths in the subagent's prompt so it reads them too.

In plan mode, when working on a new feature, run the `requirements-cop` skill on
the drafted requirements before designing against them.

## Issue tracking

Work is tracked as beads with `br`. Read [docs/beads.md](docs/beads.md) for
how this workspace uses them: the `sourceset:<name>` label, epic beads that
carry specs, and blocking edges between tickets. `/to-spec`, `/spec-review`
and `/to-tickets` write beads by those conventions.

## Delegating workspace work

A workspace thread is one way to delegate, not the only one: a Codex thread
in its own worktree of the workspace with a sourceset loaded, for work that
should not share the current checkout or needs sources and builds of its
own. The [workspace-thread skill](.agents/skills/workspace-thread/SKILL.md)
says how to launch one with `workspace_threads.create_workspace_thread`.
Ordinary threads and subagents share the caller's checkout and provision no
worktree or sourceset, which suits work meant to share it, such as a review.

## Local models

Local GGUF files are stored in `models/` at the main workspace root. Check there
before downloading models for local tests or benchmarks. In workspace threads,
use the `models/` symlink to that shared directory.

## Running HRX on a GPU

Not every AMD GPU in a machine works with HRX, and it does not pick a good one
on its own. When a task runs HRX builds, tests, or benchmarks, read
[docs/hrx-gpu-selection.md](docs/hrx-gpu-selection.md) for how to find a
supported GPU and pin to it with `ROCR_VISIBLE_DEVICES`. Skip it for tasks that
never launch HRX.
