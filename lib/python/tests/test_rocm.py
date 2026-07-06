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


def _resolved(url: str, version: str = "7.14.0"):
    """Patch :func:`builds.rocm._resolve_pin` to a fixed ``(url, version)``.

    The download/extract/cache/symlink mechanics tests care only about what the
    provider does *given* a resolved URL, so they pin one here (typically a local
    ``file://`` archive) instead of reaching through pins.json + URL building.
    """
    return mock.patch.object(rocm, "_resolve_pin", return_value=(url, version))


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
                "link_name": ".rocm",
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
    def test_repo_root_pins_json_carries_only_the_version(self) -> None:
        # pins.json holds just the pin -- the version that changes per bump. The
        # URL form and the gfx->token table are constants in code, not pin data.
        entry = load_pin_entry("rocm")
        self.assertTrue(entry["version"])
        self.assertNotIn("url_template", entry)
        self.assertNotIn("gfx_url_targets", entry)

    def test_committed_version_resolves_to_bundle_url(self) -> None:
        # The committed version (from pins.json) + the in-code table + URL form
        # must resolve a plain arch number to the bundle URL: 1201 -> gfx120X-all.
        version = load_pin_entry("rocm")["version"]
        knobs = PinnedTarballKnobs(source_dir="/x/repo", gfx_target="1201")
        url, resolved_version = rocm._resolve_pin(knobs)
        self.assertEqual(resolved_version, version)
        self.assertEqual(
            url,
            "https://rocm.nightlies.amd.com/tarball-multi-arch/"
            f"therock-dist-linux-gfx120X-all-{version}.tar.gz",
        )


class ResolvePinTests(unittest.TestCase):
    def test_builds_url_from_in_code_table_and_version(self) -> None:
        # Only the version comes from pins.json; the URL is assembled in code.
        knobs = PinnedTarballKnobs(source_dir="/x/repo", gfx_target="1201")
        with mock.patch.object(rocm, "load_pin_entry", return_value={"version": "9.9.9"}):
            url, version = rocm._resolve_pin(knobs)
        self.assertEqual(version, "9.9.9")
        self.assertEqual(
            url,
            "https://rocm.nightlies.amd.com/tarball-multi-arch/"
            "therock-dist-linux-gfx120X-all-9.9.9.tar.gz",
        )

    def test_standalone_family_uses_its_own_token(self) -> None:
        # 1151 is published standalone as gfx1151 (not a gfx115X family bundle):
        # proof the mapping is an explicit table, not a derived rule.
        knobs = PinnedTarballKnobs(source_dir="/x/repo", gfx_target="1151")
        with mock.patch.object(rocm, "load_pin_entry", return_value={"version": "9.9.9"}):
            url, _ = rocm._resolve_pin(knobs)
        self.assertIn("therock-dist-linux-gfx1151-9.9.9.tar.gz", url)

    def test_unmapped_gfx_fails_cleanly(self) -> None:
        # A gfx number with no row in the in-code table fails with an actionable
        # error naming the missing arch -- never fetching a guessed URL.
        knobs = PinnedTarballKnobs(source_dir="/x/repo", gfx_target="9999")
        with mock.patch.object(rocm, "load_pin_entry", return_value={"version": "9.9.9"}):
            with self.assertRaises(rocm.RocmInstallError) as cm:
                rocm._resolve_pin(knobs)
        message = str(cm.exception)
        self.assertIn("no ROCm tarball target for gfx '9999'", message)
        self.assertIn("_GFX_URL_TARGETS", message)

    def test_missing_version_fails_cleanly(self) -> None:
        knobs = PinnedTarballKnobs(source_dir="/x/repo", gfx_target="1201")
        with mock.patch.object(rocm, "load_pin_entry", return_value={}):
            with self.assertRaises(rocm.RocmInstallError) as cm:
                rocm._resolve_pin(knobs)
        self.assertIn("must define 'version'", str(cm.exception))


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

            with _resolved(archive.as_uri()):
                result = rocm.build(knobs)
                self.assertTrue(result.installed, msg=result.log)
                self.assertEqual(result.provider, RocmProvider.PINNED_TARBALL)

                link = repo / ".rocm"
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
            with _resolved("file:///nope/does-not-exist.tar.gz"):
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
            with _resolved(archive.as_uri()):
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
            with _resolved(arc_a.as_uri()):
                r1 = rocm.build(knobs)
            self.assertTrue(r1.installed, msg=r1.log)
            link = repo / ".rocm"
            self.assertTrue((link / "bin" / "marker_a").exists())

            with _resolved(arc_b.as_uri()):
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
            with _resolved(archive.as_uri()):
                r1 = rocm.build(knobs)
                self.assertTrue(r1.installed, msg=r1.log)

                install_dir = cache / f"7.14.0-{GFX}"
                shutil.rmtree(install_dir / "bin")
                shutil.rmtree(install_dir / "lib")
                self.assertTrue((install_dir / ".hrx-rocm-pin.json").exists())

                r2 = rocm.build(knobs)
            self.assertTrue(r2.installed, msg=r2.log)
            self.assertNotIn("Cached ROCm pin", r2.log)  # corruption detected -> re-extract
            self.assertTrue((repo / ".rocm" / "bin" / "amdclang++").exists())

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
            real = repo / ".rocm"
            real.mkdir(parents=True)
            (real / "stale").write_text("x")

            with _resolved(archive.as_uri()):
                result = rocm.build(
                    PinnedTarballKnobs(
                        source_dir=str(repo),
                        gfx_target=GFX,
                        cache_dir=str(tmp_path / "cache"),
                    )
                )
            self.assertTrue(result.installed, msg=result.log)
            link = repo / ".rocm"
            self.assertTrue(link.is_symlink())
            self.assertFalse((link / "stale").exists())


if __name__ == "__main__":
    unittest.main()
