# Provenance and notices

This repository publishes protocol constants extracted from the official Android
QQ package. It contains no Tencent code and never redistributes the QQ APK.

## What is committed

- `android_phone/<version>.json`, `android_pad/<version>.json` — protocol
  constants produced by the extractor described below.
- `sources/<version>.json` — the provenance record for each version: how the
  package was obtained, its hashes, and the exact extractor identity.

## What is never committed

The QQ APK itself. It is downloaded into a temporary runner workspace, verified,
extracted, and discarded. `.gitignore` additionally excludes `*.apk` and `*.zip`.

## Extractor

- [MrXiaoM/Eden](https://github.com/MrXiaoM/Eden) — produces the protocol
  constants. Licensed **AGPL-3.0** by its author. Eden is **not** vendored or
  redistributed in this repository: the workflow downloads the pinned release
  archive from GitHub at run time and verifies it against the recorded SHA-256
  in `sources/<version>.json` before use. The JSON files here are data produced
  by running Eden, not Eden itself.

## Components bundled inside Eden

Used during extraction; not redistributed by this repository.

- [pxb1988/dex2jar](https://github.com/pxb1988/dex2jar) — Apache-2.0
- [mstrobel/procyon](https://github.com/mstrobel/procyon) — Apache-2.0
- [googlecode/android4me](https://code.google.com/archive/p/android4me) — Android
  binary XML decoding

## Licensing of this repository

No license is declared for this repository's own scripts and workflow. They are
original work. Add a `LICENSE` file here if you intend to grant reuse rights.

## Caveat

Reverse-engineering the QQ protocol and republishing the resulting constants is
not authorised by Tencent. The upstream extractor's own README states that it is
for study only and that the project should be deleted within 24 hours of
download. Consumers of this data are responsible for their use of it.
