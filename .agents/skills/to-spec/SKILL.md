---
name: to-spec
description: "Turn the current conversation into a spec and publish it as an epic bead: no interview, just synthesis of what you've already discussed."
disable-model-invocation: true
---

This skill takes the current conversation context and codebase understanding
and produces a spec: the statement of a task to be accomplished, detailed
enough that someone outside this conversation can build it. Do NOT interview
the user; just synthesize what you already know, publish it, and hand it to
`/spec-review`, which does the asking.

The spec becomes the description of an epic bead. Read
[docs/beads.md](../../../docs/beads.md) for how this workspace uses beads and
the [br skill](../br/SKILL.md) for the commands.

## Process

1. Orient through the [component lens](../../../docs/component-lens.md), if
   you haven't already. Read the design records (READMEs and narrative
   headers) of the components the work crosses. Inherit their Language
   throughout the spec and respect the decisions they record; where a
   decision has to change, say so in the spec rather than silently
   overriding it.

2. Sketch the seams at which you're going to test the feature. A seam is a
   component boundary whose spec the tests exercise. Existing boundaries
   should be preferred to new ones. Use the highest boundary possible. If new
   seams are needed, propose them at the highest point you can. The fewer
   seams across the codebase, the better; the ideal number is one. The
   review checks them with the user; record them in the Testing section.

3. Draft the spec using the template below.

4. Publish the draft as an epic bead:

   ```bash
   ACTOR="${BR_ACTOR:-assistant}"
   br create --actor "$ACTOR" "<title>" --type epic \
     --labels sourceset:<name> --description "<spec>" --json
   ```

   The sourceset is the one whose sources the work touches; ask if it is
   unclear. Apply no other labels. Report the bead's title and id.

5. Run the Skill tool with "spec-review", passing the bead id. Publishing
   first means the review's decisions land in the bead as they settle, so an
   abrupt end loses nothing.

<spec-template>

## Why

The requirements, as one concrete story: the problem from the perspective of
the level above, what that level needs this change to accomplish, and the
constraints it runs under. A feature earns its place when the story needs
it, never ahead of it.

## Language

Terms this spec introduces, or sharpens from the design records it inherits
from. Inherited terms are used as the records define them, not redefined
here.

## What it commits to

The behaviour a consumer can observe and check at the change's boundary
without opening the interior, from the user's perspective. This is the
component lens's Spec question, asked of the change, under the heading the
design records use for it.

## Design

The implementation decisions that were made. This can include:

- The components that will be built or modified, and what each is asked for
- The interfaces at their boundaries that change
- Technical clarifications from the developer
- Architectural decisions, schema changes, API contracts
- The seams settled in step 2

Code is often the clearest way to state a decision; include snippets where
they communicate best. Paths that identify a thing are fine; paths that
identify a moment (worktrees, build and evidence directories) are not.

## Testing

Which boundaries will be tested and what makes a good test there: external
behaviour at the boundary, not implementation details. Name prior art, the
similar tests already in the codebase.

## Out of scope

The things that are out of scope for this spec.

## Notes

Any further notes about the feature.

</spec-template>
