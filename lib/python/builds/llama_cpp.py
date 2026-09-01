from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from buildlib import BuildKnobs, BuildResult, build_dir, resolve_source_dir
from builds.hrx_system import (
    HrxSystemBuildResult,
    _already_configured,
    _run,
    _write_configure_marker,
)
from builds.rocm import RocmInstallResult

PROJECT = "llama-cpp"


@dataclass(frozen=True)
class LlamaCppKnobs(BuildKnobs):
    """Knobs for building the llama.cpp fork's HRX backend and its ``.envrc``.

    ``source_dir`` (required by :class:`BuildKnobs`) is the llama.cpp checkout.
    The HRX backend is consumed as an *installed* dist -- ``find_package(hrx)`` /
    ``find_package(loomc)`` against the install tree of an upstream
    :class:`~builds.hrx_system.HrxSystemBuildResult` -- so this build carries no
    HRX source knob. The ROCm/LLVM toolchain likewise arrives as an upstream
    :class:`~builds.rocm.RocmInstallResult`, not as a knob.
    """

    # --- build knobs ---
    build_type: str = "RelWithDebInfo"
    jobs: int = 0  # 0 -> let Ninja decide; >0 passes --parallel <jobs>

    # --- .envrc knobs ---
    gpu_index: int | None = None  # ROCR_VISIBLE_DEVICES device index; None -> no mask
    overwrite: bool = True  # overwrite an existing .envrc


@dataclass(frozen=True)
class LlamaCppBuildResult(BuildResult):
    """Result of building llama.cpp (configure + compile) and writing its ``.envrc``.

    There is no install step -- the binaries stay in ``<source>/build/bin``.
    ``build_exit_code`` is ``None`` when compile was skipped because configure
    failed; ``envrc_path`` is ``None`` when the ``.envrc`` was not written (build
    failed or overwrite refused).
    """

    knobs: LlamaCppKnobs  # narrow the base's knobs field to this project's type
    configure_exit_code: int
    build_exit_code: int | None
    envrc_path: Path | None
    hrx_install_path: Path | None
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


def build(
    knobs: LlamaCppKnobs, rocm: RocmInstallResult, hrx: HrxSystemBuildResult
) -> LlamaCppBuildResult:
    """Configure + build llama.cpp with GGML_HRX, then write its ``.envrc``.

    The HRX backend resolves ``hrx`` and ``loomc`` as installed CMake packages
    (the HrxPublicDist and Loom tool components installed by
    :mod:`builds.hrx_system`), pinned in via ``CMAKE_PREFIX_PATH``; the ROCm
    toolchain is pinned through absolute compiler paths, mirroring
    :func:`builds.hrx_system._configure_argv`. Nothing in the configure needs the
    ``.envrc`` sourced (the kernel-corpus generator is stdlib-only Python), so the
    ``.envrc`` -- runtime-only wiring: ROCm SDK paths, the hrx install's shared
    libs, and an optional GPU pin -- is written after a successful build. Configure
    is skipped when ``<build>`` already holds a cache configured with an identical
    command line (recorded in a marker).
    """
    if rocm.rocm_path is None:
        raise ValueError(
            "llama_cpp.build requires an installed ROCm SDK; rocm_path is None"
        )
    if not hrx.installed or hrx.install_path is None:
        raise ValueError(
            "llama_cpp.build requires an installed HRX dist; hrx.install_path is None "
            "or the install failed"
        )
    src = resolve_source_dir(knobs)
    out = build_dir(src)
    out.mkdir(parents=True, exist_ok=True)
    rocm_root = Path(rocm.rocm_path).expanduser().resolve()
    hrx_install = Path(hrx.install_path).expanduser().resolve()

    log_parts: list[str] = []
    configure_argv = _configure_argv(src, out, rocm_root, hrx_install, knobs)
    if _already_configured(out, configure_argv):
        log_parts.append(f"== Skipping configure: {out} already configured")
        configure_rc = 0
    else:
        configure_rc = _run(configure_argv, log_parts)
        if configure_rc == 0:
            _write_configure_marker(out, configure_argv)

    build_rc: int | None = None
    envrc_path: Path | None = None
    if configure_rc == 0:
        build_argv = ["cmake", "--build", str(out)]
        if knobs.jobs > 0:
            build_argv += ["--parallel", str(knobs.jobs)]
        build_rc = _run(build_argv, log_parts)

        if build_rc == 0:
            envrc = src / ".envrc"
            if envrc.exists() and not knobs.overwrite:
                log_parts.append(f"!! {envrc} exists and overwrite=False; not written")
            else:
                envrc.write_text(
                    _render_envrc(rocm_root, hrx_install, knobs), encoding="utf-8"
                )
                envrc_path = envrc
                log_parts.append(f"== Wrote {envrc} (ROCM_PATH={rocm_root})")

    return LlamaCppBuildResult(
        project=PROJECT,
        knobs=knobs,
        source_path=src,
        build_path=out,
        configure_exit_code=configure_rc,
        build_exit_code=build_rc,
        envrc_path=envrc_path,
        hrx_install_path=hrx_install,
        rocm_path=rocm_root,
        log="\n".join(log_parts),
    )


def _configure_argv(
    src: Path, out: Path, rocm: Path, hrx_install: Path, knobs: LlamaCppKnobs
) -> list[str]:
    """Build the llama.cpp ``cmake`` configure command line.

    Mirrors :func:`builds.hrx_system._configure_argv`'s toolchain pinning (ROCm
    clang/llvm + lld), adapted to the llama.cpp entry point: ``GGML_HRX=ON``
    selects the HRX ggml backend and ``CMAKE_PREFIX_PATH`` points its
    ``find_package(hrx)`` / ``find_package(loomc)`` at the installed dist. No
    AMDGPU target list is passed -- the fork compiles no device code itself; the
    gfx targets are baked into the hrx dist by the upstream build.
    """
    llvm_bin = rocm / "lib" / "llvm" / "bin"
    return [
        "cmake",
        "-S", str(src),
        "-B", str(out),
        "-G", "Ninja",
        f"-DCMAKE_C_COMPILER={llvm_bin / 'clang'}",
        f"-DCMAKE_CXX_COMPILER={llvm_bin / 'clang++'}",
        f"-DCMAKE_AR={llvm_bin / 'llvm-ar'}",
        f"-DCMAKE_RANLIB={llvm_bin / 'llvm-ranlib'}",
        "-DCMAKE_EXE_LINKER_FLAGS=-fuse-ld=lld",
        "-DCMAKE_SHARED_LINKER_FLAGS=-fuse-ld=lld",
        "-DCMAKE_MODULE_LINKER_FLAGS=-fuse-ld=lld",
        f"-DCMAKE_BUILD_TYPE={knobs.build_type}",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        "-DGGML_HRX=ON",
        f"-DCMAKE_PREFIX_PATH={hrx_install}",
    ]


# The colon-list prepend helper, copied verbatim from builds.ggml_hrx_kernel_bench.
# Kept as a plain (non-f) raw string so its backslashes/braces are not mangled.
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


def _render_envrc(rocm_path: Path, hrx_install: Path, knobs: LlamaCppKnobs) -> str:
    header = (
        "# Generated by builds.llama_cpp -- do not edit by hand.\n"
        "# Regenerate via build.py at the workspace root.\n"
        "#\n"
        "# Wires the runtime environment for the llama.cpp HRX build: the ROCm SDK\n"
        "# on PATH/LD_LIBRARY_PATH and the installed hrx dist's shared libs\n"
        "# (libhrx/libloomc) on LD_LIBRARY_PATH. No venv is managed here -- the\n"
        "# build needs no Python deps."
    )

    # ROCM_PATH names the SDK for the hrx/loom runtime. bin + lib/llvm/bin give the
    # ROCm/LLVM toolchain; lib + rocm_sysdeps/lib cover the runtime libs. lib64 is
    # absent from TheRock SDKs.
    rocm_block = (
        "# --- ROCm (from RocmInstallResult.rocm_path) ---\n"
        f'export ROCM_PATH="{rocm_path}"\n'
        'path_prepend PATH "$ROCM_PATH/bin"\n'
        'path_prepend PATH "$ROCM_PATH/lib/llvm/bin"\n'
        'path_prepend LD_LIBRARY_PATH "$ROCM_PATH/lib"\n'
        'path_prepend LD_LIBRARY_PATH "$ROCM_PATH/lib/rocm_sysdeps/lib"'
    )
    if knobs.gpu_index is not None:
        # Pin every ROCr-runtime consumer to one GPU. The index is machine-local:
        # enumeration order can shift across reboots or driver changes, so it is
        # only meaningful on the box this .envrc runs on.
        rocm_block += (
            "\n# --- pin to a single GPU (device index; machine-local ordering) ---\n"
            f'export ROCR_VISIBLE_DEVICES="{knobs.gpu_index}"'
        )

    hrx_block = (
        "# --- installed hrx dist (libhrx / libloomc shared libs) ---\n"
        f'path_prepend LD_LIBRARY_PATH "{hrx_install / "lib"}"'
    )

    watch_block = (
        "# --- re-evaluate when the ROCm symlink retargets (a pin bump) ---\n"
        "if command -v watch_file >/dev/null 2>&1; then\n"
        f'  watch_file "{rocm_path}"\n'
        "fi"
    )

    return (
        "\n\n".join([header, _PATH_PREPEND_HELPER, rocm_block, hrx_block, watch_block])
        + "\n"
    )
