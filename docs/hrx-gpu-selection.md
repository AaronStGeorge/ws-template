# Selecting a GPU for HRX

The llama.cpp HRX backend only has dispatch registries for `gfx1100` and
`gfx1151` (`ggml/src/ggml-hrx/dispatch_registration/dispatch-registry.cpp`
returns null for every other target). hrx-system itself also builds for
`gfx1201`, but running inference on one fails at graph compute with
`no HRX dispatch registry for target gfx1201`. HRX does not pick a supported
GPU on its own: on a machine with several AMD GPUs it takes whatever ROCr
enumerates first.

Pin the process to a supported GPU with `ROCR_VISIBLE_DEVICES` (this is the
ROCr runtime variable; `HIP_VISIBLE_DEVICES` is not enough for HRX). It takes a
comma-separated list of ROCr GPU ordinals or `GPU-<uuid>` ids; the UUID form is
stable across reboots, the ordinal form is not.

Find the supported GPU and its ordinal. `rocminfo` is not on PATH in this
workspace; use the one under the ggml-staging-automation checkout's ROCm root
(`<checkout>/build/rocm-root/bin/rocminfo`), which exists after `fetch_rocm.py`
or `build.py` has run. From this workspace root the checkout is
`sources/ggml-staging-automation`; in an imp run workspace it is
`ggml-staging-automation`:

```bash
ROCMINFO=sources/ggml-staging-automation/build/rocm-root/bin/rocminfo
for ordinal in 0 1 2 3; do
  printf 'ROCR ordinal %s -> ' "$ordinal"
  ROCR_VISIBLE_DEVICES="$ordinal" "$ROCMINFO" 2>/dev/null |
    awk '/^[[:space:]]*Name:[[:space:]]+gfx[0-9]/ { name = $2 }
         /^[[:space:]]*Uuid:[[:space:]]+GPU-/  { printf "%s %s", name, $2; exit }'
  echo
done
```

Without any ROCm install, the kernel exposes the same facts:
`/sys/class/kfd/kfd/topology/nodes/*/properties` has a `gfx_target_version`
per node (`110000` is gfx1100, `110501` is gfx1151, `120001` is gfx1201); GPU
nodes in ascending node order map to ROCr ordinals 0, 1, … (the CPU node is
skipped). Do not use the `Agent` numbers `rocminfo` prints — they count CPU
agents too.

Then export the pin before any HRX build, test, or benchmark command, and put it
in the generated `.envrc` when using the `builds` library (`gpu_index` knob):

```bash
export ROCR_VISIBLE_DEVICES=GPU-<uuid-of-the-gfx1100-or-gfx1151>   # or its ordinal
```

Verify the pin took by re-running `rocminfo` with it set: exactly one GPU should
be listed, with the expected `gfx` name. On this machine, as of 2026-09-03:
ordinal `0` is a gfx1201 (RX 9070 XT, unsupported) and ordinal `1` is the
gfx1100 (Radeon Pro W7900, UUID `GPU-c9ad8b8eb7977ed1`), so the pin is
`ROCR_VISIBLE_DEVICES=1` or `ROCR_VISIBLE_DEVICES=GPU-c9ad8b8eb7977ed1`.
