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

[loops/bump-automation/](loops/bump-automation/) is the bump loop with its
overseer; its README is the record of each of its executables.
[standalone-imps.json](standalone-imps.json) inscribes the standalone
Sigils so `impctl launch` can name them. A standalone Run shows up in
`impctl runs` and `.imp/runs/` beside the loop's, but no overseer judges
it: a failed one is the human's to notice, by the `needs attention` line
at the end of its log.

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

Each Run repairs exactly one model in exactly one regime, frozen by the
wrapper before the agent starts. The agent investigates with the Loom
agent-driven kernel workflow, validates the fix on a matched baseline and
candidate pair of isolated CI runs over the complete smoke tier, and opens
a llama.cpp PR against `hrx-graph-develop-v2` from the personal fork.
hrx-system is never edited or pushed; a compiler defect ends the Run as an
`hrx_blocker` with a reproducer and handoff instead of a workaround.

It exits 0 iff `evidence.py` verified the repair from the downloaded CI
artifacts, never from the agent's self-report: the selected regime fails
on the baseline and passes on the candidate, every required model and
regime is present in both reports, and nothing that passed before fails
now. `already_fixed`, `hrx_blocker`, and `blocked` exit nonzero with the
handoff and `investigation.md` retained in the run workspace for human
review. What the agent is told, and what the verifier checks and trusts,
are the prompt and headers in `imp.py` and `evidence.py`.
