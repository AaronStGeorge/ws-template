from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from buildlib import BuildKnobs, BuildResult, resolve_source_dir
from builds.hrx_system import HrxSystemBuildResult
from builds.rocm import RocmInstallResult

PROJECT = "ggml-hrx-kernel-bench-env"

# Loom/IREE executables the bench shells out to. Maps the env-var the .envrc
# exports to the executable filename to discover under the HRX build tree. The
# bench reads these as ``--loom-link`` / ``--loom-compile`` / ``--iree-benchmark-loom``
# path flags (not from the environment), so the exports are for the calling
# script and PATH convenience.
LOOM_TOOLS = {
    "LOOM_LINK": "loom-link",
    "LOOM_COMPILE": "loom-compile",
    "IREE_BENCHMARK_LOOM": "iree-benchmark-loom",
}

# The shared build library (buildlib/builds/assemblyline) editable-install root.
# This module lives at <lib/python>/builds/ggml_hrx_kernel_bench_env.py, so the
# package root is two parents up. Baked into each per-project venv so the venv is
# self-contained -- it never touches the workspace/root venv.
_LIB_PYTHON = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class GgmlHrxKernelBenchEnvKnobs(BuildKnobs):
    """Knobs for generating the ggml-hrx-kernel-bench ``.envrc``.

    ``source_dir`` (required by :class:`BuildKnobs`) is the bench repo the
    ``.envrc`` is written into. The ROCm root and Loom tool paths are *not* knobs:
    they come from the upstream :class:`RocmInstallResult` / :class:`HrxSystemBuildResult`
    passed to :func:`build`, so the generated environment always matches a real
    build.
    """

    pip_extras: str = "numpy,dev"  # extras for the bench editable install; "" -> none
    skip_venv_var: str = "GGML_HRX_BENCH_SKIP_VENV"  # set =1 to skip venv handling
    package_import: str = "ggml_hrx_kernel_bench"  # importability guard for the bench install
    lib_import: str = "buildlib"  # importability guard for the lib/python install
    overwrite: bool = True  # overwrite an existing .envrc


@dataclass(frozen=True)
class GgmlHrxKernelBenchEnvResult(BuildResult):
    """Result of generating the bench ``.envrc``.

    ``envrc_path`` is the file written (``None`` on failure); ``rocm_path`` is the
    ROCm root it points at; ``loom_tools`` records the env-var -> discovered path
    map actually emitted (tools not found under the HRX build tree are omitted).
    """

    knobs: GgmlHrxKernelBenchEnvKnobs  # narrow the base's knobs field
    envrc_path: Path | None
    rocm_path: Path | None
    loom_tools: dict[str, str] = field(default_factory=dict)
    exit_code: int = 0
    log: str = ""

    @property
    def written(self) -> bool:
        """True when the ``.envrc`` was written without error."""
        return self.exit_code == 0 and self.envrc_path is not None

    def as_prompt_input(self, tail_chars: int = 4000) -> dict[str, object]:
        return {
            "project": self.project,
            "knobs": self.knobs.as_dict(),
            "written": self.written,
            "exit_code": self.exit_code,
            "source_path": str(self.source_path),
            "envrc_path": str(self.envrc_path) if self.envrc_path else None,
            "rocm_path": str(self.rocm_path) if self.rocm_path else None,
            "loom_tools": dict(self.loom_tools),
            "log_tail": self.log[-tail_chars:],
        }


class BenchEnvError(RuntimeError):
    """A bench-env generation step failed; carries the exit code for the result."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def build(
    knobs: GgmlHrxKernelBenchEnvKnobs,
    rocm: RocmInstallResult,
    hrx: HrxSystemBuildResult,
) -> GgmlHrxKernelBenchEnvResult:
    """Write a ``.envrc`` into the bench repo wiring ROCm, Loom, and the venv.

    ``ROCM_PATH`` is based off ``rocm.rocm_path`` (the SDK root the ROCm provider
    materialized); the Loom executables are discovered under ``hrx.build_path``
    (they have no install rule and live only in the HRX build tree). The generated
    ``.envrc`` also creates/activates a venv and editable-installs the bench.
    """
    src = resolve_source_dir(knobs)

    log_parts: list[str] = []
    envrc_path: Path | None = None
    rocm_path: Path | None = rocm.rocm_path
    loom_tools: dict[str, str] = {}
    exit_code = 0
    try:
        # Fail fast on a broken upstream environment rather than writing a
        # half-wired .envrc whose gaps only surface later at link/compile/run.
        if rocm.rocm_path is None:
            raise BenchEnvError(
                "RocmInstallResult has no rocm_path (ROCm install did not succeed)"
            )
        if not hrx.built:
            raise BenchEnvError(
                "HRX build did not succeed "
                f"(configure={hrx.configure_exit_code}, build={hrx.build_exit_code}); "
                "the Loom tools it produces are required"
            )
        loom_tools = _find_loom_tools(Path(hrx.build_path))
        text = _render_envrc(rocm.rocm_path, loom_tools, knobs)
        envrc = src / ".envrc"
        if envrc.exists() and not knobs.overwrite:
            raise BenchEnvError(f"{envrc} exists and overwrite=False")
        src.mkdir(parents=True, exist_ok=True)
        envrc.write_text(text, encoding="utf-8")
        envrc_path = envrc
        log_parts.append(f"== Wrote {envrc} (ROCM_PATH={rocm.rocm_path})")
    except BenchEnvError as exc:
        exit_code = exc.exit_code
        log_parts.append(f"!! {exc}")
    except Exception as exc:  # surface any failure on the result, not as a crash
        exit_code = 1
        log_parts.append(f"!! {type(exc).__name__}: {exc}")

    return GgmlHrxKernelBenchEnvResult(
        project=PROJECT,
        knobs=knobs,
        source_path=src,
        build_path=src,  # this task writes into the source tree, not a build dir
        envrc_path=envrc_path,
        rocm_path=rocm_path,
        loom_tools=loom_tools,
        exit_code=exit_code,
        log="\n".join(log_parts),
    )


def _find_loom_tools(build_path: Path) -> dict[str, str]:
    """Discover the Loom executables under the HRX build tree, or fail.

    Mirrors the workspace bootstrap's ``find_executable``: rglob for each tool by
    filename and take the first regular file. All tools are required -- a missing
    one means the environment is broken (e.g. hrx-system not built with
    ``LOOM_BUILD=ON``), so this raises rather than emitting a ``.envrc`` whose
    gaps would only surface later at link/compile/run.
    """
    found: dict[str, str] = {}
    missing: list[str] = []
    for env_name, filename in LOOM_TOOLS.items():
        match = next(
            (p for p in sorted(build_path.rglob(filename)) if p.is_file()), None
        )
        if match is None:
            missing.append(filename)
        else:
            found[env_name] = str(match)
    if missing:
        raise BenchEnvError(
            f"Loom tools not found under {build_path}: {', '.join(missing)}. "
            "Build hrx-system (with LOOM_BUILD=ON) before generating the bench .envrc."
        )
    return found


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

# Per-project venv with two editable installs (the shared build library and this
# bench), modeled on hrx-kernels-ws/.envrc. This is a *local* .venv owned by this
# build location -- it never creates or edits the workspace/root venv. Plain string
# with @PLACEHOLDER@ substitution to avoid f-string brace escaping around shell ${...}.
_VENV_BLOCK = """# --- per-project venv + editable installs ---
# Each build location gets its own self-contained .venv (you may want several in one
# repo). Both the shared build library (lib/python) and this bench checkout are
# editable-installed into it, so local changes to either are picked up live. The
# workspace/root venv is never touched.
if [ "${@SKIP@:-0}" != "1" ]; then
  if [ ! -f "$PWD/.venv/bin/activate" ]; then
    "${PYTHON:-python3}" -m venv "$PWD/.venv" --prompt "${PWD##*/}"
  fi

  source "$PWD/.venv/bin/activate"

  # Shared build library (buildlib/builds/assemblyline), editable.
  if ! python -c "import @LIB_IMPORT@" 2>/dev/null; then
    pip install -e "@LIB_PYTHON@" --config-settings editable_mode=compat
  fi
  # This bench checkout, editable, with extras.
  if ! python -c "import @PKG@" 2>/dev/null; then
    pip install -e "$PWD@EXTRAS@" --config-settings editable_mode=compat
  fi
fi"""


def _render_envrc(
    rocm_path: Path, loom_tools: dict[str, str], knobs: GgmlHrxKernelBenchEnvKnobs
) -> str:
    header = (
        "# Generated by builds.ggml_hrx_kernel_bench_env -- do not edit by hand.\n"
        "# Regenerate via skills/build-ggml-hrx-kernel-bench-env/scripts/ggml-hrx-kernel-bench.py.\n"
        "#\n"
        "# Wires the ROCm SDK and Loom tooling for ggml-hrx-kernel-bench and manages a\n"
        "# per-project venv (editable-installs the shared build library and this bench)."
    )

    # ROCM_PATH is what the bench propagates to the loom/iree tools (and feeds to
    # --rocm-path). bin + lib/llvm/bin give the ROCm/LLVM toolchain; lib +
    # rocm_sysdeps/lib cover the runtime libs the tools dlopen for `run`. (lib64 is
    # absent from TheRock SDKs, and GGML_HRX_ROCM_PATH is already mirrored from
    # ROCM_PATH by the bench's config.command_env -- both omitted.)
    rocm_block = (
        "# --- ROCm (from RocmInstallResult.rocm_path) ---\n"
        f'export ROCM_PATH="{rocm_path}"\n'
        'path_prepend PATH "$ROCM_PATH/bin"\n'
        'path_prepend PATH "$ROCM_PATH/lib/llvm/bin"\n'
        'path_prepend LD_LIBRARY_PATH "$ROCM_PATH/lib"\n'
        'path_prepend LD_LIBRARY_PATH "$ROCM_PATH/lib/rocm_sysdeps/lib"'
    )

    loom_lines: list[str] = []
    loom_dirs: list[str] = []
    for env_name, tool_path in loom_tools.items():
        loom_lines.append(f'export {env_name}="{tool_path}"')
        parent = os.path.dirname(tool_path)
        if parent not in loom_dirs:
            loom_dirs.append(parent)
    loom_body = "\n".join(loom_lines + [f'path_prepend PATH "{d}"' for d in loom_dirs])
    loom_block = (
        "# --- Loom tools (discovered in the HRX build tree; bench takes them via --flags) ---\n"
        f"{loom_body}\n"
        "# Loom binaries dynamically link ROCm; the LD_LIBRARY_PATH entries above cover them.\n"
        '# export LOOMC_HSA_RUNTIME_PATH="$ROCM_PATH/lib"'
    )

    extras = f"[{knobs.pip_extras}]" if knobs.pip_extras else ""
    venv_block = (
        _VENV_BLOCK.replace("@SKIP@", knobs.skip_venv_var)
        .replace("@LIB_IMPORT@", knobs.lib_import)
        .replace("@LIB_PYTHON@", str(_LIB_PYTHON))
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
            [header, _PATH_PREPEND_HELPER, rocm_block, loom_block, venv_block, watch_block]
        )
        + "\n"
    )
