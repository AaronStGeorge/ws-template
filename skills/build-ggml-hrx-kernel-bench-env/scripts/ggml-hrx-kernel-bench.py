#!/usr/bin/env python3
"""Build the workspace HRX System and generate the ggml-hrx-kernel-bench ``.envrc``.

One command threads a single AMDGPU architecture (``--gfx``) through three steps:

  1. ``builds.rocm.build`` with :class:`PinnedTarballKnobs` downloads the
     gfx-templated nightly ROCm tarball named in ``pins.json``, caches it, and
     symlinks the SDK into ``sources/hrx-system/.rocm``.
  2. ``builds.hrx_system.build`` configures + compiles + installs the in-tree IREE
     runtime, libhrx, and Loom tooling against that SDK.
  3. ``builds.ggml_hrx_kernel_bench_env.build`` writes ``sources/ggml-hrx-kernel-bench/.envrc``
     wiring that ROCm SDK and the freshly built Loom tools, and managing the
     bench's own venv + editable install.

The HRX and bench sources are hard-coded to this workspace's checkouts -- the
underlying library can target trees anywhere, but this driver targets here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from builds import ggml_hrx_kernel_bench_env, hrx_system, rocm
from builds.ggml_hrx_kernel_bench_env import GgmlHrxKernelBenchEnvKnobs
from builds.hrx_system import HrxSystemKnobs
from builds.rocm import PinnedTarballKnobs

# Repo root, for locating the workspace source checkouts (four levels up from
# skills/build-ggml-hrx-kernel-bench-env/scripts/ggml-hrx-kernel-bench.py). The
# shared build library (lib/python) is imported above as an installed package --
# the workspace root .envrc editable-installs it into the venv, so this script does
# not manage sys.path.
REPO_ROOT = Path(__file__).resolve().parents[3]
HRX_SOURCE = REPO_ROOT / "sources" / "hrx-system"
BENCH_SOURCE = REPO_ROOT / "sources" / "ggml-hrx-kernel-bench"


def _plain_gfx(value: str) -> str:
    """argparse type: require a *plain* AMDGPU arch number like ``1201``.

    The pipeline threads a plain number end to end -- the ROCm provider looks it up
    in ``pins.json``'s ``gfx_url_targets`` table and the HRX build prepends the
    ``gfx`` IREE expects -- so a ``gfx``-prefixed value is rejected here rather than
    silently breaking that one convention downstream.
    """
    gfx = value.strip()
    if not gfx:
        raise argparse.ArgumentTypeError("--gfx must not be empty")
    if gfx.lower().startswith("gfx"):
        raise argparse.ArgumentTypeError(
            f"expected a plain arch number like 1201, not {value!r} -- drop the 'gfx' prefix"
        )
    return gfx


def _gpu_index(value: str) -> int:
    """argparse type: require a non-negative GPU device index like ``0`` or ``1``.

    Written verbatim into the ``.envrc`` as ``ROCR_VISIBLE_DEVICES`` to pin every
    ROCr-runtime consumer to one GPU. Only the shape is validated (a non-negative
    integer) -- the index is not checked against enumerated hardware, since the
    ``.envrc`` may run on a different machine than the one that generated it.
    """
    text = value.strip()
    try:
        index = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--gpu-selection expects a device index like 0 or 1, not {value!r}"
        )
    if index < 0:
        raise argparse.ArgumentTypeError(
            f"--gpu-selection must be non-negative, not {index}"
        )
    return index


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build-ggml-hrx-kernel-bench.py",
        description="Build sources/hrx-system and generate the bench .envrc against it.",
    )
    parser.add_argument(
        "--gfx",
        required=True,
        type=_plain_gfx,
        help="Plain AMDGPU arch number, e.g. 1201 (no 'gfx' prefix). Threaded into "
        "the ROCm tarball selection (via pins.json's gfx_url_targets table) and the "
        "HRX AMDGPU targets.",
    )
    parser.add_argument(
        "--gpu-selection",
        dest="gpu_index",
        type=_gpu_index,
        default=None,
        help="Optional GPU device index (0, 1, ...) to pin via ROCR_VISIBLE_DEVICES "
        "in the generated .envrc. Omit to leave all GPUs visible.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved knobs and exit without fetching or building.",
    )
    return parser.parse_args(argv)


def banner(message: str) -> None:
    print(f"== {message}", flush=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not (HRX_SOURCE / "CMakeLists.txt").is_file():
        print(
            f"!! HRX source not found: expected a checkout with CMakeLists.txt at "
            f"{HRX_SOURCE}",
            file=sys.stderr,
        )
        return 1
    if not (BENCH_SOURCE / "pyproject.toml").is_file():
        print(
            f"!! Bench source not found: expected a checkout with pyproject.toml at "
            f"{BENCH_SOURCE}",
            file=sys.stderr,
        )
        return 1

    rocm_knobs = PinnedTarballKnobs(source_dir=str(HRX_SOURCE), gfx_target=args.gfx)
    hrx_knobs = HrxSystemKnobs(source_dir=str(HRX_SOURCE), gfx_targets=args.gfx)
    bench_knobs = GgmlHrxKernelBenchEnvKnobs(
        source_dir=str(BENCH_SOURCE), gpu_index=args.gpu_index
    )

    if args.dry_run:
        banner(f"DRY RUN (gfx={args.gfx})")
        print(f"  rocm  knobs: {rocm_knobs.as_dict()}")
        print(f"  hrx   knobs: {hrx_knobs.as_dict()}")
        print(f"  bench knobs: {bench_knobs.as_dict()}")
        return 0

    banner(f"Fetching ROCm (gfx={args.gfx}) [PINNED_TARBALL]")
    rocm_result = rocm.build(rocm_knobs)
    if not rocm_result.installed:
        print(rocm_result.log, file=sys.stderr)
        print(f"!! ROCm install failed (exit {rocm_result.exit_code})", file=sys.stderr)
        return 1
    print(f"   ROCm SDK: {rocm_result.rocm_path}")

    banner(f"Building HRX System (gfx={args.gfx}) at {HRX_SOURCE}")
    hrx_result = hrx_system.build(hrx_knobs, rocm_result)
    if not hrx_result.built:
        print(hrx_result.log, file=sys.stderr)
        print(f"!! HRX build failed (configure={hrx_result.configure_exit_code}, "
              f"build={hrx_result.build_exit_code})", file=sys.stderr)
        return 1

    banner(f"Generating bench .envrc at {BENCH_SOURCE}")
    bench_result = ggml_hrx_kernel_bench_env.build(bench_knobs, rocm_result, hrx_result)
    if not bench_result.written:
        print(bench_result.log, file=sys.stderr)

    print(_summary(rocm_result, hrx_result, bench_result))
    return 0 if bench_result.written else 1


def _summary(rocm_result, hrx_result, bench_result) -> str:
    lines = [
        "== Summary",
        f"   rocm.rocm_path     = {rocm_result.rocm_path}",
        f"   hrx.built          = {hrx_result.built}",
        f"   hrx.installed      = {hrx_result.installed}",
        f"   hrx.build_path     = {hrx_result.build_path}",
        f"   bench.written      = {bench_result.written}",
        f"   bench.envrc_path   = {bench_result.envrc_path}",
        f"   bench.gpu_index    = {bench_result.knobs.gpu_index}",
        f"   bench.loom_tools   = {bench_result.loom_tools}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
