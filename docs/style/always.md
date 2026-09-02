# Always-Applied Style Guide

These rules apply to code in every programming language.

## Language and platform-specific guides

Follow each additional guide that applies to the files being changed:

- [GitHub Actions style guide](github-actions.md) for GitHub Actions workflows
  and their supporting scripts.

## Style guidelines

### Break compound conditions into named facts

Before using a compound condition in control flow, assign each individual fact
to a well-named Boolean variable. Combine those facts into a final decision
rather than placing a multi-part expression directly in the condition.

Benefits:

- Readability: Names explain what each part of the decision means.
- Reviewability: Each assumption can be checked independently.
- Debuggability: Individual facts are easy to inspect or log.
- Maintainability: Changes to one fact do not obscure the overall decision.

Tip

Keep simple, single-fact conditions inline. For compound conditions, choose
names that explain the facts and the final decision. Add a short comment only
when the reason for combining those facts is not clear from the surrounding
context.

✅ Preferred:

```python
path_exists = destination.exists()
path_is_symlink = destination.is_symlink()
destination_is_occupied = path_exists or path_is_symlink

if destination_is_occupied:
    raise RuntimeError(f"Destination already exists: {destination}")
```

❌ Avoid:

```python
if destination.exists() or destination.is_symlink():
    raise RuntimeError(f"Destination already exists: {destination}")
```

### Validate once, at the provenance boundary

Trust follows provenance and invariant ownership, not representation or
transport. Passing a value through CLI arguments, JSON, files, subprocesses,
HTTP, or another function does not by itself make the value untrusted.

Validate and normalize a value where it first enters from a source not
controlled by the repository, such as a user, external service, third-party
tool, downloaded artifact, or mutable environment state.

Once checked-in code or an owning loader has established an invariant,
downstream consumers must rely on that invariant rather than validating it
again. If a consumer needs an invariant the producer does not guarantee, add
the guarantee at the producer or owning boundary.

Before adding validation, identify the external source that can violate the
invariant. If the only answer is “a bug in our checked-in producer,” fix or
test the producer instead.

### Comment the why at block scale

Between the narrative header (file scale) and the code itself (line scale)
sits block scale: a type, a function, a stanza inside a longer flow. Give
each nontrivial block a short comment carrying what the code alone cannot:

- **The role in the story** — what this block is for, when its purpose isn't
  evident from its name and shape ("the manual door: judgment-call
  relaunches come through here").
- **The contract being honored** — name the documented term or decision a
  line exists to satisfy ("stdout carries no meaning in the contract and is
  discarded"; "per the recorded no-persistence decision").
- **The load-bearing invariant** — consequences that are invisible at the
  line ("the 409 here is what makes re-emission idempotent, never
  duplicated work").
- **The road not taken** — why the obvious alternative was rejected ("one
  mutex; the traffic is far too small to earn anything finer-grained").
- **The flow's shape** — one comment at the top of a long function naming
  its phases ("validate → claim the id → wire the process → hand the wait
  to a goroutine").

The test is the same as the component lens's: a comment that restates what
the line already says is noise — delete it, or rename the code until the
comment isn't needed. A comment earns its place only by answering a
question the reader would otherwise have to reconstruct from the wider
system.

✅ Preferred:

```go
// Claim the Run Id: check-and-insert under the mutex so two concurrent
// launches of the same Id can't both win. The 409 here is load-bearing —
// it is what lets condition scripts re-emit the same launch safely.
d.mu.Lock()
```

❌ Avoid:

```go
// Lock the mutex and check if the id is in the map.
d.mu.Lock()
```

### Open each implementation file with a narrative header

Every substantial implementation file starts with a doc comment, in the
language's doc-comment form, applying the
[component lens](../component-lens.md) at file scale. The lens owns the goal
(transfer a working mental model, never restate the code), the tests for what
earns its place, and the freedom of form. At file scale its four questions
become:

- **Language** — briefly define any project-specific term the file's
  identifiers use whose meaning a newcomer could not guess.
- **Requirements** — the problem the file solves for the code that depends on
  it, and the constraints it was written under (a platform, a contract it
  must honor, a compatibility obligation).
- **Spec** — the entry points, inputs and outputs, invariants, and failure
  modes callers can rely on.
- **Design** — the strategy, plus the decisions and gotchas that would
  surprise the next reader: ordering constraints, failure handling,
  trade-offs, and abandoned approaches that illuminate the current shape.

File-scale specifics:

- Understanding why this file matters to the wider system may require
  exploring the wider system.
- If the existing file has no header, either leave it off or write one that
  builds an understanding of the entire file. When adding feature X to a file
  without a header, don't document only feature X.
- If a file's purpose is clear without documentation, it needs no header.
  - Example: `utils.py` contains utility functions
  - Example: `server_config.json` contains configuration for server X
  - Example: `thing_test.rs` contains tests for thing
