"""Bundle discovery must survive exec, renaming, and paths containing quotes."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import thonny


class MacBundleTest(unittest.TestCase):
    def test_launcher_bundle_survives_python_exec(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Student's Thonny AIA.app"
            app.mkdir()
            with patch.object(thonny.sys, "platform", "darwin"), patch.dict(
                os.environ, {"THONNY_APP_PATH": str(app)}
            ), patch.object(thonny, "_get_orig_argv", return_value=["/embedded/bin/python"]):
                self.assertEqual(thonny._get_macos_app_path(), str(app))

    def test_legacy_launcher_accepts_renamed_bundle(self):
        with patch.object(thonny.sys, "platform", "darwin"), patch.dict(
            os.environ, {"THONNY_APP_PATH": ""}
        ), patch.object(
            thonny,
            "_get_orig_argv",
            return_value=["/Applications/Thonny AIA.app/Contents/MacOS/thonny"],
        ):
            self.assertEqual(thonny._get_macos_app_path(), "/Applications/Thonny AIA.app")

    def test_non_bundle_python_does_not_restart(self):
        with patch.object(thonny.sys, "platform", "darwin"), patch.dict(
            os.environ, {"THONNY_APP_PATH": "/nonexistent.app"}
        ), patch.object(thonny, "_get_orig_argv", return_value=["/usr/bin/python3"]):
            self.assertIsNone(thonny._get_macos_app_path())

    def test_other_platform_ignores_bundle_environment(self):
        with patch.object(thonny.sys, "platform", "win32"):
            self.assertIsNone(thonny._get_macos_app_path())
