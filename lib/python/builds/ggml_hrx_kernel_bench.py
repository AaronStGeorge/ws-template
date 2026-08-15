from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from buildlib import BuildKnobs, BuildResult, build_dir, resolve_source_dir
from builds.hrx_system import (
    _already_configured,
    _cmake_gfx_target_list,
    _run,
    _write_configure_marker,
)
from builds.rocm import RocmInstallResult

PROJECT = "ggml-hrx-kernel-bench"


@dataclass(frozen=True)
class GgmlHrxKernelBenchKnobs(BuildKnobs):
    """Knobs for building ggml-hrx-kernel-bench and generating its ``.envrc``.

    ``source_dir`` (required by :class:`BuildKnobs`) is the bench checkout. The
    bench CMake ``add_subdirectory``s the hrx-system tree and builds the Loom
    tooling itself, so this build needs to know where that tree is
    (``hrx_systems_source_dir`` -> ``GGML_HRX_HRX_SYSTEMS_SOURCE_DIR``) and which
    AMDGPU arch to target (``gfx_targets`` -> ``IREE_HAL_AMDGPU_TARGETS``). The
    ROCm/LLVM toolchain is supplied to :func:`build` as an upstream
    :class:`~builds.rocm.RocmInstallResult`, not as a knob.
    """

    # --- build knobs ---
    hrx_systems_source_dir: str = ""  # -> GGML_HRX_HRX_SYSTEMS_SOURCE_DIR
    gfx_targets: str = "gfx1151"  # comma/semicolon separated AMDGPU targets
    build_type: str = "RelWithDebInfo"
    jobs: int = 0  # 0 -> let Ninja decide; >0 passes --parallel <jobs>

    # --- .envrc knobs ---
    pip_extras: str = "numpy,dev"  # extras for the bench editable install; "" -> none
    gpu_index: int | None = None  # ROCR_VISIBLE_DEVICES device index; None -> no mask
    skip_venv_var: str = "GGML_HRX_BENCH_SKIP_VENV"  # set =1 to skip venv handling
    package_import: str = "ggml_hrx_kernel_bench"  # importability guard for the bench install
    overwrite: bool = True  # overwrite an existing .envrc


@dataclass(frozen=True)
class GgmlHrxKernelBenchBuildResult(BuildResult):
    """Result of building the bench (configure + compile) and writing its ``.envrc``.

    The bench has no install step -- the Loom tools are staged into ``build/tools``
    by the ``ggml-hrx-loom-tools`` ALL target during compile. ``build_exit_code`` is
    ``None`` when compile was skipped because configure failed; ``envrc_path`` is
    ``None`` when the ``.envrc`` was not written (build failed or overwrite refused).
    """

    knobs: GgmlHrxKernelBenchKnobs  # narrow the base's knobs field to this project's type
    configure_exit_code: int
    build_exit_code: int | None
    envrc_path: Path | None
    rocm_path: Path | None
    log: str = ""
    claude_md_path: Path | None = None

    @property
    def built(self) -> bool:
        """True when configure and compile both succeeded."""
        return self.configure_exit_code == 0 and self.build_exit_code == 0

    @property
    def written(self) -> bool:
        """True when the ``.envrc`` was written (implies the build succeeded)."""
        return self.envrc_path is not None


class BenchEnvError(RuntimeError):
    """A ``.envrc`` generation step failed; carries an exit code for the log."""


def build(
    knobs: GgmlHrxKernelBenchKnobs, rocm: RocmInstallResult
) -> GgmlHrxKernelBenchBuildResult:
    """Configure + build the bench, then write a ``.envrc`` into the bench repo.

    The bench CMake builds the in-tree hrx-system/IREE runtime and the Loom tooling
    itself (staging tools into ``build/tools``), so this drives a single CMake
    configure + compile -- mirroring :mod:`builds.hrx_system` -- with the ROCm
    toolchain pinned through absolute compiler paths + ``GGML_HRX_ROCM_PATH``.

    The bench's CTest generation runs a Python script at configure time that imports
    the bench's deps (nanobind + PyYAML + numpy), so the ``.envrc`` -- which creates the
    per-project venv and editable-installs the bench into it -- is written *first*
    and sourced for the configure step; sourcing activates that venv, so CMake's
    ``find_package(Python3)`` resolves an interpreter that has the deps. Configure is
    skipped when ``<build>`` already holds a cache configured with an identical
    command line (recorded in a marker). Tool discovery is *not* emitted into the
    ``.envrc`` -- the CMake cache already carries ``GGML_HRX_TOOL_DIR`` and bakes it
    into the test commands.
    """
    if rocm.rocm_path is None:
        raise ValueError(
            "ggml_hrx_kernel_bench.build requires an installed ROCm SDK; rocm_path is None"
        )
    src = resolve_source_dir(knobs)
    out = build_dir(src)
    out.mkdir(parents=True, exist_ok=True)
    rocm_root = Path(rocm.rocm_path).expanduser().resolve()

    log_parts: list[str] = []

    # Write the .envrc first; the configure step sources it so the venv it manages
    # (bench + nanobind + PyYAML + numpy) becomes CMake's configure-time Python interpreter.
    try:
        envrc_path = _write_envrc(src, rocm_root, knobs)
        log_parts.append(f"== Wrote {envrc_path} (ROCM_PATH={rocm_root})")
        # Claude Code auto-loads CLAUDE.md but not AGENTS.md, and agent shells do not
        # auto-apply direnv, so emit a CLAUDE.md that imports AGENTS.md and documents
        # sourcing the .envrc (GPU pin, venv, ROCm tools) before running commands.
        claude_md_path = _write_agent_instructions(src, knobs)
        log_parts.append(f"== Wrote {claude_md_path} (imports AGENTS.md; documents .envrc loading)")
    except BenchEnvError as exc:
        _die(log_parts, str(exc))

    configure_argv = _configure_argv(src, out, rocm_root, knobs)
    if _already_configured(out, configure_argv):
        log_parts.append(f"== Skipping configure: {out} already configured")
        configure_rc = 0
    else:
        # _run_sourced runs the bench .envrc first, so the Python interpreter is available to cmake
        configure_rc = _run_sourced(src, configure_argv, log_parts)
        if configure_rc == 0:
            _write_configure_marker(out, configure_argv)
    if configure_rc != 0:
        _die(log_parts, f"configure failed (exit {configure_rc})")

    build_argv = ["cmake", "--build", str(out)]
    if knobs.jobs > 0:
        build_argv += ["--parallel", str(knobs.jobs)]
    build_rc = _run(build_argv, log_parts)
    if build_rc != 0:
        _die(log_parts, f"build failed (exit {build_rc})")

    return GgmlHrxKernelBenchBuildResult(
        project=PROJECT,
        knobs=knobs,
        source_path=src,
        build_path=out,
        configure_exit_code=configure_rc,
        build_exit_code=build_rc,
        envrc_path=envrc_path,
        rocm_path=rocm_root,
        log="\n".join(log_parts),
        claude_md_path=claude_md_path,
    )


def _die(log_parts: list[str], message: str) -> NoReturn:
    """Print the accumulated build log and the failure, then exit nonzero.

    The ``cmake`` steps capture their output into ``log_parts`` rather than streaming
    it, so a bare exit would be silent -- dump the log to stderr first so the failure
    is loud and diagnosable.
    """
    log_parts.append(f"!! {message}")
    print("\n".join(log_parts), file=sys.stderr)
    sys.exit(1)


def _run_sourced(src: Path, argv: list[str], log_parts: list[str]) -> int:
    """Run ``argv`` in a shell that first sources the bench ``.envrc`` (cwd = ``src``).

    Sourcing establishes the per-project venv (created, bench editable-installed, and
    activated) and the ROCm environment, so CMake's ``find_package(Python3)`` resolves
    the venv interpreter that carries the configure-time deps (nanobind + PyYAML + numpy). The
    ``.envrc`` uses ``$PWD`` for the venv location, so it must run with cwd = ``src``.
    """
    cmd = ["bash", "-c", 'source ./.envrc; exec "$@"', "bash", *[str(a) for a in argv]]
    completed = subprocess.run(cmd, cwd=str(src), capture_output=True, text=True)
    log_parts.append(
        f"$ (source .envrc) {' '.join(str(a) for a in argv)}\n"
        f"[exit {completed.returncode}]\n{completed.stdout}{completed.stderr}"
    )
    return completed.returncode


def _configure_argv(
    src: Path, out: Path, rocm: Path, knobs: GgmlHrxKernelBenchKnobs
) -> list[str]:
    """Build the bench ``cmake`` configure command line.

    Mirrors :func:`builds.hrx_system._configure_argv` (same ROCm/LLVM toolchain and
    linker), adapted to the bench entry point: it passes the required
    ``GGML_HRX_HRX_SYSTEMS_SOURCE_DIR`` and ``GGML_HRX_ROCM_PATH`` plus
    ``IREE_ROCM_DEPENDENCY_MODE`` (which the bench does not forward on its own). The
    configure-time Python interpreter is supplied by sourcing the ``.envrc`` (see
    :func:`_run_sourced`), not pinned here. The IREE HAL driver options are force-set
    for the nested build by ``cmake/GgmlHrxLoomTools.cmake``, so they are not repeated.
    """
    llvm_bin = rocm / "lib" / "llvm" / "bin"
    return [
        "cmake",
        "-S", str(src),
        "-B", str(out),
        "-G", "Ninja",
        f"-DGGML_HRX_HRX_SYSTEMS_SOURCE_DIR={knobs.hrx_systems_source_dir}",
        f"-DGGML_HRX_ROCM_PATH={rocm}",
        "-DIREE_ROCM_DEPENDENCY_MODE=package",
        f"-DCMAKE_C_COMPILER={llvm_bin / 'clang'}",
        f"-DCMAKE_CXX_COMPILER={llvm_bin / 'clang++'}",
        f"-DCMAKE_ASM_COMPILER={llvm_bin / 'clang'}",
        f"-DCMAKE_AR={llvm_bin / 'llvm-ar'}",
        f"-DCMAKE_RANLIB={llvm_bin / 'llvm-ranlib'}",
        "-DCMAKE_EXE_LINKER_FLAGS=-fuse-ld=lld",
        "-DCMAKE_SHARED_LINKER_FLAGS=-fuse-ld=lld",
        "-DCMAKE_MODULE_LINKER_FLAGS=-fuse-ld=lld",
        f"-DCMAKE_BUILD_TYPE={knobs.build_type}",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        f"-DIREE_HAL_AMDGPU_TARGETS={_cmake_gfx_target_list(knobs.gfx_targets)}",
    ]


def _write_envrc(
    src: Path, rocm_path: Path, knobs: GgmlHrxKernelBenchKnobs
) -> Path:
    text = _render_envrc(rocm_path, knobs)
    envrc = src / ".envrc"
    if envrc.exists() and not knobs.overwrite:
        raise BenchEnvError(f"{envrc} exists and overwrite=False")
    src.mkdir(parents=True, exist_ok=True)
    envrc.write_text(text, encoding="utf-8")
    return envrc


def _write_agent_instructions(src: Path, knobs: GgmlHrxKernelBenchKnobs) -> Path:
    text = _render_claude_md(knobs)
    claude_md = src / "CLAUDE.md"
    if claude_md.exists() and not knobs.overwrite:
        raise BenchEnvError(f"{claude_md} exists and overwrite=False")
    src.mkdir(parents=True, exist_ok=True)
    claude_md.write_text(text, encoding="utf-8")
    return claude_md


def _render_claude_md(knobs: GgmlHrxKernelBenchKnobs) -> str:
    # Claude Code auto-loads CLAUDE.md but not AGENTS.md, so bridge the project's
    # hand-authored AGENTS.md in via an @import and add the environment-loading
    # instruction that the .envrc (direnv) otherwise only gives to interactive shells.
    header = (
        "<!-- Generated by builds.ggml_hrx_kernel_bench -- do not edit by hand.\n"
        "     Regenerate via skills/build-ggml-hrx-kernel-bench-env/scripts/ggml-hrx-kernel-bench.py.\n"
        "     Claude Code auto-loads CLAUDE.md but not AGENTS.md; this file imports the\n"
        "     project's AGENTS.md and adds the environment-loading instruction below. -->"
    )
    pin_line = (
        f"This build pinned `ROCR_VISIBLE_DEVICES={knobs.gpu_index}` (a machine-local GPU index)."
        if knobs.gpu_index is not None
        else "This build left all GPUs visible (no `ROCR_VISIBLE_DEVICES` pin)."
    )
    body = f"""@AGENTS.md

## Load the project environment before running commands

This checkout's runtime environment is wired by `.envrc` (direnv): the ROCm SDK on
`PATH`/`LD_LIBRARY_PATH`, the per-project `.venv` (this bench editable-installed), and
the GPU pin `ROCR_VISIBLE_DEVICES`. A non-interactive or agent shell does not
auto-apply direnv, so load it yourself before any build/test/tool command, with the
working directory at this repo root:

    bash -c 'source ./.envrc; <command>'

(or `direnv exec . <command>` when direnv is installed and the `.envrc` has been
`direnv allow`ed.)

Skipping this runs against the system Python -- the bench package, nanobind, numpy,
pytest, and PyYAML are missing -- drops the ROCm and Loom tools from `PATH`, and, because the GPU
is unpinned, makes `ctest` select the wrong GPU and fail with a device-library ISA
mismatch. {pin_line}
"""
    return header + "\n\n" + body


# The colon-list prepend helper, copied verbatim from llamacpp-devws/.envrc. Kept
# as a plain (non-f) raw string so its backslashes/braces are not mangled.
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

# Per-project venv with this bench editable-installed, modeled on
# hrx-kernels-ws/.envrc. This is a *local* .venv owned by this build location -- it
# never creates or edits the workspace/root venv. Plain string with @PLACEHOLDER@
# substitution to avoid f-string brace escaping around shell ${...}.
_VENV_BLOCK = """# --- per-project venv + editable install ---
# Each build location gets its own self-contained .venv (you may want several in one
# repo). This bench checkout is editable-installed into it, so local changes are
# picked up live. The workspace/root venv is never touched.
if [ "${@SKIP@:-0}" != "1" ]; then
  if [ ! -f "$PWD/.venv/bin/activate" ]; then
    "${PYTHON:-python3}" -m venv "$PWD/.venv" --prompt "${PWD##*/}"
  fi

  source "$PWD/.venv/bin/activate"

  # This bench checkout, editable, with test/runtime extras.
  if ! python -c "import @PKG@; import nanobind; import numpy; import pytest; import yaml" 2>/dev/null; then
    pip install -e "$PWD@EXTRAS@" --config-settings editable_mode=compat
  fi
fi"""


def _render_envrc(rocm_path: Path, knobs: GgmlHrxKernelBenchKnobs) -> str:
    header = (
        "# Generated by builds.ggml_hrx_kernel_bench -- do not edit by hand.\n"
        "# Regenerate via skills/build-ggml-hrx-kernel-bench-env/scripts/ggml-hrx-kernel-bench.py.\n"
        "#\n"
        "# Wires the ROCm SDK for ggml-hrx-kernel-bench at runtime and manages a\n"
        "# per-project venv (editable-installs this bench).\n"
        "# The Loom tools are built into build/tools and located via the CMake cache\n"
        "# (GGML_HRX_TOOL_DIR), so they are intentionally not wired here."
    )

    # ROCM_PATH is what the bench propagates to the loom/iree tools (and feeds to
    # --rocm-path). GGML_HRX_ROCM_PATH is the CMake test harness default. bin +
    # lib/llvm/bin give the ROCm/LLVM toolchain; lib + rocm_sysdeps/lib cover the
    # runtime libs the tools dlopen for `run`. lib64 is absent from TheRock SDKs.
    rocm_block = (
        "# --- ROCm (from RocmInstallResult.rocm_path) ---\n"
        f'export ROCM_PATH="{rocm_path}"\n'
        'export GGML_HRX_ROCM_PATH="$ROCM_PATH"\n'
        'path_prepend PATH "$ROCM_PATH/bin"\n'
        'path_prepend PATH "$ROCM_PATH/lib/llvm/bin"\n'
        'path_prepend LD_LIBRARY_PATH "$ROCM_PATH/lib"\n'
        'path_prepend LD_LIBRARY_PATH "$ROCM_PATH/lib/rocm_sysdeps/lib"'
    )
    if knobs.gpu_index is not None:
        # Pin every ROCr-runtime consumer (IREE, HIP, the Loom binaries) to one GPU.
        # The index is machine-local: enumeration order can shift across reboots or
        # driver changes, so it is only meaningful on the box this .envrc runs on.
        rocm_block += (
            "\n# --- pin to a single GPU (device index; machine-local ordering) ---\n"
            f'export ROCR_VISIBLE_DEVICES="{knobs.gpu_index}"'
        )

    python_module_block = (
        "# --- CMake-built Python modules ---\n"
        'path_prepend PYTHONPATH "$PWD/build/python"'
    )

    extras = f"[{knobs.pip_extras}]" if knobs.pip_extras else ""
    venv_block = (
        _VENV_BLOCK.replace("@SKIP@", knobs.skip_venv_var)
        .replace("@PKG@", knobs.package_import)
        .replace("@EXTRAS@", extras)
    )

    watch_block = (
        "# --- re-evaluate when the ROCm symlink retargets (a pin bump) ---\n"
        "if command -v watch_file >/dev/null 2>&1; then\n"
        f'  watch_file "{rocm_path}"\n'
        "fi"
    )

    return (
        "\n\n".join(
            [
                header,
                _PATH_PREPEND_HELPER,
                rocm_block,
                python_module_block,
                venv_block,
                watch_block,
            ]
        )
        + "\n"
    )
