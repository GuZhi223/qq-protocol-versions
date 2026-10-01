# QQ 协议版本数据

自动从腾讯应用宝元数据接口发现最新的 Android QQ 官方安装包，校验 APK，
用 [MrXiaoM/Eden](https://github.com/MrXiaoM/Eden) 提取协议数据，并提交生成的 JSON。

## 数据布局

- `android_phone/<版本>.json`
- `android_pad/<版本>.json`
- `sources/<版本>.json` — 发现地址、APK 哈希、签名信息与 Eden 版本
- `desktop/<平台>/<版本>-<架构>.json` — QQNT Windows/Linux/macOS 包中
  `resources/app/package.json` 与 `major.node` 提取出的版本、架构、SubID 和 QUA
- `desktop/index.json` — 当前仓库中全部桌面端条目的索引

QQ APK 只会被下载到 runner 的临时工作区，本仓库从不提交、也从不发布它。

`sources` 记录中 `discovery.provider` 有两种取值：

- `Tencent App Store metadata` — 由 `scripts/update-protocol.ps1` 写入，
  `discovered_at` 与 `apk.url` 取自发现接口的返回。
- `Existing locally verified QQ APK` — 当某个版本来自另行取得的 APK 时写入。
  `apk.url` 为 `null`，`discovered_at` 记录该 APK 的取得时间。

两种取值的字段集完全一致，下游读取时无需分支处理。

## 自动化

GitHub Actions 工作流每天运行一次，也可以手动触发。它在调用 Eden 之前会依次校验：

1. 腾讯元数据确认该包是官方 `com.tencent.mobileqq`。
2. 下载得到的体积与 SHA-256 与腾讯元数据一致。
3. Android 清单中的包名与版本号与发现的版本一致。
4. 签名证书 SHA-256 与腾讯元数据一致。
5. Eden 产出合法的 Phone（`protocol_type = 1`）与 Pad（`protocol_type = 6`）JSON。

任何一项不通过都会中止运行，因此只有五项全部通过才会产生提交。

## 运行环境

- PowerShell
- Java 8
- .NET 6 或更高运行时
- Android SDK Build Tools（提供 `aapt` 与 `apksigner`）

`aapt` 与 `apksigner` 先查 `PATH`，再依次查 `$env:ANDROID_HOME`、
`$env:ANDROID_SDK_ROOT`、`$env:LOCALAPPDATA\Android\Sdk`、
`$env:ProgramFiles\Android\android-sdk`。在一个 SDK 根目录内取版本号最大的
`build-tools` 目录，按数值比较而不是按字典序。

在 Windows 上本地运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\update-protocol.ps1
```

桌面端由同一个工作流自动更新。工作流从腾讯
[PC 配置](https://cdn-go.cn/qq-web/im.qq.com_new/latest/rainbow/pcConfig.json)
读取 Windows、Linux 和 macOS 下载地址，在临时目录解包，只保留 JSON 结果；Windows
安装器和 macOS DMG 使用 7-Zip，Linux DEB 使用 Python 标准库解析。若腾讯 CDN 对 runner
返回 403，脚本会回退到带 SHA-256 校验的 [Rodert/qq-versions 镜像](https://github.com/Rodert/qq-versions)，并在结果中保留官方地址、实际下载地址和下载来源。桌面端结果的
唯一键包含平台、架构和完整构建版本，因为 Linux x64 与 arm64 可能共享版本号但使用不同
SubID。

## 许可与归属

数据来源、Eden 的 AGPL-3.0 条款、Eden 内部打包的组件，以及转载这些常量的注意事项，
见 [NOTICE.md](NOTICE.md)。
