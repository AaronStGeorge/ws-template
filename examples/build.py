#!/usr/bin/env python3
"""Reference ``build.py`` for an HRX-backed llama.cpp workspace.

This file documents the thin layer that a project-specific build driver should
own. The reusable modules in ``lib/python/builds`` know *how* to obtain ROCm and
build each project; the driver knows *where* a supplied Workspace's checkouts
are, which knobs its CLI exposes, and their composition order.

Here, a *Workspace* is only the directory containing the source checkouts needed
for this build. The repositories are siblings directly beneath it::

    <workspace>/
      hrx-system/
      llama.cpp/


The result of each stage is an input to the next one::

    rocm.build(...) -> hrx_system.build(..., rocm_result)
                    -> llama_cpp.build(..., rocm_result, hrx_result)

That data flow matters. It makes llama.cpp consume the exact ROCm SDK and
installed HRX distribution produced by this invocation instead of rediscovering
dependencies from ambient environment variables.

Run this example from the environment that provides the shared build library
(see ``README.md``), or copy its pattern into a future project driver::

    python examples/build.py /path/to/workspace --gfx 1100 --dry-run
    python examples/build.py /path/to/workspace --gfx 1100
    python examples/build.py /path/to/workspace --gfx 1100 --gpu-selection 1

Provider outputs use the following default locations:

* ROCm's version comes from ``pins.json``; the provider maps ``--gfx`` to a
  published SDK bundle, caches that SDK, and links it at ``<hrx>/.rocm``.
* HRX compiles under ``<hrx>/build`` and installs its public ``hrx`` and
  ``loomc`` CMake packages under ``<hrx>/install``.
* llama.cpp compiles under ``<llama>/build`` and receives a generated ``.envrc``
  containing the ROCm/HRX runtime paths and optional GPU selection.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from builds import hrx_system, llama_cpp, rocm
from builds.hrx_system import HrxSystemBuildResult, HrxSystemKnobs
from builds.llama_cpp import LlamaCppBuildResult, LlamaCppKnobs
from builds.rocm import PinnedTarballKnobs, RocmInstallResult

# Checkout locations are workspace policy, not build-provider policy. A future
# workspace with different clone names should change these two constants while
# leaving the provider calls below alone.
HRX_SOURCE_FROM_WORKSPACE = Path("hrx-system")
LLAMA_CPP_SOURCE_FROM_WORKSPACE = Path("llama.cpp")


def _workspace_dir(value: str) -> Path:
    """Resolve an existing workspace directory for ``argparse``."""
    candidate = Path(value).expanduser()
    try:
        workspace = candidate.resolve(strict=True)
    except OSError as error:
        raise argparse.ArgumentTypeError(
            f"workspace directory does not exist: {candidate}"
        ) from error
    if not workspace.is_dir():
        raise argparse.ArgumentTypeError(
            f"workspace path is not a directory: {workspace}"
        )
    return workspace


def _plain_gfx(value: str) -> str:
    """Accept the plain AMDGPU identifier shared by the two upstream stages.

    The pinned ROCm provider maps a value such as ``1100`` to an SDK bundle. The
    HRX provider receives the same plain value and adds the ``gfx`` prefix that
    IREE expects. Rejecting a prefixed value here prevents those contracts from
    silently diverging.
    """
    gfx = value.strip()
    if not gfx:
        raise argparse.ArgumentTypeError("--gfx must not be empty")
    if gfx.lower().startswith("gfx"):
        raise argparse.ArgumentTypeError(
            f"expected a plain architecture like 1100, not {value!r}; "
            "drop the 'gfx' prefix"
        )
    return gfx


def _gpu_index(value: str) -> int:
    """Accept a non-negative ROCr device index for the generated ``.envrc``."""
    try:
        index = int(value.strip())
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"--gpu-selection expects a device index like 0 or 1, not {value!r}"
        ) from error
    if index < 0:
        raise argparse.ArgumentTypeError(
            f"--gpu-selection must be non-negative, not {index}"
        )
    return index


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build.py",
        description=(
            "Fetch a pinned ROCm SDK, build and install HRX System, then build "
            "llama.cpp with GGML_HRX against that installed distribution."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""expected source layout:
  WORKSPACE_DIR/hrx-system
  WORKSPACE_DIR/llama.cpp

examples:
  python examples/build.py /work/my-project-ws --gfx 1100 --dry-run
  python examples/build.py /work/my-project-ws --gfx 1100 --gpu-selection 1
  python build.py workspaces/my-task --gfx 1100  # future project driver""",
    )
    parser.add_argument(
        "workspace_dir",
        type=_workspace_dir,
        help=(
            "Directory containing the hrx-system and llama.cpp source checkouts."
        ),
    )
    parser.add_argument(
        "--gfx",
        required=True,
        type=_plain_gfx,
        help="Plain AMDGPU architecture such as 1100 (without a 'gfx' prefix).",
    )
    parser.add_argument(
        "--gpu-selection",
        dest="gpu_index",
        type=_gpu_index,
        default=None,
        help=(
            "Optional GPU index written to the llama.cpp .envrc as "
            "ROCR_VISIBLE_DEVICES; omit it to leave all GPUs visible."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate checkout paths and print knobs without fetching or building.",
    )
    return parser.parse_args(argv)


def _checkout(workspace: Path, relative_path: Path, project: str) -> Path:
    """Resolve and validate one source checkout from workspace policy."""
    source = (workspace / relative_path).resolve()
    if not (source / "CMakeLists.txt").is_file():
        raise ValueError(
            f"{project} source not found: expected a checkout with CMakeLists.txt "
            f"at {source}"
        )
    return source


def _banner(message: str) -> None:
    print(f"== {message}", flush=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Resolve layout once, at the workspace boundary. Every provider then gets an
    # explicit source_dir; none relies on cwd or name inference.
    try:
        hrx_source = _checkout(
            args.workspace_dir, HRX_SOURCE_FROM_WORKSPACE, "HRX System"
        )
        llama_source = _checkout(
            args.workspace_dir, LLAMA_CPP_SOURCE_FROM_WORKSPACE, "llama.cpp"
        )
    except ValueError as error:
        print(f"!! {error}", file=sys.stderr)
        return 1

    # Knobs describe user/workspace choices. Results below describe what actually
    # happened and carry resolved paths into dependent builds.
    rocm_knobs = PinnedTarballKnobs(source_dir=str(hrx_source), gfx_target=args.gfx)
    hrx_knobs = HrxSystemKnobs(source_dir=str(hrx_source), gfx_targets=args.gfx)
    llama_knobs = LlamaCppKnobs(
        source_dir=str(llama_source), gpu_index=args.gpu_index
    )

    if args.dry_run:
        _banner(f"DRY RUN (workspace={args.workspace_dir})")
        print(f"  rocm  knobs: {rocm_knobs.as_dict()}")
        print(f"  hrx   knobs: {hrx_knobs.as_dict()}")
        print(f"  llama knobs: {llama_knobs.as_dict()}")
        return 0

    # Stage 1 reads the ROCm version from pins.json, maps the plain gfx input to a
    # published bundle, caches that SDK, and links it into HRX as `.rocm`.
    _banner(f"Fetching pinned ROCm SDK (gfx={args.gfx})")
    rocm_result = rocm.build(rocm_knobs)
    if not rocm_result.installed:
        print(rocm_result.log, file=sys.stderr)
        print(
            f"!! ROCm install failed (exit {rocm_result.exit_code})",
            file=sys.stderr,
        )
        return 1
    print(f"   ROCm SDK: {rocm_result.rocm_path}")

    # Stage 2 compiles HRX and installs its public CMake packages and shared
    # libraries. llama.cpp consumes the install result, never the HRX source tree.
    _banner(f"Building and installing HRX System at {hrx_source}")
    hrx_result = hrx_system.build(hrx_knobs, rocm_result)
    if not (hrx_result.built and hrx_result.installed):
        print(hrx_result.log, file=sys.stderr)
        print("!! HRX System build/install failed", file=sys.stderr)
        return 1
    print(f"   HRX distribution: {hrx_result.install_path}")

    # Stage 3 configures GGML_HRX against that installed distribution, builds
    # llama.cpp, and writes its runtime `.envrc` (including the optional GPU pin).
    _banner(f"Building llama.cpp with GGML_HRX at {llama_source}")
    llama_result = llama_cpp.build(llama_knobs, rocm_result, hrx_result)

    print(_summary(rocm_result, hrx_result, llama_result))
    if not (llama_result.built and llama_result.written):
        print(llama_result.log, file=sys.stderr)
        print("!! llama.cpp build or .envrc generation failed", file=sys.stderr)
        return 1
    return 0


def _summary(
    rocm_result: RocmInstallResult,
    hrx_result: HrxSystemBuildResult,
    llama_result: LlamaCppBuildResult,
) -> str:
    """Render the output paths and statuses a caller usually needs next."""
    lines = [
        "== Summary",
        f"   rocm.installed     = {rocm_result.installed}",
        f"   rocm.rocm_path     = {rocm_result.rocm_path}",
        f"   hrx.built          = {hrx_result.built}",
        f"   hrx.installed      = {hrx_result.installed}",
        f"   hrx.install_path   = {hrx_result.install_path}",
        f"   llama.built        = {llama_result.built}",
        f"   llama.build_path   = {llama_result.build_path}",
        f"   llama.envrc_path   = {llama_result.envrc_path}",
        f"   llama.gpu_index    = {llama_result.knobs.gpu_index}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
