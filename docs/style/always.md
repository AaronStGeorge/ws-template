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

### Open each implementation file with a narrative header

Every substantial implementation file starts with a doc comment, in the
language's doc-comment form. The goal is to produce a working mental model of
the code, not to produce and exhaustive reference fore the code below. Cover:

- The terms it leans on: briefly define any project-specific term the file's
  identifiers use whose meaning a newcomer could not guess.
- Why the file exists: the problem it solves for the code that depends on it,
  and the constraints it was written under (a platform, a contract it must
  honor, a compatibility obligation).
- What it commits to at its boundary: the entry points, inputs and outputs,
  invariants, and failure modes callers can rely on.
- How it delivers that: the strategy, plus the decisions and gotchas that would
  surprise the next reader — ordering constraints, failure handling, trade-offs,
  and any previous approaches now abandoned that are illustrative for the current
  shape of the code.

These points are a checklist for the writer, not an outline for the reader.
Write the comment in whatever order flows best.

- A worked call and its result, serialization of most important data contract, a short snippet, a data-flow sketch, or a walk through one request often teach more than a paragraph describing the same thing, and belong in the header when they do.

- Understanding why what this file does is important to the wider system may require exploring the wider system.

- If the existing file does not have a file header comment either leave off or create a comment that builds an understanding of what the entire file does. When adding feature X to a file that doesn't contain a file header comment don't document only feature X in the file header.

- If a file's purpose is clear without documentation it doesn't need header comment
  - Example: `utils.py` contains utility functions
  - Example: `server_config.json` contains configuration for server X
  - Example: `thing_test.rs` contains tests for thing

- The header is the file-scale counterpart of the component-level format in [`.agents/skills/layering/CONTEXT-FORMAT.md`](../../.agents/skills/layering/CONTEXT-FORMAT.md): its Language, Requirements, Spec, and Design sections are the four points above.

- Test: Is what I'm trying to describe just a re-statement of the code? If so, leave it to the code. Is it an answer to something that can't be in the code — why this component uses design X over Y — or something the code states only diffusely? Then the description earns its place.

- Test: could a reader who saw only this header predict how the file behaves at its boundary and why?
