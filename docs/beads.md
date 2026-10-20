# Beads in this workspace

Work in this workspace is tracked as beads: issues in the `br` store under
`.beads/` in the main checkout. The [br skill](../.agents/skills/br/SKILL.md)
is the manual for the tool itself. This document is how this workspace uses
it: what a bead must carry, how a spec and its tickets relate, and what
belongs in bead text. `/to-spec` and `/to-tickets` write beads by these
conventions; workspace threads and Imps read them.

## The store is local

`.beads/` is gitignored, so the store lives on this machine only. A bead id
(`lemonade-ws-NNN`) means nothing in another clone, and the git sync step the
br skill describes does not apply here: there is nothing to flush or commit.
Resolve the actor as the br skill says, `ACTOR="${BR_ACTOR:-assistant}"`,
and pass `--actor "$ACTOR"` on every write.

## The sourceset label

A bead names the sources its work needs with the label `sourceset:<name>`,
where `<name>` is a sourceset
([lib/python/sourceset/README.md](../lib/python/sourceset/README.md)). The
workspace thread launcher reads
that label to fill the workspace thread's worktree, so any bead a workspace
thread might be launched from must carry one; see
[the workspace_thread README](../lib/python/workspace_thread/README.md) for
what the launcher does with it. A bead need not become a workspace thread:
workspace threads give agents an isolated workspace, they are not the only
way work gets done.

## Epic beads carry specs

A spec is the description of an epic bead: the statement of a task to be
accomplished, detailed enough that someone outside the conversation can
build it. It is to a task what a component's Spec in the
[component lens](component-lens.md) is to a component, the commitment others
build against without opening the interior, at the scale of a change rather
than a boundary. A spec is not documentation. Docs build a compressed mental model
of the system for readers, through the lens, and may be written before the spec,
during it, or as one of its tasks. The two serve different readers and
neither stands in for the other.

Tickets are task beads that are children of the epic (`--parent <epic>`).
Each ticket blocks the epic (`br dep add <epic> <ticket>`), and tickets block
each other where one genuinely gates another. `br ready` is therefore the
frontier: it lists tickets whose blockers are closed, and lists the epic only
once every ticket has closed. The orchestrator owns the epic and closes it;
nothing that works a ticket closes or edits the epic.

## Paths and code in bead text

Paths that identify a thing are welcome: a kernel file, a sourceset, a
script. Paths that identify a moment are not: worktrees, build directories,
evidence directories. Those are what rotted in earlier beads and had to be
annotated as historical provenance afterwards. Code is often the clearest
way to say something; use snippets wherever they communicate best.
