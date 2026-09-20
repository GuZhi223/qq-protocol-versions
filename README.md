# QQ Protocol Versions

Automatically discovers the latest official Android QQ package from Tencent's
App Store metadata endpoint, verifies the APK, extracts protocol data with
[MrXiaoM/Eden](https://github.com/MrXiaoM/Eden), and commits the generated JSON.

## Data layout

- `android_phone/<version>.json`
- `android_pad/<version>.json`
- `sources/<version>.json` — discovery URL, APK hashes, signature and Eden version

The QQ APK is downloaded only into the temporary runner workspace. It is never
committed or published by this repository.

A `sources` record carries one of two origins in `discovery.provider`:

- `Tencent App Store metadata` — written by `scripts/update-protocol.ps1`, with
  `discovered_at` and `apk.url` filled from the discovery response.
- `Existing locally verified QQ APK` — written when a version was extracted from
  an APK obtained out of band. `apk.url` is `null` and `discovered_at` records
  when the APK was acquired.

Both variants share the same key set, so downstream consumers can read either
without branching.

## Automation

The GitHub Actions workflow runs every day and can also be started manually.
It performs these checks before running Eden:

1. Tencent metadata identifies the package as official `com.tencent.mobileqq`.
2. Downloaded size and SHA-256 match Tencent's metadata.
3. Android manifest package/version match the discovered release.
4. The signing certificate SHA-256 matches Tencent's metadata.
5. Eden outputs valid Phone (`protocol_type = 1`) and Pad (`protocol_type = 6`) JSON.

Each check aborts the run, so nothing is committed unless all five pass.

## Requirements

- PowerShell
- Java 8
- .NET 6 or newer runtime
- Android SDK Build Tools (provides `aapt` and `apksigner`)

`aapt` and `apksigner` are resolved from `PATH` first, then from
`$env:ANDROID_HOME`, `$env:ANDROID_SDK_ROOT`, `$env:LOCALAPPDATA\Android\Sdk`,
or `$env:ProgramFiles\Android\android-sdk`. Within an SDK root the newest
`build-tools` directory wins, compared numerically.

To run locally on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\update-protocol.ps1
```

## Licensing and attribution

See [NOTICE.md](NOTICE.md) for the provenance of the published data, Eden's
AGPL-3.0 terms, the components bundled inside Eden, and the caveat about
republishing these constants.

