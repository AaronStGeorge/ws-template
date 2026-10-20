"""The sourceset package against real git: read a manifest, load it, check ``sources/``.

A temporary workspace holds one sourceset naming a local repository at a
tagged ref, with one asset. The "remote" is a path, so no network is
needed, and nothing outside the temporary directory is touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from sourceset import SOURCES_DIR, load_sourceset, read_sourceset

# Commits and tags need an identity and must not wait on a signing key.
GIT = [
    "git",
    "-c", "user.email=test@example.com",
    "-c", "user.name=test",
    "-c", "commit.gpgsign=false",
    "-c", "tag.gpgsign=false",
]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [*GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def write_manifest(sourcesets_dir: Path, name: str, repos: list[dict[str, str]]) -> Path:
    directory = sourcesets_dir / name
    directory.mkdir(parents=True)
    (directory / "sourceset.json").write_text(json.dumps({"repos": repos}))
    return directory


class SourcesetTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sourceset-test-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

        # The repository the sourceset clones: two commits, a tag on the first.
        self.upstream = self.root / "upstream" / "thing.git"
        self.upstream.mkdir(parents=True)
        git(self.upstream, "init", "-q", "-b", "main")
        (self.upstream / "first").write_text("1\n")
        git(self.upstream, "add", "first")
        git(self.upstream, "commit", "-q", "-m", "first")
        git(self.upstream, "tag", "v1")
        (self.upstream / "second").write_text("2\n")
        git(self.upstream, "add", "second")
        git(self.upstream, "commit", "-q", "-m", "second")

        # The workspace: one sourceset in the default place, with one asset.
        self.workspace = self.root / "workspace"
        self.sourcesets = self.workspace / "sourcesets"
        sample = write_manifest(self.sourcesets, "sample", [{"url": str(self.upstream), "ref": "v1"}])
        self.assets = sample / "assets"
        self.assets.mkdir()
        (self.assets / "build.py").write_text("print('asset')\n")

    def test_read_then_load_clones_at_the_ref_and_links_the_asset(self):
        chosen = read_sourceset(self.sourcesets, "sample")
        self.assertEqual(chosen.name, "sample")
        self.assertEqual(chosen.assets, self.assets)

        with open(os.devnull, "w") as log:
            sources = load_sourceset(chosen, self.workspace, log)

        self.assertEqual(sources, self.workspace / SOURCES_DIR)
        clone = sources / "thing"
        self.assertTrue((clone / "first").is_file(), "the clone is named after the url's last segment, without .git")
        self.assertFalse((clone / "second").exists(), "checked out at the tagged ref")
        self.assertEqual(git(clone, "rev-parse", "HEAD"), git(self.upstream, "rev-parse", "v1"))

        link = sources / "build.py"
        self.assertTrue(link.is_symlink())
        self.assertFalse(os.path.isabs(os.readlink(link)), "relative, so a moved workspace keeps its links")
        self.assertEqual(link.resolve(), (self.assets / "build.py").resolve())
        self.assertEqual(link.read_text(), "print('asset')\n")

    def test_a_sourceset_without_assets_has_none(self):
        write_manifest(self.sourcesets, "bare", [])
        self.assertIsNone(read_sourceset(self.sourcesets, "bare").assets)

    def test_a_sourceset_is_read_from_the_directory_given_and_nowhere_else(self):
        elsewhere = self.root / "elsewhere"
        write_manifest(elsewhere, "other", [])
        self.assertEqual(read_sourceset(elsewhere, "other").name, "other")
        with self.assertRaises(FileNotFoundError):
            read_sourceset(elsewhere, "sample")

    def test_read_refuses_a_name_that_is_not_one_before_looking_for_it(self):
        with self.assertRaises(ValueError):
            read_sourceset(self.sourcesets, "../sample")
        with self.assertRaises(ValueError):
            read_sourceset(self.sourcesets, "Sample")
        with self.assertRaises(FileNotFoundError):
            read_sourceset(self.sourcesets, "missing")


if __name__ == "__main__":
    unittest.main()
