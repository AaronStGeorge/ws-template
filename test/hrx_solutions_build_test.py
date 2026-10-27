"""Regression coverage for installing read-only Bazel output repeatedly."""
from pathlib import Path
import tempfile
import unittest

from builds.hrx_solutions import _install_file


class InstallTests(unittest.TestCase):
    def test_reinstall_replaces_read_only_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bazel-output"
            target = root / "installed"
            source.write_bytes(b"first build")
            source.chmod(0o555)
            _install_file(source, target)
            replacement = root / "new-output"
            replacement.write_bytes(b"second build")
            replacement.chmod(0o555)
            _install_file(replacement, target)
            self.assertEqual(target.read_bytes(), b"second build")
            self.assertEqual(target.stat().st_mode & 0o777, 0o555)
