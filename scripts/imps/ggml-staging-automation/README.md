# The ggml-staging-automation imps

Everything imp in this workspace that acts on
[ROCm/ggml-staging-automation](https://github.com/ROCm/ggml-staging-automation)
lives here, laid out as [scripts/imps/README.md](../README.md) describes:
[loops/](loops/) holds one directory per loop, each standalone Imp has a
directory beside it, and [build.py](build.py) is the build driver every
Imp here copies into its run workspace. Bring the project up with both
Manifests:

```sh
impctl up --manifest scripts/imps/ggml-staging-automation/loops/bump-automation/bump-loop.json \
          --manifest scripts/imps/ggml-staging-automation/standalone-imps.json
```

[loops/bump-automation/](loops/bump-automation/) is the bump loop; its
README is the record of each of its executables. Its Manifest has the
workspace's [overseer](../overseer/imp.py) check the newest
`fix-llama-bump` Run each weekday.
[standalone-imps.json](standalone-imps.json) inscribes the standalone
Sigils so `impctl launch` can name them. A standalone Run shows up in
`impctl runs` and `.imp/runs/` beside the loop's, but no overseer Watch
names a standalone Sigil, so nothing judges it: a failed one is the
human's to notice, by the `needs attention` line at the end of its log.

## `fix-llama-perplexity`

Arguments: a completed `ROCm/ggml-staging-automation` Actions run URL, the
exact model identifier in that run's perplexity report, and `prefill` or
`decode`. It has no Sensor; launch it by hand under a fresh Run Id:

```sh
impctl launch fix-llama-perplexity fix-ppl-investigation-1 \
  'https://github.com/ROCm/ggml-staging-automation/actions/runs/<run-id>' \
  '<model identifier from the report>' decode
```

Prerequisites: `gh` and `codex` logged in, SSH access to GitHub, and the
workspace `.venv`. The Run clones the automation from GitHub into a fresh
`/tmp/fix-ppl-*` workspace and depends on no checkout under `sources/`.

Each Run checks out the supplied run's automation commit and initializes its
pinned submodules. Codex attempts to reproduce, root-cause and fix the selected
model/regime using `loom/docs/src/workflows/agent-driven-kernel-development.md`
inside that checkout's `hrx-system` repository. The existing build driver and
workspace GPU/style instructions are available in the isolated workspace.

Any repair branch is pushed only to `AaronStGeorge/llama.cpp`. The agent does
not create a PR, edit HRX, or push automation/HRX changes. Diagnosis without a
fix is a valid result; there is no prescribed CI batch or independent verifier.

The workspace retains `source-run.json`, `prompt.txt`, and `handoff.md`.
The Markdown handoff contains reproduction results, root-cause analysis,
any fix and validation, remaining uncertainty, and an optional pushed branch
URL. Exit 0 means Codex delivered a report, not a certified repair. Setup,
execution, or missing/empty-report failures exit nonzero. The workspace and
report location are printed in the Run log for human review.
