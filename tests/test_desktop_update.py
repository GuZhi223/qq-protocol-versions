"""Offline regression tests for the daily desktop updater."""

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update-qqnt-desktop.py"
SPEC = importlib.util.spec_from_file_location("desktop_updater", SCRIPT)
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)


class DesktopDownloadTests(unittest.TestCase):
    def completed(self, code=0, status=200):
        return subprocess.CompletedProcess([], code, str(status), "test error" if code else "")

    def test_timeouts_and_https_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "package.exe"

            def download(command, **kwargs):
                path.write_bytes(b"package")
                self.assertEqual(kwargs["timeout"], 615)
                for flag, value in (
                    ("--connect-timeout", "30"), ("--max-time", "600"),
                    ("--proto", "=https"), ("--proto-redir", "=https"),
                ):
                    self.assertEqual(command[command.index(flag) + 1], value)
                self.assertNotIn("--insecure", command)
                return self.completed()

            with patch.object(updater.shutil, "which", return_value="curl"), patch.object(
                updater.subprocess, "run", side_effect=download
            ):
                updater.download_resumable("https://example.test/package.exe", path)
            self.assertEqual(path.read_bytes(), b"package")

    def test_interrupted_download_resumes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "package.exe"
            calls = []

            def download(command, **kwargs):
                calls.append(command)
                if len(calls) == 1:
                    path.write_bytes(b"part")
                    return self.completed(18, 200)
                self.assertEqual(command[command.index("--continue-at") + 1], "4")
                with path.open("ab") as stream:
                    stream.write(b"rest")
                return self.completed(0, 206)

            with (
                patch.object(updater.shutil, "which", return_value="curl"),
                patch.object(updater.subprocess, "run", side_effect=download),
                patch.object(updater.time, "sleep") as sleep,
            ):
                updater.download_resumable("https://example.test/package.exe", path)
                sleep.assert_called_once_with(3)
            self.assertEqual(path.read_bytes(), b"partrest")

    def test_ignored_range_restarts_without_duplicate_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "package.exe"
            path.write_bytes(b"partial")
            calls = []

            def download(command, **kwargs):
                calls.append(command)
                if len(calls) == 1:
                    self.assertIn("--continue-at", command)
                    return self.completed(33, 200)
                self.assertNotIn("--continue-at", command)
                self.assertFalse(path.exists())
                path.write_bytes(b"complete-package")
                return self.completed()

            with (
                patch.object(updater.shutil, "which", return_value="curl"),
                patch.object(updater.subprocess, "run", side_effect=download),
                patch.object(updater.time, "sleep"),
            ):
                updater.download_resumable("https://example.test/package.exe", path)
            self.assertEqual(path.read_bytes(), b"complete-package")

    def test_permanent_errors_and_unexpected_status_fail_fast(self):
        for code, status in ((22, 403), (22, 404), (60, 0), (0, 206)):
            with self.subTest(code=code, status=status), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "package.exe"

                def download(*args, **kwargs):
                    path.write_bytes(b"partial")
                    return self.completed(code, status)

                with (
                    patch.object(updater.shutil, "which", return_value="curl"),
                    patch.object(updater.subprocess, "run", side_effect=download) as run,
                    patch.object(updater.time, "sleep") as sleep,
                ):
                    with self.assertRaises(RuntimeError):
                        updater.download_resumable("https://example.test/package.exe", path)
                    self.assertEqual(run.call_count, 1)
                    sleep.assert_not_called()
                self.assertFalse(path.exists())

    def test_transient_http_failure_retries(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "package.exe"
            results = iter((self.completed(22, 503), self.completed()))

            def download(*args, **kwargs):
                result = next(results)
                if result.returncode == 0:
                    path.write_bytes(b"package")
                return result

            with (
                patch.object(updater.shutil, "which", return_value="curl"),
                patch.object(updater.subprocess, "run", side_effect=download) as run,
                patch.object(updater.time, "sleep"),
            ):
                updater.download_resumable("https://example.test/package.exe", path)
                self.assertEqual(run.call_count, 2)

    def test_retry_exhaustion_removes_partial_file(self):
        for result in (self.completed(28, 200), subprocess.TimeoutExpired("curl", 615)):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "package.exe"

                def download(*args, **kwargs):
                    path.write_bytes(b"partial")
                    if isinstance(result, Exception):
                        raise result
                    return result

                with (
                    patch.object(updater.shutil, "which", return_value="curl"),
                    patch.object(updater.subprocess, "run", side_effect=download) as run,
                    patch.object(updater.time, "sleep") as sleep,
                ):
                    with self.assertRaises(RuntimeError):
                        updater.download_resumable("https://example.test/package.exe", path)
                    self.assertEqual(run.call_count, 2)
                    sleep.assert_called_once_with(3)
                self.assertFalse(path.exists())

    def test_empty_success_is_not_a_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "package.exe"
            with (
                patch.object(updater.shutil, "which", return_value="curl"),
                patch.object(updater.subprocess, "run", return_value=self.completed()),
            ):
                with self.assertRaises(RuntimeError):
                    updater.download_resumable("https://example.test/package.exe", path)


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
