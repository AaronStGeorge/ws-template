from __future__ import annotations

import shutil
import tarfile
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from buildlib import BuildKnobs, BuildResult, load_pin_entry
from builds import rocm
from builds.rocm import (
    PinnedTarballKnobs,
    RocmInstallKnobs,
    RocmInstallResult,
    RocmProvider,
)

GFX = "gfx1151"


def _make_fake_sdk(root: Path, wrapper: str | None = None) -> Path:
    """Create a minimal directory that looks like a ROCm SDK (bin/ + lib/).

    When ``wrapper`` is given the SDK is nested one level down, mimicking a
    tarball that wraps everything in a single top-level directory.
    """
    base = root / wrapper if wrapper else root
    (base / "bin").mkdir(parents=True)
    (base / "lib" / "llvm" / "bin").mkdir(parents=True)
    (base / "bin" / "amdclang++").write_text("#!/bin/true\n")
    (base / "lib" / "llvm" / "bin" / "clang").write_text("#!/bin/true\n")
    return base


def _tar_gz(source_dir: Path, archive: Path) -> None:
    with tarfile.open(archive, "w:gz") as tf:
        for child in sorted(source_dir.iterdir()):
            tf.add(child, arcname=child.name)


def _entry(url_template: str) -> dict:
    """A ``pins.json`` ``rocm`` entry as ``load_pin_entry`` would return it."""
    return {"version": "7.14.0", "url_template": url_template}


class RocmKnobsTests(unittest.TestCase):
    def test_gfx_target_is_required(self) -> None:
        with self.assertRaises(TypeError):
            PinnedTarballKnobs(source_dir="/x/repo")  # type: ignore[call-arg]

    def test_subclass_is_a_buildknobs(self) -> None:
        knobs = PinnedTarballKnobs(source_dir="/x/repo", gfx_target=GFX)
        self.assertIsInstance(knobs, BuildKnobs)
        self.assertIsInstance(knobs, RocmInstallKnobs)

    def test_provider_property(self) -> None:
        knobs = PinnedTarballKnobs(source_dir="/x/repo", gfx_target=GFX)
        self.assertEqual(knobs.provider, RocmProvider.PINNED_TARBALL)

    def test_as_dict_carries_gfx_and_pin_without_pins_file(self) -> None:
        knobs = PinnedTarballKnobs(source_dir="/x/repo", gfx_target=GFX)
        self.assertEqual(
            knobs.as_dict(),
            {
                "source_dir": "/x/repo",
                "gfx_target": "gfx1151",
                "pin": "rocm",
                "cache_dir": "",
                "link_name": "rocm-root",
            },
        )


class RocmResultTests(unittest.TestCase):
    def test_installed_reflects_exit_code_and_path(self) -> None:
        knobs = PinnedTarballKnobs(source_dir="/x/repo", gfx_target=GFX)
        ok = RocmInstallResult(
            project="rocm",
            knobs=knobs,
            source_path=Path("/x/repo"),
            build_path=Path("/x/repo/build"),
            provider=RocmProvider.PINNED_TARBALL,
            rocm_path=Path("/x/repo/build/rocm-root"),
            cache_path=Path("/c/7.14.0-gfx1151"),
            exit_code=0,
            log="",
        )
        self.assertIsInstance(ok, BuildResult)
        self.assertTrue(ok.installed)
        self.assertEqual(ok.as_prompt_input()["provider"], "pinned_tarball")

        failed = RocmInstallResult(
            project="rocm",
            knobs=knobs,
            source_path=Path("/x/repo"),
            build_path=Path("/x/repo/build"),
            provider=RocmProvider.PINNED_TARBALL,
            rocm_path=None,
            cache_path=None,
            exit_code=1,
            log="boom",
        )
        self.assertFalse(failed.installed)


class RocmBuildDispatchTests(unittest.TestCase):
    def test_unimplemented_provider_raises(self) -> None:
        @dataclass(frozen=True)
        class TheRockKnobs(RocmInstallKnobs):
            @property
            def provider(self) -> RocmProvider:
                return RocmProvider.THEROCK_SOURCE

        with self.assertRaises(NotImplementedError):
            rocm.build(TheRockKnobs(source_dir="/x/therock"))


class PinsTests(unittest.TestCase):
    def test_repo_root_pins_json_has_rocm_entry(self) -> None:
        # The committed pins.json at the repo root resolves and carries the
        # gfx-templated URL the README documents. The build always reads this
        # file -- the location is not configurable.
        entry = load_pin_entry("rocm")
        self.assertIn("{gfx}", entry["url_template"])
        self.assertIn("{version}", entry["url_template"])
        self.assertTrue(entry["version"])


class PinnedTarballBuildTests(unittest.TestCase):
    def test_download_verify_extract_link_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # A whole-SDK tarball that wraps the root in a single top-level dir.
            sdk_src = tmp_path / "sdk_src"
            _make_fake_sdk(sdk_src, wrapper="rocm-7.14.0")
            archive = tmp_path / "rocm.tar.gz"
            _tar_gz(sdk_src, archive)

            repo = tmp_path / "repo"
            repo.mkdir()
            knobs = PinnedTarballKnobs(
                source_dir=str(repo),
                gfx_target=GFX,
                cache_dir=str(tmp_path / "cache"),
            )

            entry = _entry(archive.as_uri())
            with mock.patch.object(rocm, "load_pin_entry", return_value=entry):
                result = rocm.build(knobs)
                self.assertTrue(result.installed, msg=result.log)
                self.assertEqual(result.provider, RocmProvider.PINNED_TARBALL)

                link = repo / "build" / "rocm-root"
                self.assertTrue(link.is_symlink())
                self.assertEqual(result.rocm_path, link)
                # Located the real root inside the wrapper directory.
                self.assertTrue((link / "bin" / "amdclang++").exists())

                # Second build with the same pin reuses the cache (no re-extract).
                again = rocm.build(knobs)
                self.assertTrue(again.installed, msg=again.log)
                self.assertIn("Cached ROCm pin", again.log)

    def test_download_failure_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            entry = _entry("file:///nope/does-not-exist.tar.gz")
            with mock.patch.object(rocm, "load_pin_entry", return_value=entry):
                result = rocm.build(
                    PinnedTarballKnobs(
                        source_dir=str(repo),
                        gfx_target=GFX,
                        cache_dir=str(tmp_path / "cache"),
                    )
                )
            self.assertFalse(result.installed)
            self.assertEqual(result.exit_code, 1)
            self.assertIn("download failed", result.log)

    def test_gfx_target_templated_into_url(self) -> None:
        # The URL the provider fetches is the pin template with the gfx input
        # substituted -- proving gfx is an input, not read from the pin.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            entry = _entry("file:///nope/therock-dist-linux-{gfx}-{version}.tar.gz")
            with mock.patch.object(rocm, "load_pin_entry", return_value=entry):
                result = rocm.build(
                    PinnedTarballKnobs(
                        source_dir=str(repo),
                        gfx_target="gfx1100",
                        cache_dir=str(tmp_path / "cache"),
                    )
                )
            self.assertFalse(result.installed)  # file does not exist
            self.assertIn("therock-dist-linux-gfx1100-7.14.0.tar.gz", result.log)

    def test_unknown_pin_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            with mock.patch.object(rocm, "load_pin_entry", side_effect=KeyError("rocm")):
                result = rocm.build(
                    PinnedTarballKnobs(
                        source_dir=str(repo),
                        gfx_target=GFX,
                        cache_dir=str(tmp_path / "cache"),
                    )
                )
            self.assertFalse(result.installed)
            self.assertEqual(result.exit_code, 1)
            self.assertIn("KeyError", result.log)
            self.assertIn("rocm", result.log)

    def test_incomplete_pin_fails_cleanly(self) -> None:
        for entry in (
            {},
            {"version": "7.14.0"},
            {"url_template": "file:///x-{gfx}-{version}.tar.gz"},
        ):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                repo = tmp_path / "repo"
                repo.mkdir()
                with mock.patch.object(rocm, "load_pin_entry", return_value=entry):
                    result = rocm.build(
                        PinnedTarballKnobs(
                            source_dir=str(repo),
                            gfx_target=GFX,
                            cache_dir=str(tmp_path / "cache"),
                        )
                    )
                self.assertFalse(result.installed, msg=f"entry={entry}")
                self.assertIn("must define", result.log)

    def test_malformed_url_template_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            # Unknown placeholder -> .format raises -> clear pin diagnostic.
            entry = {"version": "7.14.0", "url_template": "file:///x-{nope}.tar.gz"}
            with mock.patch.object(rocm, "load_pin_entry", return_value=entry):
                result = rocm.build(
                    PinnedTarballKnobs(
                        source_dir=str(repo),
                        gfx_target=GFX,
                        cache_dir=str(tmp_path / "cache"),
                    )
                )
            self.assertFalse(result.installed)
            self.assertIn("url_template is malformed", result.log)

    def test_unlocatable_sdk_fails_cleanly(self) -> None:
        # A tarball with two top-level dirs has no single SDK root to descend into.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            (src / "alpha").mkdir(parents=True)
            (src / "beta").mkdir(parents=True)
            (src / "alpha" / "f").write_text("x")
            (src / "beta" / "f").write_text("x")
            archive = tmp_path / "rocm.tar.gz"
            _tar_gz(src, archive)

            repo = tmp_path / "repo"
            repo.mkdir()
            with mock.patch.object(rocm, "load_pin_entry", return_value=_entry(archive.as_uri())):
                result = rocm.build(
                    PinnedTarballKnobs(
                        source_dir=str(repo),
                        gfx_target=GFX,
                        cache_dir=str(tmp_path / "cache"),
                    )
                )
            self.assertFalse(result.installed)
            self.assertIn("could not locate a ROCm SDK root", result.log)

    def test_pin_bump_reextracts(self) -> None:
        # A changed URL for the same version/gfx cache key must invalidate the
        # cache and re-extract the new tarball (covers _reset_dir's rmtree branch).
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sdk_a = tmp_path / "a"
            _make_fake_sdk(sdk_a)
            (sdk_a / "bin" / "marker_a").write_text("a")
            arc_a = tmp_path / "a.tar.gz"
            _tar_gz(sdk_a, arc_a)

            sdk_b = tmp_path / "b"
            _make_fake_sdk(sdk_b)
            (sdk_b / "bin" / "marker_b").write_text("b")
            arc_b = tmp_path / "b.tar.gz"
            _tar_gz(sdk_b, arc_b)

            repo = tmp_path / "repo"
            repo.mkdir()
            knobs = PinnedTarballKnobs(
                source_dir=str(repo), gfx_target=GFX, cache_dir=str(tmp_path / "cache")
            )
            with mock.patch.object(rocm, "load_pin_entry", return_value=_entry(arc_a.as_uri())):
                r1 = rocm.build(knobs)
            self.assertTrue(r1.installed, msg=r1.log)
            link = repo / "build" / "rocm-root"
            self.assertTrue((link / "bin" / "marker_a").exists())

            with mock.patch.object(rocm, "load_pin_entry", return_value=_entry(arc_b.as_uri())):
                r2 = rocm.build(knobs)
            self.assertTrue(r2.installed, msg=r2.log)
            self.assertNotIn("Cached ROCm pin", r2.log)  # url mismatch -> re-extract
            self.assertTrue((link / "bin" / "marker_b").exists())
            self.assertFalse((link / "bin" / "marker_a").exists())

    def test_corrupted_cache_self_heals(self) -> None:
        # If the SDK tree is evicted but the marker survives, the next build must
        # detect the corruption and re-extract rather than trust the marker.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sdk = tmp_path / "sdk"
            _make_fake_sdk(sdk)
            archive = tmp_path / "rocm.tar.gz"
            _tar_gz(sdk, archive)

            repo = tmp_path / "repo"
            repo.mkdir()
            cache = tmp_path / "cache"
            knobs = PinnedTarballKnobs(
                source_dir=str(repo), gfx_target=GFX, cache_dir=str(cache)
            )
            with mock.patch.object(rocm, "load_pin_entry", return_value=_entry(archive.as_uri())):
                r1 = rocm.build(knobs)
                self.assertTrue(r1.installed, msg=r1.log)

                install_dir = cache / f"7.14.0-{GFX}"
                shutil.rmtree(install_dir / "bin")
                shutil.rmtree(install_dir / "lib")
                self.assertTrue((install_dir / ".hrx-rocm-pin.json").exists())

                r2 = rocm.build(knobs)
            self.assertTrue(r2.installed, msg=r2.log)
            self.assertNotIn("Cached ROCm pin", r2.log)  # corruption detected -> re-extract
            self.assertTrue((repo / "build" / "rocm-root" / "bin" / "amdclang++").exists())

    def test_symlink_replaces_existing_real_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sdk = tmp_path / "sdk"
            _make_fake_sdk(sdk)
            archive = tmp_path / "rocm.tar.gz"
            _tar_gz(sdk, archive)

            repo = tmp_path / "repo"
            repo.mkdir()
            # A pre-existing real directory sits where the symlink should go.
            real = repo / "build" / "rocm-root"
            real.mkdir(parents=True)
            (real / "stale").write_text("x")

            with mock.patch.object(rocm, "load_pin_entry", return_value=_entry(archive.as_uri())):
                result = rocm.build(
                    PinnedTarballKnobs(
                        source_dir=str(repo),
                        gfx_target=GFX,
                        cache_dir=str(tmp_path / "cache"),
                    )
                )
            self.assertTrue(result.installed, msg=result.log)
            link = repo / "build" / "rocm-root"
            self.assertTrue(link.is_symlink())
            self.assertFalse((link / "stale").exists())


if __name__ == "__main__":
    unittest.main()
