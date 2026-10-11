"""Discover public Android QQ beta announcements and verify them with Eden.

Announcements are untrusted hints, not protocol data. The shared PowerShell
extractor checks the Tencent signer, complete build version, Gray* manifest
channel and both SubIDs before publishing anything outside temporary storage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path


FEED_URL = "https://t.me/s/qq_updatelog"
VERSION_PATTERN = re.compile(r"(?<![\d.])(9\.\d{1,3}\.\d{1,3}\.\d{1,7})(?![\d.])")
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def version_key(version: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", version):
        raise ValueError(f"Invalid complete build version: {version!r}")
    return tuple(int(part) for part in version.split("."))


def trusted_apk_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "downv6.qq.com"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and re.fullmatch(r"/qqweb/QQ_1/android_apk/[A-Za-z0-9_.-]+\.apk", parsed.path) is not None
        )
    except ValueError:
        return False


class FeedParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.messages: list[dict] = []
        self.current: dict | None = None
        self.message_depth = 0
        self.text_depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag not in VOID_TAGS:
            self.stack.append(tag)
        post = attributes.get("data-post") or ""
        if re.fullmatch(r"qq_updatelog/\d+", post):
            self.current = {"post": post, "text": [], "urls": []}
            self.message_depth = len(self.stack)
        if self.current is None:
            return
        if "tgme_widget_message_text" in (attributes.get("class") or "").split():
            self.text_depth = len(self.stack)
        if tag == "a" and self.text_depth:
            url = attributes.get("href") or ""
            if trusted_apk_url(url):
                self.current["urls"].append(url)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self.current is not None and self.text_depth:
            self.current["text"].append(data)

    def handle_endtag(self, tag):
        if tag in VOID_TAGS or tag not in self.stack:
            return
        index = len(self.stack) - 1 - self.stack[::-1].index(tag)
        del self.stack[index:]
        if self.text_depth and len(self.stack) < self.text_depth:
            self.text_depth = 0
        if self.current is not None and len(self.stack) < self.message_depth:
            self.messages.append(self.current)
            self.current = None
            self.message_depth = 0


def parse_candidates(html: str) -> list[dict[str, str]]:
    parser = FeedParser()
    parser.feed(html)
    parser.close()
    if not parser.messages or not any(message["text"] for message in parser.messages):
        raise RuntimeError("Public QQ feed has no recognizable messages; discovery may be unavailable.")
    candidates: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for message in reversed(parser.messages):
        text = " ".join(message["text"])
        if not re.search(r"#Beta\b|灰度|测试版", text, re.I):
            continue
        versions = {match.group(1) for match in VERSION_PATTERN.finditer(text)}
        # Ambiguous announcements cannot choose a build identity for us.
        if len(versions) != 1:
            continue
        version = versions.pop()
        for url in message["urls"]:
            if (url, version) in seen:
                continue
            seen.add((url, version))
            candidates.append({
                "version": version,
                "url": url,
                "feed_url": FEED_URL,
                "announcement_url": "https://t.me/" + message["post"],
            })
    return sorted(candidates, key=lambda value: version_key(value["version"]), reverse=True)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verified_records(root: Path) -> list[tuple[dict, dict]]:
    records: list[tuple[dict, dict]] = []
    for path in sorted((root / "android_gray" / "sources").glob("*.json")):
        source = read_json(path)
        version = source["full_version"]
        version_key(version)
        if path.stem != version or source["channel"] != "gray":
            raise RuntimeError(f"Inconsistent gray source record: {path.name}")
        phone_path = root / "android_gray" / "android_phone" / f"{version}.json"
        pad_path = root / "android_gray" / "android_pad" / f"{version}.json"
        phone, pad = read_json(phone_path), read_json(pad_path)
        for output, expected_type, output_path, digest_key in (
            (phone, 1, phone_path, "android_phone_sha256"),
            (pad, 6, pad_path, "android_pad_sha256"),
        ):
            if (output["apk_id"] != "com.tencent.mobileqq"
                or output["protocol_type"] != expected_type
                or output["sort_version_name"] != version
                or sha256_file(output_path) != source["outputs"][digest_key]):
                raise RuntimeError(f"Inconsistent gray protocol output: {output_path.name}")
        row = {
            "version": version,
            "version_code": source["apk"]["version_code"],
            "manifest_channel": source["manifest_channel"],
            "phone_subid": phone["sub_app_id"],
            "pad_subid": pad["sub_app_id"],
            "phone_path": phone_path.relative_to(root).as_posix(),
            "pad_path": pad_path.relative_to(root).as_posix(),
            "source_path": path.relative_to(root).as_posix(),
        }
        records.append((row, source))
    return sorted(records, key=lambda item: version_key(item[0]["version"]))


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8-sig") == content:
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def rebuild_index(root: Path) -> None:
    records = verified_records(root)
    # Temporary consumer compatibility: numeric full-build filenames coexist
    # with stable short-version filenames. Never overwrite the stable record.
    exports: list[tuple[Path, dict]] = []
    for row, source in records:
        for directory, path_key in (("android_phone", "phone_path"), ("android_pad", "pad_path")):
            protocol = dict(read_json(root / row[path_key]))
            protocol.update(channel="gray", manifest_channel=row["manifest_channel"],
                            source_path=row["source_path"])
            destination = root / directory / f"{row['version']}.json"
            if destination.exists():
                existing = read_json(destination)
                if (existing.get("channel") != "gray"
                    or existing.get("sort_version_name") != row["version"]
                    or existing.get("sub_app_id") != protocol["sub_app_id"]):
                    raise RuntimeError(f"Gray compatibility export would replace another record: {destination}")
            exports.append((destination, protocol))
    for destination, protocol in exports:
        write_json(destination, protocol)
    rows = [row for row, source in records]
    write_json(root / "android_gray" / "index.json", {
        "schema_version": 1,
        "channel": "gray",
        "latest_version": rows[-1]["version"] if rows else None,
        "entries": rows,
    })


def select_pending(candidates: list[dict], root: Path) -> list[dict]:
    records = verified_records(root)
    known = {(source["apk"]["url"], row["version"]) for row, source in records}
    versions = [version_key(row["version"]) for row, source in records]
    for path in (root / "android_phone").glob("*.json"):
        versions.append(version_key(read_json(path)["sort_version_name"]))
    floor = max(versions, default=(0, 0, 0, 0))
    return [candidate for candidate in candidates
            if (candidate["url"], candidate["version"]) not in known
            and version_key(candidate["version"]) > floor]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check-only", action="store_true", help="discover candidates without downloading or modifying data")
    parser.add_argument("--max-candidates", type=int, default=2, help="bound downloads per run (default: 2)")
    args = parser.parse_args()
    if args.max_candidates < 1 or args.max_candidates > 5:
        parser.error("--max-candidates must be between 1 and 5")
    root = args.repository_root.resolve()
    request = urllib.request.Request(FEED_URL, headers={"User-Agent": "qq-protocol-versions-gray-updater/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        html = response.read(2 * 1024 * 1024 + 1)
    if len(html) > 2 * 1024 * 1024:
        raise RuntimeError("Public feed exceeds the discovery size limit.")
    candidates = select_pending(parse_candidates(html.decode("utf-8")), root)[:args.max_candidates]
    if args.check_only:
        print(json.dumps(candidates, ensure_ascii=True, indent=2))
        return 0
    if not candidates:
        rebuild_index(root)
        print("No new public Android QQ gray build was discovered.")
        return 0
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        raise RuntimeError("PowerShell is required for signed APK validation and Eden extraction.")
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="qq-gray-discovery-") as temporary:
        candidate_file = Path(temporary) / "candidate.json"
        for candidate in candidates:
            print(f"Checking public gray candidate {candidate['version']} from {candidate['announcement_url']}", flush=True)
            write_json(candidate_file, candidate)
            try:
                subprocess.run([
                    powershell, "-NoProfile", "-File", str(Path(__file__).with_name("update-protocol.ps1")),
                    "-Channel", "gray", "-CandidateFile", str(candidate_file), "-RepositoryRoot", str(root),
                ], check=True)
            except subprocess.CalledProcessError as error:
                failures.append(f"{candidate['version']}: validation/extraction exited with {error.returncode}")
    # Preserve an index of valid partial results even if another candidate failed.
    rebuild_index(root)
    if failures:
        raise RuntimeError("Gray updates failed: " + "; ".join(failures))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"gray update failed: {error}", file=sys.stderr)
        raise SystemExit(1)
