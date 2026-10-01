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

### Update controlled producers and consumers together

When all producers, consumers, and data instances are controlled by this
repository and can be updated together, change them together. Do not add
backward compatibility code, migration fallbacks, or guards for obsolete
formats.

For example, a manifest checked into the repository can be updated alongside
its loader. The loader should implement the current format without handling
or explicitly rejecting historical formats.

Keep backward compatibility only when an identified consumer or persisted
data instance cannot be updated with the change. Document that dependency
and the compatibility it requires.

### Give each explanation a home

Use the [component lens](../component-lens.md) to decide what understanding a
passage should provide. Keep each detailed explanation in the location best
suited to its readers.

Before adding an explanation, check whether it already exists. Extend or link
to that account instead of maintaining another explanation at similar depth.
Brief summaries may repeat facts when readers need them to use the current
document.

READMEs usually provide orientation, invocation, and outcomes. Headers provide
file-wide context; comments explain local rationale. Tutorials introduce
details needed for their examples. These are defaults, not limits on what
each location may contain.

A link should carry readers to further detail, not replace the explanation
they came for. For example, a README can say that recovered models resume
benchmarking while an agent prompt specifies the required manifest edits.

### Comment the why at block scale

Place a block's explanation beside the code it concerns. Use the
[component lens](../component-lens.md#the-point) to decide whether a comment
adds useful understanding. A block's size or complexity does not itself
require a comment.

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

### One main idea per paragraph

Give each paragraph one main idea, stated early. Keep supporting reasons,
examples, and consequences together when they develop that idea. Start a new
paragraph when the subject changes or a separate decision needs attention.

### Use narrative headers to orient implementation readers

Use the language's doc-comment form for file-level orientation. Apply the
[component lens](../component-lens.md#the-frame) to find context an
implementation reader needs, exploring the wider system when necessary.

A header should orient readers to the file as a whole and point to relevant
explanations elsewhere. Keep file-wide contracts and rationale here when this
is their useful home; put feature-specific rationale beside the feature.

If the file needs no additional orientation, it needs no header, regardless
of its size. Do not add a header just to repeat the filename or code.
