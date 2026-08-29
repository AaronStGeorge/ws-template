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