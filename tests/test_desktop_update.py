"""Offline regression tests for the daily desktop updater."""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update-qqnt-desktop.py"
SPEC = importlib.util.spec_from_file_location("desktop_updater", SCRIPT)
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)


class DesktopUpdateTests(unittest.TestCase):
    def run_update(self, targets, results, output_dir, *, strict=True):
        def fake_download(url, destination):
            destination.write_bytes(b"test-package")

        def fake_extract(package, source_url=None):
            value = results[source_url]
            if isinstance(value, Exception):
                raise value
            return dict(value)

        arguments = [str(SCRIPT), "--output-dir", str(output_dir)]
        if strict:
            arguments.append("--strict")
        with (
            patch.object(sys, "argv", arguments),
            patch.object(updater, "load_config", return_value=({}, "test-config")),
            patch.object(updater, "target_packages", return_value=targets),
            patch.object(updater, "find_napcat_asset", return_value=None),
            patch.object(updater, "find_mirror_asset", return_value=None),
            patch.object(updater, "download_resumable", side_effect=fake_download),
            patch.object(updater, "extract", side_effect=fake_extract),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return updater.main()

    @staticmethod
    def result(platform, architectures):
        return {
            "platform": platform,
            "architectures": architectures,
            "version": "9.9.36-12345",
            "subid": 123456789,
        }

    def test_all_targets_and_index(self):
        targets = [
            ("windows", "x64", ".exe", "test-windows"),
            ("linux", "mips64el", ".deb", "test-linux"),
            ("macos", "universal", ".dmg", "test-macos"),
        ]
        results = {
            "test-windows": self.result("windows", ["x64"]),
            "test-linux": self.result("linux", ["mips"]),
            "test-macos": self.result("macos", ["arm64", "x64"]),
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "desktop"
            self.assertEqual(self.run_update(targets, results, output), 0)
            index = json.loads((output / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(len(index["entries"]), 3)
            self.assertEqual(
                {entry["architecture"] for entry in index["entries"]},
                {"x64", "mips64el", "universal"},
            )

    def test_rejected_package_never_written(self):
        target = [("windows", "arm64", ".exe", "test-package")]
        for invalid in (
            self.result("windows", ["x64"]),
            self.result("linux", ["arm64"]),
        ):
            with self.subTest(result=invalid), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "desktop"
                with self.assertRaises(RuntimeError):
                    self.run_update(target, {"test-package": invalid}, output)
                self.assertFalse(output.exists())

    def test_macos_requires_both_architectures(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "desktop"
            with self.assertRaises(RuntimeError):
                self.run_update(
                    [("macos", "universal", ".dmg", "test-macos")],
                    {"test-macos": self.result("macos", ["x64"])},
                    output,
                )
            self.assertFalse(output.exists())

    def test_partial_success_has_index_before_strict_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "desktop"
            with self.assertRaises(RuntimeError):
                self.run_update(
                    [
                        ("windows", "arm64", ".exe", "bad"),
                        ("linux", "x64", ".deb", "good"),
                    ],
                    {
                        "bad": ValueError("invalid package"),
                        "good": self.result("linux", ["x64"]),
                    },
                    output,
                )
            index = json.loads((output / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(len(index["entries"]), 1)
            self.assertEqual(index["entries"][0]["platform"], "linux")
            self.assertFalse((output / "windows").exists())


if __name__ == "__main__":
    unittest.main()
