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

### Validate at trust boundaries

Decide whether validation is needed from a value's provenance. Values produced
by checked-in code or configuration in this repository are inside its trust
boundary: make them correct at their source rather than defensively validating
them in consumers. Validate values when they enter from outside the repository's
trust boundary.
