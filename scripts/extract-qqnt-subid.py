"""Read QQNT version, QUA, and SubID from QQNT desktop packages.

Usage:
    python work/extract-qqnt-subid.py <QQNT version directory>
    python work/extract-qqnt-subid.py <linuxqq.deb>
    python work/extract-qqnt-subid.py <QQ installer.exe>
    python work/extract-qqnt-subid.py <QQ.dmg>

EXE and DMG require 7-Zip on PATH. The extractor reads package.json and
major.node without running QQ or installing the package.
It fails if the candidate values are missing, ambiguous, or inconsistent.
"""

import argparse
import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


APP_ID_PATTERN = re.compile(rb"QQAppId/(\d{6,12})")
QUA_PATTERN = re.compile(rb"V1_(WIN|LNX|MAC)_NQ_([0-9.]+)_([0-9]+)_GW_B")
VERSION_PATTERN = re.compile(r"^(\d+\.\d+\.\d+)-(\d+)$")
PLATFORMS = {"WIN": "windows", "LNX": "linux", "MAC": "macos"}
PE_ARCHITECTURES = {0x14C: "x86", 0x8664: "x64", 0xAA64: "arm64"}
ELF_ARCHITECTURES = {3: "x86", 8: "mips", 62: "x64", 183: "arm64", 258: "loongarch64"}
MACH_ARCHITECTURES = {7: "x86", 0x01000007: "x64", 0x0100000C: "arm64"}


class SectionReader:
    def __init__(self, path, offset, size):
        self.file = path.open("rb")
        self.file.seek(offset)
        self.remaining = size

    def read(self, size=-1):
        if self.remaining == 0:
            return b""
        if size < 0 or size > self.remaining:
            size = self.remaining
        data = self.file.read(size)
        self.remaining -= len(data)
        return data

    def close(self):
        self.file.close()


def read_deb(path):
    with path.open("rb") as archive:
        if archive.read(8) != b"!<arch>\n":
            raise ValueError("not a Debian ar archive")
        while True:
            header = archive.read(60)
            if not header:
                break
            if len(header) != 60 or header[58:60] != b"`\n":
                raise ValueError("invalid Debian ar header")
            name = header[:16].strip().rstrip(b"/").decode("ascii")
            size = int(header[48:58].strip())
            offset = archive.tell()
            if name.startswith("data.tar."):
                section = SectionReader(path, offset, size)
                try:
                    with tarfile.open(fileobj=section, mode="r|*") as payload:
                        package = major = None
                        for entry in payload:
                            normalized = entry.name.lstrip("./")
                            if not entry.isfile():
                                continue
                            if normalized.endswith("/resources/app/package.json"):
                                package = payload.extractfile(entry).read()
                            elif normalized.endswith("/resources/app/major.node"):
                                major = payload.extractfile(entry).read()
                        if package is None or major is None:
                            raise ValueError("package.json or major.node missing from DEB")
                        return package, major
                finally:
                    section.close()
            archive.seek(offset + size + (size % 2))
    raise ValueError("data.tar archive missing from DEB")


def read_directory(path):
    candidates = list(path.rglob("major.node"))
    candidates = [p for p in candidates if p.parent.name.lower() == "app" and p.parent.parent.name.lower() == "resources"]
    if len(candidates) != 1:
        raise ValueError(f"expected one resources/app/major.node, found {len(candidates)}")
    major_path = candidates[0]
    package_path = major_path.with_name("package.json")
    if not package_path.is_file():
        raise ValueError("package.json missing beside major.node")
    return package_path.read_bytes(), major_path.read_bytes()


def read_7z_archive(path):
    seven_zip = shutil.which("7z")
    if seven_zip is None:
        raise ValueError("7-Zip (7z) is required to unpack EXE and DMG")
    listing = subprocess.run(
        [seven_zip, "l", "-slt", str(path)],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    if listing.returncode != 0:
        raise ValueError(f"7-Zip could not list {path.name}: {listing.stderr.strip()}")
    entries = [line[7:] for line in listing.stdout.splitlines() if line.startswith("Path = ")]
    relevant = [
        entry for entry in entries
        if entry.replace("\\", "/").lower().endswith(
            ("/resources/app/major.node", "/resources/app/package.json")
        )
    ]
    major_paths = [entry for entry in relevant if entry.lower().endswith("major.node")]
    if len(major_paths) != 1:
        raise ValueError(f"expected one resources/app/major.node in archive, found {len(major_paths)}")
    major_path = major_paths[0]
    parent = major_path.replace("\\", "/").rsplit("/", 1)[0].lower()
    package_paths = [
        entry for entry in relevant
        if entry.replace("\\", "/").rsplit("/", 1)[0].lower() == parent
        and entry.lower().endswith("package.json")
    ]
    if len(package_paths) != 1:
        raise ValueError("package.json missing beside major.node in archive")
    with tempfile.TemporaryDirectory(prefix="qqnt-extract-") as temp:
        extraction = subprocess.run(
            [seven_zip, "x", "-y", "-bso0", "-bsp0", f"-o{temp}", str(path), major_path, package_paths[0]],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
        if extraction.returncode != 0:
            raise ValueError(f"7-Zip extraction failed: {extraction.stderr.strip()}")
        return read_directory(Path(temp))


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_architectures(major):
    if major[:2] == b"MZ":
        if len(major) < 64:
            raise ValueError("truncated PE header")
        offset = struct.unpack_from("<I", major, 0x3C)[0]
        if major[offset:offset + 4] != b"PE\0\0":
            raise ValueError("invalid PE header")
        code = struct.unpack_from("<H", major, offset + 4)[0]
        architectures = [PE_ARCHITECTURES.get(code)]
    elif major[:4] == b"\x7fELF":
        endian = "<" if major[5] == 1 else ">" if major[5] == 2 else None
        if endian is None:
            raise ValueError("invalid ELF endianness")
        code = struct.unpack_from(endian + "H", major, 18)[0]
        architectures = [ELF_ARCHITECTURES.get(code)]
    elif major[:4] in {b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}:
        endian = ">" if major[:4] == b"\xca\xfe\xba\xbe" else "<"
        count = struct.unpack_from(endian + "I", major, 4)[0]
        if count < 1 or count > 16:
            raise ValueError("invalid Mach-O fat header")
        architectures = [
            MACH_ARCHITECTURES.get(struct.unpack_from(endian + "I", major, 8 + index * 20)[0])
            for index in range(count)
        ]
    elif major[:4] in {b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"}:
        endian = ">" if major[:4] in {b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf"} else "<"
        architectures = [MACH_ARCHITECTURES.get(struct.unpack_from(endian + "I", major, 4)[0])]
    else:
        raise ValueError("unknown major.node binary format")
    if any(arch is None for arch in architectures):
        raise ValueError("unknown major.node CPU architecture")
    return sorted(set(architectures))


def extract(path, source_url=None):
    if path.is_dir():
        package_bytes, major = read_directory(path)
        source_sha256 = None
    elif path.is_file() and path.suffix.lower() == ".deb":
        package_bytes, major = read_deb(path)
        source_sha256 = sha256_file(path)
    elif path.is_file() and path.suffix.lower() in {".exe", ".dmg"}:
        package_bytes, major = read_7z_archive(path)
        source_sha256 = sha256_file(path)
    else:
        raise ValueError("input must be an unpacked QQNT directory, .deb, .exe, or .dmg file")

    package = json.loads(package_bytes.decode("utf-8-sig"))
    version = package.get("version")
    match = VERSION_PATTERN.fullmatch(version or "")
    if not match:
        raise ValueError(f"unexpected package version: {version!r}")

    app_ids = [int(m.group(1)) for m in APP_ID_PATTERN.finditer(major)]
    quas = [m for m in QUA_PATTERN.finditer(major)]
    unique_ids = set(app_ids)
    unique_quas = {m.group(0) for m in quas}
    if len(unique_ids) != 1 or len(unique_quas) != 1:
        raise ValueError(f"ambiguous QQAppId/QUA: {sorted(unique_ids)}, {sorted(unique_quas)}")
    qua = quas[0]
    if qua.group(2).decode() != match.group(1) or qua.group(3).decode() != match.group(2):
        raise ValueError("QUA version does not match package.json")

    return {
        "platform": PLATFORMS[qua.group(1).decode()],
        "architectures": detect_architectures(major),
        "version": version,
        "subid": app_ids[0],
        "qua": qua.group(0).decode(),
        "qqappid_occurrences": len(app_ids),
        "qua_occurrences": len(quas),
        "major_sha256": hashlib.sha256(major).hexdigest(),
        "source_sha256": source_sha256,
        "source_url": source_url,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--source-url", help="download URL to include in the output provenance")
    args = parser.parse_args()
    try:
        print(json.dumps(extract(args.input, args.source_url), ensure_ascii=False, indent=2))
    except (OSError, ValueError, tarfile.TarError, json.JSONDecodeError) as error:
        print(f"extraction failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
