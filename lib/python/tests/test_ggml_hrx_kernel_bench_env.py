from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from buildlib import BuildResult
from builds.ggml_hrx_kernel_bench_env import (
    GgmlHrxKernelBenchEnvKnobs,
    GgmlHrxKernelBenchEnvResult,
    build,
)
from builds.hrx_system import HrxSystemBuildResult, HrxSystemKnobs
from builds.rocm import PinnedTarballKnobs, RocmInstallResult, RocmProvider

LOOM_NAMES = ("loom-link", "loom-compile", "iree-benchmark-loom")


def _rocm_result(rocm_path: Path | None) -> RocmInstallResult:
    return RocmInstallResult(
        project="rocm",
        knobs=PinnedTarballKnobs(source_dir="/x/hrx", gfx_target="gfx1151"),
        source_path=Path("/x/hrx"),
        build_path=Path("/x/hrx/build"),
        provider=RocmProvider.PINNED_TARBALL,
        rocm_path=rocm_path,
        cache_path=Path("/c/7.14.0-gfx1151"),
        exit_code=0 if rocm_path is not None else 1,
        log="",
    )


def _hrx_result(build_path: Path) -> HrxSystemBuildResult:
    return HrxSystemBuildResult(
        project="hrx-system",
        knobs=HrxSystemKnobs(source_dir="/x/hrx"),
        source_path=Path("/x/hrx"),
        build_path=build_path,
        configure_exit_code=0,
        build_exit_code=0,
        install_exit_code=0,
        install_path=build_path / "install",
        log="",
    )


def _make_loom_tree(build_path: Path, names: tuple[str, ...]) -> None:
    # Mirror the real layout: <build>/loom/src/loom/tools/<name>/<name>.
    for name in names:
        tool_dir = build_path / "loom" / "src" / "loom" / "tools" / name
        tool_dir.mkdir(parents=True)
        exe = tool_dir / name
        exe.write_text("#!/bin/true\n")
        exe.chmod(0o755)


class BenchEnvBuildTests(unittest.TestCase):
    def test_writes_envrc_with_rocm_loom_and_venv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bench = tmp_path / "bench"
            bench.mkdir()
            hrx_build = tmp_path / "hrxbuild"
            _make_loom_tree(hrx_build, LOOM_NAMES)
            rocm_root = tmp_path / "rocm-root"  # need not exist on disk

            result = build(
                GgmlHrxKernelBenchEnvKnobs(source_dir=str(bench)),
                _rocm_result(rocm_root),
                _hrx_result(hrx_build),
            )

            self.assertIsInstance(result, BuildResult)
            self.assertTrue(result.written, msg=result.log)
            envrc = bench / ".envrc"
            self.assertEqual(result.envrc_path, envrc)
            text = envrc.read_text()

            # ROCm + helper + LD_LIBRARY_PATH composition
            self.assertIn("path_prepend()", text)
            self.assertIn(f'export ROCM_PATH="{rocm_root}"', text)
            self.assertIn('path_prepend PATH "$ROCM_PATH/lib/llvm/bin"', text)
            self.assertIn('path_prepend LD_LIBRARY_PATH "$ROCM_PATH/lib"', text)
            self.assertIn('path_prepend LD_LIBRARY_PATH "$ROCM_PATH/lib/rocm_sysdeps/lib"', text)
            # trimmed: lib64 is absent from the SDK, GGML_HRX_ROCM_PATH is redundant
            self.assertNotIn("GGML_HRX_ROCM_PATH", text)
            self.assertNotIn("lib64", text)
            # per-project venv (no shared/root venv) + two editable installs + watch_file
            self.assertIn('"${PYTHON:-python3}" -m venv "$PWD/.venv"', text)
            self.assertNotIn("source_up", text)  # each location owns its own venv
            # the shared build library, editable-installed into the per-project venv
            from builds.ggml_hrx_kernel_bench_env import _LIB_PYTHON

            self.assertIn('python -c "import buildlib"', text)
            self.assertIn(
                f'pip install -e "{_LIB_PYTHON}" --config-settings editable_mode=compat',
                text,
            )
            # the bench itself, editable with extras
            self.assertIn('python -c "import ggml_hrx_kernel_bench"', text)
            self.assertIn(
                'pip install -e "$PWD[numpy,dev]" --config-settings editable_mode=compat',
                text,
            )
            self.assertIn(f'watch_file "{rocm_root}"', text)
            # all three Loom tools discovered + exported
            self.assertEqual(set(result.loom_tools), {"LOOM_LINK", "LOOM_COMPILE", "IREE_BENCHMARK_LOOM"})
            for env_name, tool_path in result.loom_tools.items():
                self.assertIn(f'export {env_name}="{tool_path}"', text)
                self.assertTrue(tool_path.endswith(("loom-link", "loom-compile", "iree-benchmark-loom")))

    def test_missing_loom_tool_fails_fast(self) -> None:
        # A broken environment (some Loom tools absent) must fail at generation
        # time, not write a half-wired .envrc that breaks later at compile/run.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bench = tmp_path / "bench"
            bench.mkdir()
            hrx_build = tmp_path / "hrxbuild"
            _make_loom_tree(hrx_build, ("loom-link",))  # only one tool built

            result = build(
                GgmlHrxKernelBenchEnvKnobs(source_dir=str(bench)),
                _rocm_result(tmp_path / "rocm-root"),
                _hrx_result(hrx_build),
            )

            self.assertFalse(result.written)
            self.assertEqual(result.exit_code, 1)
            self.assertFalse((bench / ".envrc").exists())  # nothing written on failure
            self.assertIn("Loom tools not found", result.log)
            self.assertIn("loom-compile", result.log)
            self.assertIn("iree-benchmark-loom", result.log)

    def test_unbuilt_hrx_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bench = tmp_path / "bench"
            bench.mkdir()
            hrx_build = tmp_path / "hrxbuild"
            _make_loom_tree(hrx_build, LOOM_NAMES)
            hrx = _hrx_result(hrx_build)
            object.__setattr__(hrx, "build_exit_code", 1)  # compile failed -> not built

            result = build(
                GgmlHrxKernelBenchEnvKnobs(source_dir=str(bench)),
                _rocm_result(tmp_path / "rocm-root"),
                hrx,
            )

            self.assertFalse(result.written)
            self.assertEqual(result.exit_code, 1)
            self.assertFalse((bench / ".envrc").exists())
            self.assertIn("HRX build did not succeed", result.log)

    def test_no_rocm_path_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bench = tmp_path / "bench"
            bench.mkdir()
            hrx_build = tmp_path / "hrxbuild"
            _make_loom_tree(hrx_build, LOOM_NAMES)

            result = build(
                GgmlHrxKernelBenchEnvKnobs(source_dir=str(bench)),
                _rocm_result(None),
                _hrx_result(hrx_build),
            )

            self.assertFalse(result.written)
            self.assertEqual(result.exit_code, 1)
            self.assertFalse((bench / ".envrc").exists())
            self.assertIn("no rocm_path", result.log)

    def test_overwrite_false_preserves_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bench = tmp_path / "bench"
            bench.mkdir()
            (bench / ".envrc").write_text("OLD\n")
            hrx_build = tmp_path / "hrxbuild"
            _make_loom_tree(hrx_build, LOOM_NAMES)

            result = build(
                GgmlHrxKernelBenchEnvKnobs(source_dir=str(bench), overwrite=False),
                _rocm_result(tmp_path / "rocm-root"),
                _hrx_result(hrx_build),
            )

            self.assertFalse(result.written)
            self.assertEqual(result.exit_code, 1)
            self.assertEqual((bench / ".envrc").read_text(), "OLD\n")


class BenchEnvKnobsTests(unittest.TestCase):
    def test_as_dict_defaults(self) -> None:
        knobs = GgmlHrxKernelBenchEnvKnobs(source_dir="/x/bench")
        self.assertEqual(
            knobs.as_dict(),
            {
                "source_dir": "/x/bench",
                "pip_extras": "numpy,dev",
                "skip_venv_var": "GGML_HRX_BENCH_SKIP_VENV",
                "package_import": "ggml_hrx_kernel_bench",
                "lib_import": "buildlib",
                "overwrite": "true",
            },
        )

    def test_result_written_property(self) -> None:
        knobs = GgmlHrxKernelBenchEnvKnobs(source_dir="/x/bench")
        ok = GgmlHrxKernelBenchEnvResult(
            project="ggml-hrx-kernel-bench-env",
            knobs=knobs,
            source_path=Path("/x/bench"),
            build_path=Path("/x/bench"),
            envrc_path=Path("/x/bench/.envrc"),
            rocm_path=Path("/r"),
            exit_code=0,
        )
        self.assertTrue(ok.written)
        self.assertEqual(ok.as_prompt_input()["project"], "ggml-hrx-kernel-bench-env")


if __name__ == "__main__":
    unittest.main()
