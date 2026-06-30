from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from buildlib import BuildKnobs, BuildResult, build_dir, resolve_source_dir
from builds import hrx_system
from builds.hrx_system import HrxSystemBuildResult, HrxSystemKnobs
from builds.hrx_system import _already_configured, _write_configure_marker
from builds.hrx_system import _cmake_targets as hrx_cmake_targets
from builds.rocm import PinnedTarballKnobs, RocmInstallResult, RocmProvider
from builds.toy_ml import ToyMlBuildResult, ToyMlKnobs


class BuildKnobsTests(unittest.TestCase):
    def test_source_dir_is_required(self) -> None:
        with self.assertRaises(TypeError):
            ToyMlKnobs()  # type: ignore[call-arg]

    def test_subclass_is_a_buildknobs(self) -> None:
        self.assertIsInstance(ToyMlKnobs(source_dir="/x/toy"), BuildKnobs)

    def test_as_dict_stringifies_typed_fields(self) -> None:
        knobs = ToyMlKnobs(source_dir="/x/toy", build_type="Release", jobs=4)
        self.assertEqual(
            knobs.as_dict(),
            {"source_dir": "/x/toy", "build_type": "Release", "jobs": "4"},
        )

    def test_as_dict_handles_bool_and_enum(self) -> None:
        class Mode(Enum):
            FAST = "fast"

        @dataclass(frozen=True)
        class K(BuildKnobs):
            flag: bool = True
            mode: Mode = Mode.FAST

        self.assertEqual(
            K(source_dir="/x").as_dict(),
            {"source_dir": "/x", "flag": "true", "mode": "fast"},
        )


class PathHelperTests(unittest.TestCase):
    def test_build_dir(self) -> None:
        self.assertEqual(build_dir(Path("/x/toy")), Path("/x/toy/build"))

    def test_resolve_source_dir_is_absolute(self) -> None:
        knobs = ToyMlKnobs(source_dir="skills/assemblyline/examples/toy-tasks/toy-ml")
        resolved = resolve_source_dir(knobs)
        self.assertTrue(resolved.is_absolute())
        self.assertEqual(resolved.name, "toy-ml")


class BuildResultTests(unittest.TestCase):
    def test_subclasses_base_and_carries_knobs(self) -> None:
        knobs = ToyMlKnobs(source_dir="/x/toy")
        result = ToyMlBuildResult(
            project="toy-ml",
            knobs=knobs,
            source_path=Path("/x/toy"),
            build_path=Path("/x/toy/build"),
            configure_exit_code=0,
            build_exit_code=0,
            log="",
        )
        self.assertIsInstance(result, BuildResult)
        self.assertIs(result.knobs, knobs)
        self.assertTrue(result.built)


class HrxSystemKnobsTests(unittest.TestCase):
    def test_source_dir_is_required(self) -> None:
        with self.assertRaises(TypeError):
            HrxSystemKnobs()  # type: ignore[call-arg]

    def test_subclass_is_a_buildknobs(self) -> None:
        self.assertIsInstance(HrxSystemKnobs(source_dir="/x/hrx"), BuildKnobs)

    def test_as_dict_stringifies_typed_fields(self) -> None:
        knobs = HrxSystemKnobs(
            source_dir="/x/hrx",
            gfx_targets="gfx1151",
            jobs=8,
            loom_build=False,
        )
        self.assertEqual(
            knobs.as_dict(),
            {
                "source_dir": "/x/hrx",
                "gfx_targets": "gfx1151",
                "build_type": "RelWithDebInfo",
                "jobs": "8",
                "loom_build": "false",
                "install": "true",
                "install_tests": "false",
            },
        )

    def test_cmake_targets_normalizes_separators(self) -> None:
        self.assertEqual(hrx_cmake_targets("gfx1151, gfx1100; gfx1201"), "gfx1151;gfx1100;gfx1201")


class HrxConfigureSkipTests(unittest.TestCase):
    def test_already_configured_matches_recorded_argv(self) -> None:
        argv = ["cmake", "-S", "/x/hrx", "-B", "/x/hrx/build", "-DA=1"]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            # No cache yet -> not configured.
            self.assertFalse(_already_configured(out, argv))
            _write_configure_marker(out, argv)
            # Marker present but no CMakeCache.txt -> still not trusted.
            self.assertFalse(_already_configured(out, argv))
            (out / "CMakeCache.txt").write_text("x")
            # Cache + matching marker -> configured (skip).
            self.assertTrue(_already_configured(out, argv))
            # A changed configure command line invalidates the skip.
            self.assertFalse(_already_configured(out, argv + ["-DB=2"]))

    def test_build_requires_installed_rocm(self) -> None:
        # A failed ROCm install (rocm_path=None) is a precondition violation: the
        # HRX build refuses to run rather than configure against a missing SDK.
        failed_rocm = RocmInstallResult(
            project="rocm",
            knobs=PinnedTarballKnobs(source_dir="/x/hrx", gfx_target="gfx1151"),
            source_path=Path("/x/hrx"),
            build_path=Path("/x/hrx/build"),
            provider=RocmProvider.PINNED_TARBALL,
            rocm_path=None,
            cache_path=None,
            exit_code=1,
            log="boom",
        )
        with self.assertRaises(ValueError):
            hrx_system.build(HrxSystemKnobs(source_dir="/x/hrx"), failed_rocm)


class HrxSystemBuildResultTests(unittest.TestCase):
    def test_built_and_installed_reflect_exit_codes(self) -> None:
        knobs = HrxSystemKnobs(source_dir="/x/hrx")
        result = HrxSystemBuildResult(
            project="hrx-system",
            knobs=knobs,
            source_path=Path("/x/hrx"),
            build_path=Path("/x/hrx/build"),
            configure_exit_code=0,
            build_exit_code=0,
            install_exit_code=0,
            install_path=Path("/x/hrx/build/install"),
            log="",
        )
        self.assertIsInstance(result, BuildResult)
        self.assertIs(result.knobs, knobs)
        self.assertTrue(result.built)
        self.assertTrue(result.installed)

    def test_built_is_false_when_compile_fails(self) -> None:
        knobs = HrxSystemKnobs(source_dir="/x/hrx")
        result = HrxSystemBuildResult(
            project="hrx-system",
            knobs=knobs,
            source_path=Path("/x/hrx"),
            build_path=Path("/x/hrx/build"),
            configure_exit_code=0,
            build_exit_code=1,
            install_exit_code=None,
            install_path=None,
            log="",
        )
        self.assertFalse(result.built)
        self.assertFalse(result.installed)


if __name__ == "__main__":
    unittest.main()
