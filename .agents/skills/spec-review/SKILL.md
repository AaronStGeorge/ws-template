---
name: spec-review
description: Review a spec until an outside builder could build from it alone. A fresh-context subagent reads the spec and reports its gaps, then a grilling session works them with the user, recording each settled decision back into the epic bead.
disable-model-invocation: true
---

# Spec Review

A spec is the statement of a task, detailed enough that someone outside the
conversation can build it ([docs/beads.md](../../../docs/beads.md)). This
skill tests that claim and keeps grilling until it holds. `/to-spec` calls
it after publishing a draft; it also runs alone on any existing epic bead.

## Process

### 1. Load the spec

With a bead id argument, read it with `br show <id> --json` and its comments
with `br comments list <id> --json`. Without one, review the draft in the
conversation. Note which: the write-back in step 4 depends on it.

### 2. Dispatch the reviewer

A read-only subagent with a fresh context does the reading. The session
that wrote the spec knows what it meant to say and reads that into the text;
a stranger reads only what is there, which is what the builder will get.

Give it the spec verbatim, the paths of
[the component lens](../../../docs/component-lens.md) and
[docs/beads.md](../../../docs/beads.md), and the design records the spec's
Language inherits from. Its brief:

- **The builder's test.** Attempt to plan the build from the spec alone.
  Every point where it would have to guess, ask, or read the conversation
  is a finding: what is missing and where in the spec it belongs.
- **The lens cross-checks.** A commitment no requirement in Why motivates.
  A requirement no commitment satisfies. A requirement
  that names a mechanism or has no level above asking for it. Language that
  contradicts the design records it inherits from.
- **The seams.** Whether the boundaries the Testing section names are the
  highest existing ones that could carry the tests, and whether a new seam
  is proposed where an existing one would do.

It returns findings only: one line each, with the evidence. It decides
nothing.

In the same step run the Skill tool with "requirements-cop" on the
requirements the spec's Why section states. Its CUT and SHARPEN verdicts
join the reviewer's findings.

### 3. Grill

Run the Skill tool with "grilling". The first frontier is the combined
findings and verdicts, each put to the user as a numbered question with a
recommended answer. Settled answers open new questions as they do in any
grilling session; continue until the frontier is empty. The review ends when
a stranger could build from the spec, not when the findings list is
exhausted.

### 4. Record as you go

Record each decision in the spec the moment it settles; sessions end
abruptly and unwritten resolutions disappear. For a bead:

```bash
ACTOR="${BR_ACTOR:-assistant}"
br update --actor "$ACTOR" <id> --description "<revised spec>"
```

For a draft in the conversation, restate the revised sections in the reply.
Either way the spec keeps the `/to-spec` template; a decision goes into the
section it belongs to, never into a trailing changelog.
