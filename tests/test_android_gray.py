"""Offline coverage for public-feed discovery and isolated gray data."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update-android-gray.py"
SPEC = importlib.util.spec_from_file_location("gray_updater", SCRIPT)
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)
APK_URL = "https://downv6.qq.com/qqweb/QQ_1/android_apk/test.apk"


def message(post=729, version="9.3.75.42245", url=APK_URL, marker="#Beta"):
    return f'''<div data-post="qq_updatelog/{post}"><div class="tgme_widget_message_text js-message_text">
    QQ_NT {version} <a href="?q=%23Beta">{marker}</a><br/>
    <a href="{url}">Download</a><a href="{url}">Duplicate</a></div>
    <footer>9.9.36.12345 #Beta</footer></div>'''


class AndroidGrayTests(unittest.TestCase):
    def test_public_beta_discovery_is_not_a_hardcoded_version(self):
        candidates = updater.parse_candidates(message(version="9.3.80.43000"))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["version"], "9.3.80.43000")
        self.assertEqual(candidates[0]["announcement_url"], "https://t.me/qq_updatelog/729")

    def test_untrusted_urls_and_non_beta_posts_are_ignored(self):
        for url in (
            "http://downv6.qq.com/qqweb/QQ_1/android_apk/test.apk",
            "https://downv6.qq.com.evil.example/qqweb/QQ_1/android_apk/test.apk",
            "https://attacker@downv6.qq.com/qqweb/QQ_1/android_apk/test.apk",
            "https://downv6.qq.com/qqweb/QQ_1/android_apk/%2e%2e/test.apk",
            "https://downv6.qq.com/qqweb/TIM/android_apk/test.apk",
            APK_URL + "?redirect=evil",
        ):
            with self.subTest(url=url):
                self.assertFalse(updater.trusted_apk_url(url))
                self.assertEqual(updater.parse_candidates(message(url=url)), [])
        self.assertEqual(updater.parse_candidates(message(marker="#Release")), [])

    def test_ambiguous_versions_and_layout_failures(self):
        self.assertEqual(updater.parse_candidates(message(version="9.3.75.42245 9.3.80.43000")), [])
        with self.assertRaises(RuntimeError):
            updater.parse_candidates("<html>Unavailable</html>")

    def test_versions_are_numeric(self):
        candidates = updater.parse_candidates(message(version="9.3.75.42245") + message(post=730, version="9.3.100.45000"))
        self.assertEqual(candidates[0]["version"], "9.3.100.45000")

    def create_record(self, root):
        version = "9.3.75.42245"
        phone = {"apk_id": "com.tencent.mobileqq", "protocol_type": 1, "sort_version_name": version, "sub_app_id": 537397314}
        pad = dict(phone, protocol_type=6, sub_app_id=537397325)
        phone_path = root / "android_gray" / "android_phone" / f"{version}.json"
        pad_path = root / "android_gray" / "android_pad" / f"{version}.json"
        updater.write_json(phone_path, phone)
        updater.write_json(pad_path, pad)
        updater.write_json(root / "android_gray" / "sources" / f"{version}.json", {
            "channel": "gray", "full_version": version, "manifest_channel": "GrayOne",
            "apk": {"url": APK_URL, "version_code": 16538},
            "outputs": {"android_phone_sha256": updater.sha256_file(phone_path), "android_pad_sha256": updater.sha256_file(pad_path)},
        })

    def test_index_and_duplicate_skip_preserve_stable_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stable_path = root / "android_phone" / "9.3.70.json"
            updater.write_json(stable_path, {"sort_version_name": "9.3.70.41925"})
            stable_before = stable_path.read_bytes()
            self.create_record(root)
            updater.rebuild_index(root)
            index_path = root / "android_gray" / "index.json"
            index_before = index_path.read_bytes()
            index = json.loads(index_before)
            self.assertEqual(index["latest_version"], "9.3.75.42245")
            self.assertEqual(index["entries"][0]["phone_subid"], 537397314)
            phone_export = root / "android_phone" / "9.3.75.42245.json"
            pad_export = root / "android_pad" / "9.3.75.42245.json"
            phone = updater.read_json(phone_export)
            pad = updater.read_json(pad_export)
            self.assertEqual(phone["channel"], "gray")
            self.assertEqual(phone["manifest_channel"], "GrayOne")
            self.assertEqual(phone["sort_version_name"], "9.3.75.42245")
            self.assertEqual(pad["sub_app_id"], 537397325)
            self.assertEqual(phone["source_path"], "android_gray/sources/9.3.75.42245.json")
            export_before = (phone_export.read_bytes(), pad_export.read_bytes())
            self.assertEqual(updater.select_pending(updater.parse_candidates(message()), root), [])
            updater.rebuild_index(root)
            self.assertEqual(index_path.read_bytes(), index_before)
            self.assertEqual(stable_path.read_bytes(), stable_before)
            self.assertEqual((phone_export.read_bytes(), pad_export.read_bytes()), export_before)

    def test_compatibility_export_never_overwrites_a_non_gray_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.create_record(root)
            path = root / "android_phone" / "9.3.75.42245.json"
            updater.write_json(path, {"sort_version_name": "9.3.75.42245", "sub_app_id": 537397314})
            before = path.read_bytes()
            with self.assertRaises(RuntimeError):
                updater.rebuild_index(root)
            self.assertEqual(path.read_bytes(), before)

    def test_modified_output_cannot_be_treated_as_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.create_record(root)
            updater.write_json(root / "android_gray" / "android_phone" / "9.3.75.42245.json", {
                "apk_id": "com.tencent.mobileqq", "protocol_type": 1,
                "sort_version_name": "9.3.75.42245", "sub_app_id": 123456789,
            })
            with self.assertRaises(RuntimeError):
                updater.rebuild_index(root)


if __name__ == "__main__":
    unittest.main()
