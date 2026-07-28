#!/usr/bin/env python3
"""Fetch a pinned ROCm SDK, wire the gfx1201-assembly-for-dummies post's ``.envrc``, and build it.

One command threads a single AMDGPU architecture (``--gfx``) through two build steps:

  1. ``builds.rocm.build`` with :class:`PinnedTarballKnobs` (the PINNED_TARBALL
     strategy) downloads the gfx-templated nightly tarball named in ``pins.json``,
     caches it, and symlinks the SDK into the post directory's ``.rocm``.
  2. :func:`build` writes the post directory's ``.envrc`` -- putting the SDK's LLVM
     toolchain (clang, llvm-mc, clang-offload-bundler under ``$ROCM_PATH/lib/llvm/bin``)
     on PATH, the tools for assembling gfx1201 ``.hsaco`` files, and ``CMAKE_PREFIX_PATH``
     on the SDK -- then configures and compiles the post with CMake (``amdclang++`` +
     ``find_package(hip)``), running each CMake step in a shell that sources that
     ``.envrc`` first.

gfx1201-assembly-for-dummies is a blog post that ships a small HIP host program plus a
hand-written gfx1201 assembly kernel; the source is hard-coded to that checkout
``sources/blog/posts/P001_gfx1201-assembly-for-dummies``.

:func:`build` is shaped like the shared providers in ``lib/python/builds`` (a
``BuildKnobs``/``BuildResult`` pair plus a ``build(knobs, rocm)`` entry point over the
``buildlib`` primitives), but it lives here rather than in that package because it is
specific to this one post and not shared.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from buildlib import BuildKnobs, BuildResult, build_dir, resolve_source_dir
from builds import rocm
from builds.rocm import PinnedTarballKnobs, RocmInstallResult

# Repo root, for locating the workspace source checkout (four levels up from
# skills/gfx1201-assembly-for-dummies/scripts/build-gfx1201-assembly-for-dummies.py).
# The shared build library (lib/python) is imported above as an installed package --
# the workspace root .envrc editable-installs it into the venv, so this script does not
# manage sys.path.
REPO_ROOT = Path(__file__).resolve().parents[3]
# The gfx1201-assembly-for-dummies post lives inside the blog as a post directory; this
# script writes .rocm/.envrc there and builds it in place.
POST_SOURCE = (
    REPO_ROOT / "sources" / "blog" / "posts" / "P001_gfx1201-assembly-for-dummies"
)

PROJECT = "gfx1201-assembly-for-dummies"


# --------------------------------------------------------------------------------------
# Build function (.envrc generation + CMake configure/compile)
#
# Modeled on the shared providers in ``lib/python/builds`` but kept here: this wiring is
# specific to this post and not shared, so it does not belong in that package.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Gfx1201AssemblyForDummiesKnobs(BuildKnobs):
    """Typed knobs for building the gfx1201-assembly-for-dummies post.

    ``source_dir`` (required by :class:`BuildKnobs`) is the post checkout the ``.envrc``
    is written into and CMake builds in place. The ROCm SDK is *not* a knob: it is
    supplied to :func:`build` as an upstream :class:`~builds.rocm.RocmInstallResult`, so
    the build always consumes a freshly resolved install.
    """

    gpu_index: int | None = None  # ROCR_VISIBLE_DEVICES device index; None -> no mask


@dataclass(frozen=True)
class Gfx1201AssemblyForDummiesBuildResult(BuildResult):
    """Result of writing the post ``.envrc`` and building it (configure + compile).

    The post has no install step. ``build_exit_code`` is ``None`` when the compile was
    skipped because configure failed; ``envrc_path`` is the ``.envrc`` written and
    ``rocm_path`` the SDK root it points at.
    """

    knobs: Gfx1201AssemblyForDummiesKnobs  # narrow the base's knobs field
    configure_exit_code: int
    build_exit_code: int | None
    envrc_path: Path | None
    rocm_path: Path | None
    log: str = ""

    @property
    def built(self) -> bool:
        """True when configure and compile both succeeded."""
        return self.configure_exit_code == 0 and self.build_exit_code == 0

    @property
    def written(self) -> bool:
        """True when the ``.envrc`` was written."""
        return self.envrc_path is not None

    def as_prompt_input(self, tail_chars: int = 4000) -> dict[str, object]:
        return {
            "project": self.project,
            "knobs": self.knobs.as_dict(),
            "built": self.built,
            "written": self.written,
            "configure_exit_code": self.configure_exit_code,
            "build_exit_code": self.build_exit_code,
            "build_path": str(self.build_path),
            "envrc_path": str(self.envrc_path) if self.envrc_path else None,
            "rocm_path": str(self.rocm_path) if self.rocm_path else None,
            "log_tail": self.log[-tail_chars:],
        }


def build(
    knobs: Gfx1201AssemblyForDummiesKnobs, rocm: RocmInstallResult
) -> Gfx1201AssemblyForDummiesBuildResult:
    """Write the post ``.envrc`` and drive a CMake configure + compile.

    Output lands in ``<source_dir>/build``. The ``.envrc`` is written first -- it puts
    the ROCm/LLVM toolchain (``amdclang++``, ``llvm-mc``, ``clang-offload-bundler``) on
    PATH and ``CMAKE_PREFIX_PATH`` on the SDK. The post's ``CMakeLists.txt`` uses the
    bare ``amdclang++`` compiler and ``find_package(hip)``, both of which come only from
    that ``.envrc``, so the configure and compile steps each run in a shell that sources
    it (see :func:`_run_sourced`); the compile is skipped when configure fails.
    """
    if rocm.rocm_path is None:
        raise ValueError(
            "gfx1201-assembly-for-dummies build requires an installed ROCm SDK; "
            "rocm_path is None"
        )
    src = resolve_source_dir(knobs)
    out = build_dir(src)
    out.mkdir(parents=True, exist_ok=True)
    rocm_root = Path(rocm.rocm_path).expanduser().resolve()

    log_parts: list[str] = []

    # Bootstrap only: wire the toolchain onto PATH/CMAKE_PREFIX_PATH; compiles nothing.
    envrc_path = _write_envrc(src, rocm_root, knobs)
    log_parts.append(f"== Wrote {envrc_path} (ROCM_PATH={rocm_root})")

    configure_rc = _run_sourced(
        src, ["cmake", "-S", str(src), "-B", str(out), "-G", "Ninja"], log_parts
    )
    build_rc: int | None = None
    if configure_rc == 0:
        build_rc = _run_sourced(src, ["cmake", "--build", str(out)], log_parts)

    return Gfx1201AssemblyForDummiesBuildResult(
        project=PROJECT,
        knobs=knobs,
        source_path=src,
        build_path=out,
        configure_exit_code=configure_rc,
        build_exit_code=build_rc,
        envrc_path=envrc_path,
        rocm_path=rocm_root,
        log="\n".join(log_parts),
    )


def _run_sourced(src: Path, argv: list[str], log_parts: list[str]) -> int:
    """Run ``argv`` in a shell that first sources the post ``.envrc`` (cwd = ``src``).

    The post's ``CMakeLists.txt`` uses the bare ``amdclang++`` compiler and
    ``find_package(hip)`` (resolved via ``CMAKE_PREFIX_PATH``); both are provided only by
    the ``.envrc`` :func:`_write_envrc` just wrote, so each CMake step runs with it
    sourced. Unlike the shared ``builds`` helpers this streams output (does not capture
    it) so a long compile is visible live; only the command line and exit code are
    recorded in the build log. The ``.envrc`` is sourced by relative path, so this must
    run with cwd = ``src``.
    """
    cmd = ["bash", "-c", 'source ./.envrc; exec "$@"', "bash", *[str(a) for a in argv]]
    rc = subprocess.run(cmd, cwd=str(src)).returncode
    log_parts.append(f"$ (source .envrc) {' '.join(str(a) for a in argv)}\n[exit {rc}]")
    return rc


def _write_envrc(
    src: Path, rocm_path: Path, knobs: Gfx1201AssemblyForDummiesKnobs
) -> Path:
    """Write ``<src>/.envrc`` wiring the gfx1201 ROCm/LLVM toolchain; return its path.

    Any existing ``.envrc`` is overwritten so a re-run always regenerates it against the
    current SDK.
    """
    src.mkdir(parents=True, exist_ok=True)
    envrc = src / ".envrc"
    envrc.write_text(_render_envrc(rocm_path, knobs), encoding="utf-8")
    return envrc


# The colon-list prepend helper, copied verbatim from llamacpp-devws/.envrc. Kept as a
# plain (non-f) raw string so its backslashes/braces are not mangled.
_PATH_PREPEND_HELPER = r"""# Prepend $2 to the colon-list env var $1, skipping missing dirs and duplicates.
path_prepend() {
  local var_name="$1"
  local path_value="$2"

  [ -d "$path_value" ] || return 0
  eval "case \":\${$var_name:-}:\" in
    *\":$path_value:\"*) ;;
    *) export $var_name=\"$path_value\${$var_name:+:\$$var_name}\" ;;
  esac"
}"""


def _render_envrc(rocm_path: Path, knobs: Gfx1201AssemblyForDummiesKnobs) -> str:
    header = (
        "# Generated by build-gfx1201-assembly-for-dummies.py -- do not edit by hand.\n"
        "# Regenerate via skills/gfx1201-assembly-for-dummies/scripts/build-gfx1201-assembly-for-dummies.py.\n"
        "#\n"
        "# Wires the gfx1201 ROCm/LLVM toolchain (amdclang, llvm-mc, clang-offload-bundler)\n"
        "# onto PATH for compiling .hsaco files, and exports the CMake variables that let\n"
        "# CMakeLists.txt find HIP and use amdclang with no hard-coded paths of its own."
    )

    # bin + lib/llvm/bin give the ROCm/LLVM toolchain (llvm-mc / clang-offload-bundler
    # for assembling .hsaco); lib + rocm_sysdeps/lib cover the runtime libs the tools
    # dlopen. (lib64 is absent from TheRock SDKs.)
    rocm_block = (
        "# --- ROCm (from RocmInstallResult.rocm_path) ---\n"
        f'export ROCM_PATH="{rocm_path}"\n'
        'path_prepend PATH "$ROCM_PATH/bin"\n'
        'path_prepend PATH "$ROCM_PATH/lib/llvm/bin"\n'
        'path_prepend LD_LIBRARY_PATH "$ROCM_PATH/lib"\n'
        'path_prepend LD_LIBRARY_PATH "$ROCM_PATH/lib/rocm_sysdeps/lib"'
    )
    if knobs.gpu_index is not None:
        # Pin every ROCr-runtime consumer (HIP, the LLVM tools) to one GPU. The index
        # is machine-local: enumeration order can shift across reboots or driver
        # changes, so it is only meaningful on the box this .envrc runs on.
        rocm_block += (
            "\n# --- pin to a single GPU (device index; machine-local ordering) ---\n"
            f'export ROCR_VISIBLE_DEVICES="{knobs.gpu_index}"'
        )
    # find_package(hip) resolves via CMAKE_PREFIX_PATH and the build uses amdclang.
    cmake_block = (
        "# --- CMake toolchain (consumed by cmake; keeps CMakeLists.txt path-free) ---\n"
        'path_prepend CMAKE_PREFIX_PATH "$ROCM_PATH"\n'
    )

    watch_block = (
        "# --- re-evaluate when the ROCm symlink retargets (a pin bump) ---\n"
        "if command -v watch_file >/dev/null 2>&1; then\n"
        f'  watch_file "{rocm_path}"\n'
        "fi"
    )

    return (
        "\n\n".join([header, _PATH_PREPEND_HELPER, rocm_block, cmake_block, watch_block])
        + "\n"
    )


# --------------------------------------------------------------------------------------
# CLI driver
# --------------------------------------------------------------------------------------


def _plain_gfx(value: str) -> str:
    """argparse type: require a *plain* AMDGPU arch number like ``1201``.

    The ROCm provider looks the number up in its ``gfx_url_targets`` table, so a
    ``gfx``-prefixed value is rejected here rather than silently breaking that
    convention downstream.
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
        prog="build-gfx1201-assembly-for-dummies.py",
        description="Fetch a pinned ROCm SDK, write the gfx1201-assembly-for-dummies "
        "post .envrc, and build the post with CMake.",
    )
    parser.add_argument(
        "--gfx",
        required=True,
        type=_plain_gfx,
        help="Plain AMDGPU arch number, e.g. 1201 (no 'gfx' prefix). Selects the ROCm "
        "tarball via pins.json's gfx_url_targets table.",
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
        help="Print the resolved knobs and exit without fetching, writing, or building.",
    )
    return parser.parse_args(argv)


def banner(message: str) -> None:
    print(f"== {message}", flush=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not POST_SOURCE.is_dir():
        print(
            f"!! gfx1201-assembly-for-dummies post directory not found: expected a "
            f"checkout at {POST_SOURCE}",
            file=sys.stderr,
        )
        return 1

    rocm_knobs = PinnedTarballKnobs(source_dir=str(POST_SOURCE), gfx_target=args.gfx)
    knobs = Gfx1201AssemblyForDummiesKnobs(
        source_dir=str(POST_SOURCE), gpu_index=args.gpu_index
    )

    if args.dry_run:
        banner(f"DRY RUN (gfx={args.gfx}, gpu_index={args.gpu_index}, source={POST_SOURCE})")
        print(f"  rocm knobs: {rocm_knobs.as_dict()}")
        print(f"  post knobs: {knobs.as_dict()}")
        return 0

    banner(f"Fetching ROCm (gfx={args.gfx}) [PINNED_TARBALL]")
    rocm_result = rocm.build(rocm_knobs)
    if not rocm_result.installed:
        print(rocm_result.log, file=sys.stderr)
        print(f"!! ROCm install failed (exit {rocm_result.exit_code})", file=sys.stderr)
        return 1
    print(f"   ROCm SDK: {rocm_result.rocm_path}")

    banner(f"Building {PROJECT} at {POST_SOURCE}")
    result = build(knobs, rocm_result)
    print(_summary(rocm_result, result))
    return 0 if result.built else 1


def _summary(rocm_result: RocmInstallResult, result: Gfx1201AssemblyForDummiesBuildResult) -> str:
    lines = [
        "== Summary",
        f"   rocm.rocm_path   = {rocm_result.rocm_path}",
        f"   envrc_path       = {result.envrc_path}",
        f"   gpu_index        = {result.knobs.gpu_index}",
        f"   cmake.configure  = {result.configure_exit_code}",
        f"   cmake.build      = {result.build_exit_code}",
        f"   built            = {result.built}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
