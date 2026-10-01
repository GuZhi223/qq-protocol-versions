"""Download official QQNT desktop packages and publish extracted SubID rows.

The script is intentionally dependency-free. It imports extract-qqnt-subid.py
from the same directory and only needs 7z on PATH for EXE/DMG packages.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path



def load_extractor():
    path = Path(__file__).with_name("extract-qqnt-subid.py")
    spec = importlib.util.spec_from_file_location("qqnt_extractor", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载提取脚本：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.extract


extract = load_extractor()


DEFAULT_CONFIG_URL = (
    "https://cdn-go.cn/qq-web/im.qq.com_new/latest/rainbow/pcConfig.json"
)
DEFAULT_HOME_URL = "https://im.qq.com/index/"
USER_AGENT = "qq-protocol-versions-desktop-updater/1.0"


def request_bytes(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/html,*/*",
            "Referer": DEFAULT_HOME_URL,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def load_config(config_url: str) -> tuple[dict, str]:
    errors: list[str] = []
    try:
        return json.loads(request_bytes(config_url).decode("utf-8-sig")), config_url
    except Exception as error:
        errors.append(f"{config_url}: {error}")

    try:
        homepage = request_bytes(DEFAULT_HOME_URL).decode("utf-8", "replace")
        candidates = re.findall(
            r"""https?://[^"'<>\\\s]+pcConfig\.json""", homepage, flags=re.I
        )
        candidates.extend(
            [
                DEFAULT_CONFIG_URL,
                "https://cdn-go.cn/qq-web/im.qq.com_new/latest/rainbow/pcConfig.json",
            ]
        )
        seen: set[str] = set()
        for candidate in candidates:
            candidate = candidate.replace("\\/", "/")
            if candidate in seen:
                continue
            seen.add(candidate)
            try:
                return json.loads(request_bytes(candidate).decode("utf-8-sig")), candidate
            except Exception as error:
                errors.append(f"{candidate}: {error}")
    except Exception as error:
        errors.append(f"{DEFAULT_HOME_URL}: {error}")
    raise RuntimeError("无法读取 QQ PC 配置：" + "；".join(errors[-4:]))


def target_packages(config: dict) -> list[tuple[str, str, str, str]]:
    """Return (platform, architecture, package suffix, URL) tuples."""
    targets: list[tuple[str, str, str, str]] = []
    windows = config.get("Windows") or {}
    for architecture, key in (
        ("x86", "ntDownloadUrl"),
        ("x64", "ntDownloadX64Url"),
        ("arm64", "ntDownloadARMUrl"),
    ):
        url = windows.get(key)
        if url:
            targets.append(("windows", architecture, ".exe", str(url)))

    linux = config.get("Linux") or {}
    for architecture, value in (
        ("x64", (linux.get("x64DownloadUrl") or {}).get("deb")),
        ("arm64", (linux.get("armDownloadUrl") or {}).get("deb")),
    ):
        if value:
            targets.append(("linux", architecture, ".deb", str(value)))
    for architecture, key in (
        ("loongarch64", "loongarchDownloadUrl"),
        ("mips64el", "mipsDownloadUrl"),
    ):
        value = linux.get(key)
        if value:
            targets.append(("linux", architecture, ".deb", str(value)))

    macos = config.get("macOS") or {}
    if macos.get("downloadUrl"):
        targets.append(("macos", "universal", ".dmg", str(macos["downloadUrl"])))
    if not targets:
        raise RuntimeError("pcConfig.json 中没有可用的 QQNT 下载地址")
    return targets


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_resumable(url: str, destination: Path, retries: int = 4) -> None:
    """Download with retries and a byte-range resume when the server supports it."""
    for attempt in range(1, retries + 1):
        offset = destination.stat().st_size if destination.exists() else 0
        headers = {"User-Agent": USER_AGENT, "Referer": DEFAULT_HOME_URL}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                if offset and getattr(response, "status", 200) == 200:
                    # The server ignored Range; restarting avoids duplicate bytes.
                    destination.unlink(missing_ok=True)
                    offset = 0
                mode = "ab" if offset else "wb"
                with destination.open(mode) as output:
                    shutil.copyfileobj(response, output, length=1024 * 1024)
            if destination.stat().st_size > 0:
                return
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as error:
            if attempt == retries:
                raise RuntimeError(f"下载失败：{url}：{error}") from error
            time.sleep(attempt * 3)


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "unknown"


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def rebuild_index(output_dir: Path) -> None:
    entries: list[dict] = []
    for path in sorted(output_dir.glob("*/*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and value.get("platform") and value.get("subid"):
            entries.append(value)
    write_json(
        output_dir / "index.json",
        {
            "schema_version": 1,
            "entries": entries,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-url", default=DEFAULT_CONFIG_URL)
    parser.add_argument("--output-dir", type=Path, default=Path("desktop"))
    parser.add_argument(
        "--platform",
        action="append",
        choices=("windows", "linux", "macos"),
        dest="platforms",
        help="limit the run; may be specified more than once",
    )
    args = parser.parse_args()

    config, resolved_config_url = load_config(args.config_url)
    targets = target_packages(config)
    if args.platforms:
        targets = [item for item in targets if item[0] in args.platforms]
    if not targets:
        raise RuntimeError("筛选后没有可处理的平台")

    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="qqnt-desktop-") as temporary:
        temporary_dir = Path(temporary)
        for platform, architecture, suffix, url in targets:
            package = temporary_dir / f"{platform}-{architecture}{suffix}"
            print(f"[{platform}/{architecture}] downloading {url}", flush=True)
            try:
                download_resumable(url, package)
                result = extract(package, source_url=url)
                result["architecture"] = architecture
                result["config_url"] = resolved_config_url
                result["source_sha256"] = sha256_file(package)
                output = (
                    args.output_dir
                    / platform
                    / f"{safe_name(result['version'])}-{safe_name(architecture)}.json"
                )
                write_json(output, result)
                print(
                    f"[{platform}/{architecture}] {result['version']} -> "
                    f"{result['subid']}",
                    flush=True,
                )
            except Exception as error:
                failures.append(f"{platform}/{architecture}: {error}")
                print(f"[{platform}/{architecture}] FAILED: {error}", file=sys.stderr)

    if failures:
        raise RuntimeError("桌面端更新未完成：" + "；".join(failures))
    rebuild_index(args.output_dir)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"update failed: {error}", file=sys.stderr)
        raise SystemExit(1)
