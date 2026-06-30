#!/usr/bin/env python3
"""Fetch a pinned ROCm SDK and build the workspace's HRX System against it.

One command threads a single AMDGPU architecture (``--gfx``) through both halves
of the build:

  1. ``builds.rocm.build`` with :class:`PinnedTarballKnobs` (the PINNED_TARBALL
     strategy) downloads the gfx-templated nightly tarball named in ``pins.json``,
     caches it, and symlinks the SDK into ``sources/hrx-system/build/rocm-root``.
  2. ``builds.hrx_system.build`` configures + compiles + installs the in-tree IREE
     runtime, libhrx, and Loom tooling against that SDK.

Everything except the gfx target uses the library defaults. The HRX source is
hard-coded to the workspace checkout ``sources/hrx-system`` -- the underlying
library can build an hrx-system anywhere, but this driver targets this workspace's
own tree.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from builds import hrx_system, rocm
from builds.rocm import PinnedTarballKnobs
from builds.hrx_system import HrxSystemKnobs

# Repo root, for locating the workspace source checkouts (four levels up from
# skills/build-hrx/scripts/build_hrx.py). The shared build library (lib/python) is
# imported above as an installed package -- the workspace root .envrc
# editable-installs it into the venv, so this script does not manage sys.path.
REPO_ROOT = Path(__file__).resolve().parents[3]
HRX_SOURCE = REPO_ROOT / "sources" / "hrx-system"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build_hrx.py",
        description="Fetch a pinned ROCm SDK and build sources/hrx-system against it.",
    )
    parser.add_argument(
        "--gfx",
        required=True,
        help="AMDGPU architecture, e.g. gfx1151. Threaded into both the ROCm "
        "tarball selection and the HRX AMDGPU targets.",
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

    rocm_knobs = PinnedTarballKnobs(source_dir=str(HRX_SOURCE), gfx_target=args.gfx)
    hrx_knobs = HrxSystemKnobs(source_dir=str(HRX_SOURCE), gfx_targets=args.gfx)

    if args.dry_run:
        banner(f"DRY RUN (gfx={args.gfx}, hrx_source={HRX_SOURCE})")
        print(f"  rocm knobs: {rocm_knobs.as_dict()}")
        print(f"  hrx  knobs: {hrx_knobs.as_dict()}")
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

    print(_summary(rocm_result, hrx_result))
    if not hrx_result.built:
        print(hrx_result.log, file=sys.stderr)

    return 0 if hrx_result.built and hrx_result.installed else 1


def _summary(rocm_result, hrx_result) -> str:
    lines = [
        "== Summary",
        f"   rocm.installed     = {rocm_result.installed}",
        f"   rocm.rocm_path     = {rocm_result.rocm_path}",
        f"   hrx.configure_rc   = {hrx_result.configure_exit_code}",
        f"   hrx.build_rc       = {hrx_result.build_exit_code}",
        f"   hrx.install_rc     = {hrx_result.install_exit_code}",
        f"   hrx.built          = {hrx_result.built}",
        f"   hrx.installed      = {hrx_result.installed}",
        f"   hrx.build_path     = {hrx_result.build_path}",
        f"   hrx.install_path   = {hrx_result.install_path}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
