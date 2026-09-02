#!/usr/bin/env python3
"""Build driver for a ggml-staging-automation workspace.

The workspace is the directory this file lives in; the checkout is its sibling::

    <workspace>/
      ggml-staging-automation/   # checkout or worktree (hrx-system, llama.cpp are submodules)
      build.py                   # this file

The build itself lives in ``builds.ggml_staging_automation``; run this with the
workspace-root venv active so that package imports::

    python build.py
    python build.py --no-vulkan
    python .workspaces/<ticket>/build.py -DIREE_HAL_AMDGPU_TARGETS=gfx1151
"""

from __future__ import annotations

import argparse
from pathlib import Path

from builds import ggml_staging_automation
from builds.ggml_staging_automation import (
    GgmlStagingAutomationBuildResult,
    GgmlStagingAutomationKnobs,
)

WORKSPACE = Path(__file__).resolve().parent
CHECKOUT_FROM_WORKSPACE = Path("ggml-staging-automation")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build.py",
        description="Build ggml-staging-automation (ROCm fetch, hrx-system, llama.cpp) via its scripts/hrx tooling.",
        epilog="Unknown arguments are forwarded to scripts/hrx/build_all.py "
        "(which forwards CMake -D flags on to build_hrx_system.py).",
    )
    parser.add_argument("--no-vulkan", action="store_true", help="Skip the Vulkan SDK and GGML_VULKAN.")
    parser.add_argument("--build-type", default="Release", help="llama.cpp CMAKE_BUILD_TYPE.")
    parser.add_argument("--hrx-build-type", default="Release", help="hrx-system CMAKE_BUILD_TYPE.")
    parser.add_argument("--skip-fetch", action="store_true", help="Reuse an existing build/rocm-root.")
    parser.add_argument("--skip-validate", action="store_true", help="Skip validate_install.py.")
    args, extra = parser.parse_known_args()
    args.extra_args = tuple(extra)
    return args


def main() -> int:
    args = parse_args()
    knobs = GgmlStagingAutomationKnobs(
        source_dir=str(WORKSPACE / CHECKOUT_FROM_WORKSPACE),
        build_type=args.build_type,
        hrx_build_type=args.hrx_build_type,
        vulkan=not args.no_vulkan,
        skip_fetch=args.skip_fetch,
        skip_validate=args.skip_validate,
        extra_args=args.extra_args,
    )
    result = ggml_staging_automation.build(knobs)
    print(_summary(result))
    return result.exit_code


def _summary(result: GgmlStagingAutomationBuildResult) -> str:
    lines = [
        "== Summary",
        f"   built              = {result.built}",
        f"   failed_step        = {result.failed_step}",
        f"   rocm_root          = {result.rocm_root}",
        f"   hrx_install_path   = {result.hrx_install_path}",
        f"   llama_install_path = {result.llama_install_path}",
        f"   vulkan_sdk_path    = {result.vulkan_sdk_path}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
