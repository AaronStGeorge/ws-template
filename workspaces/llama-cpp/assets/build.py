#!/usr/bin/env python3
"""``build.py`` for the HRX-backed llama.cpp workspace.

This file is the thin layer that a project-specific build driver should own.
The reusable modules in ``lib/python/builds`` know *how* to obtain ROCm and
build each project; the driver knows *where* the checkouts are, which knobs its
CLI exposes, and their composition order.

``scripts/ws_load.py`` symlinks this file into ``sources/`` beside the checkouts
it builds, and it finds them as its siblings there::

    sources/
      build.py -> ../workspaces/llama-cpp/assets/build.py
      hrx-system/
      llama.cpp/


The result of each stage is an input to the next one::

    rocm.build(...) -> llama_cpp.build(..., rocm_result)

That data flow matters. It makes llama.cpp consume the exact ROCm SDK and
HRX source checkout supplied by this invocation instead of rediscovering
dependencies from ambient environment variables.

Run it from the environment that provides the shared build library (see
``README.md``)::

    python sources/build.py --gfx 1100 --dry-run
    python sources/build.py --gfx 1100
    python sources/build.py --gfx 1100 --gpu-selection 1

Provider outputs use the following default locations:

* ROCm's version comes from ``pins.json``; the provider maps ``--gfx`` to a
  published SDK bundle, caches that SDK, and links it at ``sources/.rocm``.
* HRX compiles as a CMake dependency inside ``<llama>/build``. Upstream CMake
  owns its compilation targets; ``--gfx`` selects only the ROCm SDK.
* llama.cpp compiles under ``<llama>/build`` and receives a generated ``.envrc``
  containing the ROCm/HRX runtime paths and optional GPU selection.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Dry-run must not create import caches in the shared library.
sys.dont_write_bytecode = True

from builds import llama_cpp, rocm
from builds.llama_cpp import LlamaCppBuildResult, LlamaCppKnobs
from builds.rocm import PinnedTarballKnobs, RocmInstallResult

# The directory this file is invoked from, which is sources/ through the
# symlink. Not resolved: resolving would follow the link into assets/, where
# there are no checkouts.
WORKSPACE_DIR = Path(__file__).absolute().parent

# Checkout locations are workspace policy, not build-provider policy. A future
# workspace with different clone names should change these two constants while
# leaving the provider calls below alone.
HRX_SOURCE_FROM_WORKSPACE = Path("hrx-system")
LLAMA_CPP_SOURCE_FROM_WORKSPACE = Path("llama.cpp")


def _plain_gfx(value: str) -> str:
    """Accept a plain AMDGPU identifier for the pinned ROCm SDK."""
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
            "Fetch a pinned ROCm SDK, then build llama.cpp and HRX together "
            "through HRX_SOURCE_DIR."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""expected source layout:
  sources/hrx-system
  sources/llama.cpp

examples:
  python sources/build.py --gfx 1100 --dry-run
  python sources/build.py --gfx 1100 --gpu-selection 1""",
    )
    parser.add_argument(
        "--gfx",
        required=True,
        type=_plain_gfx,
        help="ROCm SDK architecture such as 1100 (without a 'gfx' prefix).",
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
        hrx_source = _checkout(WORKSPACE_DIR, HRX_SOURCE_FROM_WORKSPACE, "HRX System")
        llama_source = _checkout(
            WORKSPACE_DIR, LLAMA_CPP_SOURCE_FROM_WORKSPACE, "llama.cpp"
        )
    except ValueError as error:
        print(f"!! {error}", file=sys.stderr)
        return 1

    # Knobs describe user/workspace choices. Results below describe what actually
    # happened and carry resolved paths into dependent builds.
    # The SDK link goes beside the checkouts rather than inside one: an untracked
    # file in a checkout would make scripts/ws_load.py refuse to replace it.
    rocm_knobs = PinnedTarballKnobs(
        source_dir=str(WORKSPACE_DIR), gfx_target=args.gfx
    )
    llama_knobs = LlamaCppKnobs(
        source_dir=str(llama_source), hrx_source_dir=str(hrx_source),
        gpu_index=args.gpu_index
    )

    if args.dry_run:
        _banner(f"DRY RUN (workspace={WORKSPACE_DIR})")
        print(f"  rocm  knobs: {rocm_knobs.as_dict()}")
        print(f"  llama knobs: {llama_knobs.as_dict()}")
        return 0

    # Stage 1 reads the ROCm version from pins.json, maps the plain gfx input to a
    # published bundle, caches that SDK, and links it beside the checkouts as `.rocm`.
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

    # Stage 2 lets upstream CMake compile HRX and Loom alongside llama.cpp.
    _banner(f"Building llama.cpp with GGML_HRX at {llama_source}")
    llama_result = llama_cpp.build(llama_knobs, rocm_result)

    print(_summary(rocm_result, llama_result))
    build_complete = llama_result.built and llama_result.written
    if not build_complete:
        print(llama_result.log, file=sys.stderr)
        print("!! llama.cpp build or .envrc generation failed", file=sys.stderr)
        return 1
    return 0


def _summary(
    rocm_result: RocmInstallResult,
    llama_result: LlamaCppBuildResult,
) -> str:
    """Render the output paths and statuses a caller usually needs next."""
    lines = [
        "== Summary",
        f"   rocm.installed     = {rocm_result.installed}",
        f"   rocm.rocm_path     = {rocm_result.rocm_path}",
        f"   llama.built        = {llama_result.built}",
        f"   llama.hrx_build_path = {llama_result.hrx_build_path}",
        f"   llama.build_path   = {llama_result.build_path}",
        f"   llama.envrc_path   = {llama_result.envrc_path}",
        f"   llama.gpu_index    = {llama_result.knobs.gpu_index}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
