from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from buildlib import BuildKnobs, BuildResult, build_dir, resolve_source_dir

from .rocm import RocmInstallResult

PROJECT = "hrx-system"

# Components installed by the HRX CMake build for downstream consumers: the
# public HRX + loomc distribution and the tools llama.cpp uses to build its
# binary Loom kernel corpus and run Loom benchmarks.
PUBLIC_DIST_COMPONENT = "HrxPublicDist"
LOOM_TOOL_INSTALL_COMPONENTS = (
    "IREETool-loom-link",
    "IREETool-loom-format",
    "IREETool-iree-benchmark-loom",
)
TESTS_DIST_COMPONENT = "HrxTestsDist"


@dataclass(frozen=True)
class HrxSystemKnobs(BuildKnobs):
    """Typed knobs for the HRX System build (in addition to the required source_dir).

    HRX is a CMake/Ninja build of the in-tree IREE runtime plus libhrx and the
    Loom tooling. It needs a ROCm/TheRock SDK (for the LLVM/clang toolchain, the
    AMDGPU device toolchain, and ROCm headers); that SDK is supplied to
    :func:`build` as an upstream :class:`~builds.rocm.RocmInstallResult` dependency
    rather than as a knob, so the build always consumes a freshly resolved install.
    """

    gfx_targets: str = "gfx1151"  # comma/semicolon separated AMDGPU targets
    build_type: str = "RelWithDebInfo"
    jobs: int = 0  # 0 -> let Ninja decide; >0 passes --parallel <jobs>
    loom_build: bool = True  # build the Loom compiler/link tooling (-DLOOM_BUILD=ON)
    install: bool = True  # install the public HRX dist and downstream Loom tools
    install_tests: bool = False  # also install the HRX test tree (HrxTestsDist)


@dataclass(frozen=True)
class HrxSystemBuildResult(BuildResult):
    """Result of building the HRX System CMake project (configure + compile [+ install]).

    Testing is the assembly line's responsibility — the build only compiles (and
    optionally installs the public distribution and downstream Loom tools).
    ``build_exit_code`` is ``None`` when the build was skipped because configure
    failed; ``install_exit_code`` is ``None`` when install was not requested or
    was skipped because the build failed.
    """

    knobs: HrxSystemKnobs  # narrow the base's knobs field to this project's type
    configure_exit_code: int
    build_exit_code: int | None
    install_exit_code: int | None
    install_path: Path | None
    log: str

    @property
    def built(self) -> bool:
        """True when configure and compile both succeeded.

        Mirrors the workspace rule that builds *compile*; the install of the
        public dist is reported separately via :attr:`installed`.
        """
        return self.configure_exit_code == 0 and self.build_exit_code == 0

    @property
    def installed(self) -> bool:
        """True when the public dist and downstream Loom tools installed cleanly."""
        return self.install_exit_code == 0


def build(knobs: HrxSystemKnobs, rocm: RocmInstallResult) -> HrxSystemBuildResult:
    """Configure and build the HRX System CMake project (and optionally install it).

    Output lands in ``<source_dir>/build``; the public dist and downstream Loom
    tools, when installed, go to ``<source_dir>/install`` (a sibling of
    ``build/``). The ROCm toolchain from the upstream ``rocm`` install result is
    pinned entirely through the CMake configure flags (absolute compiler paths +
    ``IREE_ROCM_PATH``); the toolchain binaries self-resolve their shared libraries
    via RUNPATH, so the build inherits the ambient process environment unchanged.

    Configure is skipped when ``<build>`` already holds a CMake cache configured
    with an identical command line (recorded in a marker), so a no-op re-run is
    just Ninja's up-to-date check. Any knob that affects configure changes the
    command line, so the marker misses and the build reconfigures -- staleness
    cannot slip through.
    """
    if rocm.rocm_path is None:
        raise ValueError(
            "hrx_system.build requires an installed ROCm SDK; rocm_path is None"
        )
    src = resolve_source_dir(knobs)
    out = build_dir(src)
    out.mkdir(parents=True, exist_ok=True)
    rocm_root = Path(rocm.rocm_path).expanduser().resolve()

    log_parts: list[str] = []
    configure_argv = _configure_argv(src, out, rocm_root, knobs)
    if _already_configured(out, configure_argv):
        log_parts.append(f"== Skipping configure: {out} already configured")
        configure_rc = 0
    else:
        configure_rc = _run(configure_argv, log_parts)
        if configure_rc == 0:
            _write_configure_marker(out, configure_argv)

    build_rc: int | None = None
    install_rc: int | None = None
    install_path: Path | None = None
    if configure_rc == 0:
        build_argv = ["cmake", "--build", str(out)]
        if knobs.jobs > 0:
            build_argv += ["--parallel", str(knobs.jobs)]
        build_rc = _run(build_argv, log_parts)

        build_succeeded = build_rc == 0
        install_requested = knobs.install
        should_install = build_succeeded and install_requested
        if should_install:
            install_path = src / "install"
            install_components = (
                PUBLIC_DIST_COMPONENT,
                *LOOM_TOOL_INSTALL_COMPONENTS,
            )
            for component in install_components:
                install_rc = _run(
                    _install_argv(out, install_path, component), log_parts
                )
                if install_rc != 0:
                    break

            downstream_components_installed = install_rc == 0
            tests_install_requested = knobs.install_tests
            should_install_tests = (
                downstream_components_installed and tests_install_requested
            )
            if should_install_tests:
                install_rc = _run(
                    _install_argv(out, src / "install-tests", TESTS_DIST_COMPONENT),
                    log_parts,
                )

    return HrxSystemBuildResult(
        project=PROJECT,
        knobs=knobs,
        source_path=src,
        build_path=out,
        configure_exit_code=configure_rc,
        build_exit_code=build_rc,
        install_exit_code=install_rc,
        install_path=install_path,
        log="\n".join(log_parts),
    )


CONFIGURE_MARKER = ".hrx-configure.json"


def _already_configured(out: Path, configure_argv: list[str]) -> bool:
    """True when ``out`` holds a CMake cache configured with ``configure_argv``.

    Requires both a ``CMakeCache.txt`` and a marker recording the exact configure
    command line, so a wiped/partial build tree or a changed configure invocation
    falls through to a fresh configure rather than trusting a stale cache.
    """
    if not (out / "CMakeCache.txt").is_file():
        return False
    marker = out / CONFIGURE_MARKER
    if not marker.is_file():
        return False
    try:
        recorded = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return recorded.get("configure_argv") == configure_argv


def _write_configure_marker(out: Path, configure_argv: list[str]) -> None:
    (out / CONFIGURE_MARKER).write_text(
        json.dumps({"configure_argv": configure_argv}, indent=2) + "\n",
        encoding="utf-8",
    )


def _configure_argv(src: Path, out: Path, rocm: Path, knobs: HrxSystemKnobs) -> list[str]:
    llvm_bin = rocm / "lib" / "llvm" / "bin"
    argv = [
        "cmake",
        "-S", str(src),
        "-B", str(out),
        "-G", "Ninja",
        f"-DIREE_ROCM_PATH={rocm}",
        "-DIREE_ROCM_DEPENDENCY_MODE=package",
        "-DCMAKE_INSTALL_LIBDIR=lib",
        f"-DCMAKE_C_COMPILER={llvm_bin / 'clang'}",
        f"-DCMAKE_CXX_COMPILER={llvm_bin / 'clang++'}",
        f"-DCMAKE_ASM_COMPILER={llvm_bin / 'clang'}",
        f"-DCMAKE_AR={llvm_bin / 'llvm-ar'}",
        f"-DCMAKE_RANLIB={llvm_bin / 'llvm-ranlib'}",
        "-DCMAKE_EXE_LINKER_FLAGS=-fuse-ld=lld",
        "-DCMAKE_SHARED_LINKER_FLAGS=-fuse-ld=lld",
        "-DCMAKE_MODULE_LINKER_FLAGS=-fuse-ld=lld",
        f"-DCMAKE_BUILD_TYPE={knobs.build_type}",
        "-DIREE_HAL_DRIVER_AMDGPU=ON",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        "-DIREE_HAL_DRIVER_VULKAN=ON",
        "-DIREE_HAL_DRIVER_LOCAL_SYNC=ON",
        "-DIREE_HAL_DRIVER_LOCAL_TASK=ON",
        "-DIREE_HAL_DRIVER_NULL=ON",
        f"-DIREE_HAL_AMDGPU_TARGETS={_cmake_gfx_target_list(knobs.gfx_targets)}",
    ]
    if knobs.loom_build:
        argv.append("-DLOOM_BUILD=ON")
    return argv


def _install_argv(out: Path, prefix: Path, component: str) -> list[str]:
    return [
        "cmake", "--install", str(out),
        "--prefix", str(prefix),
        "--component", component,
    ]


def _cmake_gfx_target_list(raw: str) -> str:
    """Normalize a comma/semicolon target list to CMake's ``;`` separated form.

    Each token is also given the ``gfx`` prefix IREE expects when it is missing,
    so a bare arch as threaded from ``--gfx`` (e.g. ``1201``) becomes the valid
    ``IREE_HAL_AMDGPU_TARGETS`` value ``gfx1201``; tokens already prefixed are
    left as-is.
    """
    parts = [p.strip() for p in raw.replace(",", ";").split(";") if p.strip()]
    return ";".join(p if p[:3].lower() == "gfx" else f"gfx{p}" for p in parts)


def _run(argv: list[str], log_parts: list[str]) -> int:
    completed = subprocess.run(argv, capture_output=True, text=True)
    log_parts.append(
        f"$ {' '.join(argv)}\n[exit {completed.returncode}]\n"
        f"{completed.stdout}{completed.stderr}"
    )
    return completed.returncode
