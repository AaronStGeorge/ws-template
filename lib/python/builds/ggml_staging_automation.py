"""Build ``ggml-staging-automation`` by driving its own ``scripts/hrx`` tooling.

The staging repo already owns the *how*: ``scripts/hrx/build_all.py`` fetches the
pinned TheRock ROCm artifacts, builds and installs hrx-system, builds and
installs llama.cpp with ``GGML_HRX`` (and optionally ``GGML_VULKAN``), then
validates the install. ``scripts/hrx/build_vulkan_sdk.py`` builds the Vulkan
SDK that the Vulkan backend needs. This module only sequences those scripts and
takes care of what a fresh checkout / worktree lacks:

1. ``git submodule update --init hrx-system llama.cpp``
2. a ``<source>/.venv`` with ``requirements.txt`` (boto3, zstandard for the
   artifact fetch) plus CI's ``cmake``/``ninja`` pins
3. ``build/vulkan-sdk`` (unless ``vulkan=False``)
4. ``build_all.py``
5. an optional ``.envrc`` next to the workspace's ``build.py``

Layout under ``<source>/build`` is the staging repo's default::

    build/rocm-root  build/downloads  build/vulkan-sdk
    build/hrx-system-build  build/hrx-system-install
    build/llama.cpp-build   build/llama.cpp-install

The staging scripts must be run *by path* with the venv's python (they resolve
``import hrx_build`` through the script directory), never imported.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from buildlib import BuildKnobs, BuildResult, build_dir, resolve_source_dir

PROJECT = "ggml-staging-automation"

SUBMODULES = ("hrx-system", "llama.cpp")
# CI installs these next to requirements.txt (.github/workflows/build_llama_cpp_linux.yml).
PIP_EXTRAS = ("cmake==3.31.6", "ninja")


@dataclass(frozen=True)
class GgmlStagingAutomationKnobs(BuildKnobs):
    """Knobs for the staging build. ``source_dir`` is the ggml-staging-automation checkout."""

    # --- forwarded to scripts/hrx/build_all.py ---
    build_type: str = "Release"  # llama.cpp CMAKE_BUILD_TYPE
    hrx_build_type: str = "Release"  # hrx-system CMAKE_BUILD_TYPE
    vulkan: bool = True  # build build/vulkan-sdk and enable GGML_VULKAN
    skip_fetch: bool = False  # reuse an existing build/rocm-root
    skip_validate: bool = False  # skip scripts/hrx/validate_install.py
    # Extra args passed verbatim to build_all.py, which forwards its own unknown
    # args to build_hrx_system.py as raw CMake args (e.g. -DIREE_HAL_AMDGPU_TARGETS=gfx1151).
    extra_args: tuple[str, ...] = ()

    # --- .envrc knobs ---
    envrc_dir: str = ""  # directory to write .envrc into; "" -> do not write one
    gpu_index: int | None = None  # ROCR_VISIBLE_DEVICES device index; None -> no mask


@dataclass(frozen=True)
class GgmlStagingAutomationBuildResult(BuildResult):
    """Result of the staged build. Steps run in order and stop at the first failure."""

    knobs: GgmlStagingAutomationKnobs
    exit_code: int  # 0, or the exit code of the failing step
    failed_step: str | None  # "submodules" | "venv" | "vulkan-sdk" | "build-all" | None
    venv_path: Path
    vulkan_sdk_path: Path | None
    rocm_root: Path
    hrx_install_path: Path
    llama_install_path: Path
    envrc_path: Path | None
    log: str

    @property
    def built(self) -> bool:
        return self.exit_code == 0


def build(knobs: GgmlStagingAutomationKnobs) -> GgmlStagingAutomationBuildResult:
    src = resolve_source_dir(knobs)
    out = build_dir(src)
    venv = src / ".venv"
    python = venv / "bin" / "python"
    vulkan_sdk = out / "vulkan-sdk" if knobs.vulkan else None

    # The venv's bin goes first on PATH so hrx_build.py picks up the pinned
    # cmake/ninja (it only uses the Ninja generator if `ninja` is on PATH).
    env = dict(os.environ)
    env["PATH"] = f"{venv / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["VIRTUAL_ENV"] = str(venv)

    log_parts: list[str] = []

    def run(argv: list[str]) -> int:
        return _run(argv, log_parts, cwd=src, env=env)

    steps: list[tuple[str, list[list[str]]]] = [
        ("submodules", [["git", "submodule", "update", "--init", *SUBMODULES]]),
        (
            "venv",
            ([] if venv.exists() else [[sys.executable, "-m", "venv", str(venv)]])
            + [[str(python), "-m", "pip", "install", "-r", "requirements.txt", *PIP_EXTRAS]],
        ),
    ]
    if vulkan_sdk is not None:
        steps.append(
            (
                "vulkan-sdk",
                [[str(python), "scripts/hrx/build_vulkan_sdk.py", "--vulkan-sdk-dir", str(vulkan_sdk)]],
            )
        )
    build_all = [
        str(python),
        "scripts/hrx/build_all.py",
        "--build-type",
        knobs.build_type,
        "--hrx-build-type",
        knobs.hrx_build_type,
    ]
    if vulkan_sdk is not None:
        build_all += ["--vulkan-sdk-dir", str(vulkan_sdk)]
    if knobs.skip_fetch:
        build_all.append("--skip-fetch")
    if knobs.skip_validate:
        build_all.append("--skip-validate")
    build_all += list(knobs.extra_args)
    steps.append(("build-all", [build_all]))

    exit_code = 0
    failed_step: str | None = None
    for name, commands in steps:
        for argv in commands:
            exit_code = run(argv)
            if exit_code != 0:
                failed_step = name
                break
        if failed_step:
            break

    llama_install = out / "llama.cpp-install"
    envrc_path: Path | None = None
    if failed_step is None and knobs.envrc_dir:
        envrc_path = Path(knobs.envrc_dir).expanduser().resolve() / ".envrc"
        envrc_path.write_text(_render_envrc(llama_install, knobs))
        log_parts.append(f"wrote {envrc_path}")

    return GgmlStagingAutomationBuildResult(
        project=PROJECT,
        knobs=knobs,
        source_path=src,
        build_path=out,
        exit_code=exit_code,
        failed_step=failed_step,
        venv_path=venv,
        vulkan_sdk_path=vulkan_sdk,
        rocm_root=out / "rocm-root",
        hrx_install_path=out / "hrx-system-install",
        llama_install_path=llama_install,
        envrc_path=envrc_path,
        log="\n".join(log_parts),
    )


def _render_envrc(llama_install: Path, knobs: GgmlStagingAutomationKnobs) -> str:
    lines = [
        "# Generated by builds.ggml_staging_automation -- do not edit by hand.",
        "# Regenerate via build.py in this directory.",
        "#",
        "# The llama.cpp install is $ORIGIN-bundled (HRX/Loom/ROCm runtime libs sit",
        "# next to the binaries), so only PATH is needed.",
        f'PATH_add "{llama_install / "bin"}"',
    ]
    if knobs.gpu_index is not None:
        lines += [
            "",
            "# Pin every ROCr-runtime consumer to one GPU. The index is machine-local:",
            "# enumeration order can shift across reboots or driver changes.",
            f'export ROCR_VISIBLE_DEVICES="{knobs.gpu_index}"',
        ]
    return "\n".join(lines) + "\n"


def _run(argv: list[str], log_parts: list[str], *, cwd: Path, env: dict[str, str]) -> int:
    """Run ``argv``, streaming output to stdout while also collecting it in the log.

    Unlike the capture-only ``_run`` in ``builds.hrx_system``, this streams: the
    staging steps (artifact fetch, hrx-system, llama.cpp) run for tens of minutes
    and this build is normally driven interactively.
    """
    print(f"$ {' '.join(argv)}", flush=True)
    chunks = [f"$ {' '.join(argv)}"]
    proc = subprocess.Popen(
        argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)
        chunks.append(line.rstrip("\n"))
    rc = proc.wait()
    chunks.append(f"[exit {rc}]")
    log_parts.append("\n".join(chunks))
    return rc
