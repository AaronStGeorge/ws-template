#!/usr/bin/env python3
"""Fetch the pinned SDK and build Loom Serve plus the Qwen bytecode catalog.

Invoke through sources/build.py after loading the hrx-solutions sourceset.
The shared builds providers own compilation; this driver owns sibling layout.
Bazelisk must be on PATH or supplied with --bazel (see hrx-solutions/README.md).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from builds import hrx_solutions, rocm
from builds.hrx_solutions import HrxSolutionsKnobs
from builds.rocm import PinnedTarballKnobs

WORKSPACE_DIR = Path(__file__).absolute().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gfx", required=True, help="Plain SDK architecture, e.g. 1100")
    parser.add_argument("--gpu-selection", default="0", help="ROCr ordinal or GPU UUID")
    parser.add_argument("--bazel", default="bazel", help="Bazel or Bazelisk executable")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    for name in ("hrx-solutions", "hrx-system"):
        if not (WORKSPACE_DIR / name / "MODULE.bazel").is_file():
            parser.error(f"expected a {name} checkout beside this driver")
    sdk_knobs = PinnedTarballKnobs(source_dir=str(WORKSPACE_DIR), gfx_target=args.gfx)
    knobs = HrxSolutionsKnobs(
        source_dir=str(WORKSPACE_DIR / "hrx-solutions"),
        hrx_source_dir=str(WORKSPACE_DIR / "hrx-system"),
        bazel=args.bazel, jobs=args.jobs, gpu_selection=args.gpu_selection,
    )
    if args.dry_run:
        print(f"rocm: {sdk_knobs.as_dict()}\nhrx-solutions: {knobs.as_dict()}")
        return 0
    sdk = rocm.build(sdk_knobs)
    if not sdk.installed:
        print(sdk.log, file=sys.stderr)
        return 1
    print(f"ROCm: {sdk.rocm_path}", flush=True)
    result = hrx_solutions.build(knobs, sdk)
    print(f"Build log: {result.log_path}")
    if not result.built:
        print(f"Build failed (exit {result.exit_code})", file=sys.stderr)
        return 1
    print(f"Install: {result.install_path}\nEnvironment: {result.envrc_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
