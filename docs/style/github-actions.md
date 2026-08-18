# GitHub Actions Style Guide

These rules apply to GitHub Actions workflows and their supporting scripts.

## Style Guidelines

### Prefer isolated Python scripts for nontrivial logic

Keep workflow YAML focused on orchestration. When inline shell logic becomes
somewhat complex, move it into a small, checked-in Python script and invoke
that script from the workflow.

Benefits:

- Testability: The logic can be exercised locally with focused fixtures.
- Debuggability: Python provides clearer errors and standard debugging tools.
- Portability: The script is less dependent on a particular shell environment.
- Readability: The workflow continues to show the high-level sequence of work.

Use judgment for short command sequences that are clearer inline. Signals that
logic belongs in a script include conditionals, loops, parsing, validation,
string manipulation, filesystem transformations, or cleanup behavior.

Preferred:

```yaml
- name: Extract release package
  run: |
    python3 scripts/hrx/extract_release_package.py \
      --archive artifacts/release.tar.gz \
      --package-root-name llama-install \
      --output-dir release/benchmark-package
```

Avoid:

```yaml
- name: Extract release package
  shell: bash
  run: |
    tar -xzf artifacts/release.tar.gz -C release
    if [[ ! -d release/llama-install ]]; then
      echo "Missing package root" >&2
      exit 1
    fi
    mv release/llama-install release/benchmark-package
```
