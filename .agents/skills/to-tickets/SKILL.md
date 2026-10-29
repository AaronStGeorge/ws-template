---
name: to-tickets
description: Break a spec, a plan, or the current conversation into a set of tracer-bullet tickets, each declaring its blocking edges, published as task beads under the spec's epic.
disable-model-invocation: true
---

# To Tickets

Break a spec, plan, or conversation into a set of **tickets**: tracer-bullet
vertical slices, each declaring the tickets that **block** it.

Tickets are task beads. Read [docs/beads.md](../../../docs/beads.md) for how
this workspace uses beads and the [br skill](../br/SKILL.md) for the
commands.

## Process

### 1. Gather context

The usual input is an epic bead whose spec `/to-spec`. Read the epic in full:
`br show <id> --json` for the spec and `br comments list <id> --json` for its
comments.

Without an epic, work from a document path or the conversation itself, but
say so: the usual move is `/to-spec` first, and the user may prefer it.
Publishing then creates the epic from the plan (step 5).

### 2. Explore the codebase (optional)

If you have not already explored the codebase, do so through the
[component lens](../../../docs/component-lens.md): read the design records
(READMEs and narrative headers) of the components the work crosses. Ticket
titles and descriptions use their Language, and respect the decisions they
record.

Look for opportunities to prefactor the code to make the implementation
easier. "Make the change easy, then make the easy change."

### 3. Draft vertical slices

Break the work into **tracer bullet** tickets.

<vertical-slice-rules>

- Each slice cuts a narrow but COMPLETE path through every component the
  behaviour crosses, tests included: vertical, NOT a horizontal slice of one
  component
- A completed slice is demoable or verifiable on its own
- Each slice is sized to fit in a single fresh context window
- Any prefactoring should be done first

</vertical-slice-rules>

Give each ticket its **blocking edges**: the other tickets that must complete
before it can start. A ticket with no blockers can start immediately.

**Wide refactors are the exception to vertical slicing.** A **wide refactor**
is one mechanical change (rename a column, retype a shared symbol) whose
**blast radius** fans across the whole codebase, so a single edit breaks
thousands of call sites at once and no vertical slice can land green. Don't
force it into a tracer bullet; sequence it as **expand–contract**. First
expand: add the new form beside the old so nothing breaks. Then migrate the
call sites over in batches sized by blast radius (per package, per
directory), each batch its own ticket blocked by the expand, keeping CI green
batch to batch because the old form still exists. Finally contract: delete
the old form once no caller remains, in a ticket blocked by every migrate
batch. When even the batches can't stay green alone, keep the sequence but
let them share an integration branch that all block a final
integrate-and-verify ticket; green is promised only there.

### 4. Quiz the user

Present the proposed breakdown as a numbered list. For each ticket, show:

- **Title**: short descriptive name
- **Blocked by**: which other tickets (if any) must complete first
- **What it delivers**: the end-to-end behaviour this ticket makes work

Then put the open decisions to the user as a `/grilling`-style round, each
question numbered with a recommended answer:

- Does the granularity feel right? (too coarse / too fine)
- Are the blocking edges correct: does each ticket only depend on tickets
  that genuinely gate it?
- Should any tickets be merged or split further?

Iterate until the user approves the breakdown.

### 5. Publish the tickets as beads

Publish the approved tickets in dependency order (blockers first) so each
ticket's blocking edges can reference real ids. For each ticket:

```bash
ACTOR="${BR_ACTOR:-assistant}"
br create --actor "$ACTOR" "<title>" --type task \
  --parent <epic> --labels sourceset:<name> --description "<body>" --json
br dep add <ticket> <blocker>   # once per blocking ticket
br dep add <epic> <ticket>      # the ticket blocks the epic
```

- `--parent` is the epic bead whose spec the tickets implement. In the
  no-epic case from step 1, create one first with the plan as its
  description, so the tickets still have a parent to block.
- Every ticket carries the `sourceset:<name>` label the workspace thread launcher
  needs. It is usually the epic's; ask when a ticket's sources differ or are
  unclear.
- Leave priority at the default, and apply no other labels.
- The parent and blocking edges are native; do not restate them in the body.

Work the **frontier**: `br ready` lists the tickets whose blockers are all
closed. For a purely linear chain that means top to bottom.

Never edit or close the epic. It belongs to the orchestrator, and `br ready`
surfaces it once every ticket has closed.

<ticket-template>

## What to build

The end-to-end behaviour this ticket makes work, from the user's
perspective, not layer-by-layer implementation.

## Acceptance criteria

- [ ] Criterion 1
- [ ] Criterion 2

</ticket-template>

Code is often the clearest way to state a decision; include snippets where
they communicate best. Paths that identify a thing are fine; paths that
identify a moment (worktrees, build and evidence directories) are not.
