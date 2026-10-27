# Building Loom Serve

This sourceset pairs HRX Solutions with the HRX revision declared in its
`MODULE.bazel`. Solutions exposes `//:loom-serve` and owns the Qwen pipelines;
HRX implements the server in `loom/src/loom/tools/loom-serve`. Keep these source
pins aligned: newer HRX revisions can change Bazel flags and pipeline contracts.

Load this sourceset with the workspace loader, then use the workspace Python
environment and Bazelisk on PATH. The upstream Solutions README gives the
Bazelisk 1.29.0 installation command and checksum; `.bazelversion` selects Bazel.
Select the GPU using [the workspace guide](../../docs/hrx-gpu-selection.md).

```sh
PYTHONPATH="$PWD/lib/python" python sources/build.py --gfx 1100 --dry-run
PYTHONPATH="$PWD/lib/python" python sources/build.py \
  --gfx 1100 --gpu-selection GPU-YOUR-UUID --jobs 16 --bazel /path/to/bazel
source sources/hrx-solutions/.envrc
loom-serve --device=amdgpu --listen=127.0.0.1:18080
```

The driver reuses the pinned ROCm SDK cache and passes that SDK and the sibling
HRX checkout explicitly to the Bazel provider. Build state stays under
`sources/hrx-solutions/build`; Bazel may update its checkout's module lockfile
for the local override. The log is `build/build.log`, the native executable is
`build/install/bin/loom-serve`, and the catalogs are under
`build/install/loom-pipeline/qwen/{qwen_08b,qwen_27b}/pipeline.loombc`.
The generated `.envrc` supplies runtime library paths and the GPU pin.
Stop the server before rebuilding its installed pipeline files.

Follow `sources/hrx-solutions/docs/solutions/llm-serving.md` to register,
activate and undrain a model, then send a streaming chat request. Use the
installed catalog directory above as `base_directory`. Check shared `models/`
first: the 0.8B pipeline specifically requires its documented Q5_K_XL checkpoint
and matching tokenizer; an existing Q4 file does not satisfy that contract.
A deployment with `admission: "open"` can also be loaded at startup with
`--model=/absolute/path/to/deployment.json`. Ctrl-C stops a foreground server.

The native build and Qwen3.5-0.8B streaming inference were validated locally on
a Radeon Pro W7900 (gfx1100), including a second build/install and server restart.
This verifies serving connectivity, not the small model's coding-agent quality.
