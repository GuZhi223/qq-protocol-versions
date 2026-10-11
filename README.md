# QQ 协议版本数据

自动从腾讯应用宝元数据接口发现最新的 Android QQ 官方安装包，校验 APK，
用 [MrXiaoM/Eden](https://github.com/MrXiaoM/Eden) 提取协议数据，并提交生成的 JSON。

## 数据布局

- `android_phone/<版本>.json`
- `android_pad/<版本>.json`
- `sources/<版本>.json` — 发现地址、APK 哈希、签名信息与 Eden 版本
- `android_gray/android_phone/<完整版本>.json`、`android_gray/android_pad/<完整版本>.json`
  — 独立保存的 Android 灰度协议数据，不覆盖正式版目录
- `android_gray/sources/<完整版本>.json` — 公告地址、腾讯 APK 地址、实际哈希、签名与灰度标记
- `android_gray/index.json` — 已验证灰度版本索引与最高完整版本
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

GitHub Actions 工作流每天运行一次，也可以手动触发。

每日任务在默认分支 `main` 执行，计划时间为北京时间 11:23（UTC 03:23），实际启动可能
受 GitHub 调度延迟影响。Android、Windows、Linux、macOS 均由此任务检查；Android 失败
时桌面端检查仍继续。桌面端会尝试全部平台与架构，失败时任务显示失败，并提交其他目标
已成功校验的结果；没有变化时不创建提交。

Android 灰度通道也每日执行，失败不会阻止正式版和桌面端继续检查。它从
[QQ/TIM Update Log 公开频道](https://t.me/s/qq_updatelog) 最新页面读取带 `Beta`/灰度标记、
完整四段版本号和腾讯 APK 链接的公告。此频道**不是腾讯官方元数据接口**，只提供发现线索：
脚本仅下载 `downv6.qq.com/qqweb/QQ_1/android_apk/` 下的 HTTPS 直链，拒绝下载重定向，
并使用已验证正式版的腾讯 QQ 签名证书 SHA-256 作为信任锚。包名、清单构建号、Phone/Pad
的 `Gray*` 标记、Eden 完整版本与两个 SubID 必须全部一致才会写入灰度目录。
灰度 JSON 在记录输出哈希前统一为 UTF-8、LF 换行，与仓库的 `.gitattributes` 规则一致，
保证首次生成、Git 提交和下次检出后的文件字节哈希一致。

每次最多尝试两个高于仓库当前版本的新候选；同一灰度构建不会被不同 APK 静默覆盖。
公告可能滞后、频道可能不可访问，也不包含所有受邀用户的灰度包，因此不保证覆盖腾讯
全部灰度发布。发现源不可用会明确报错，不会冒充“没有更新”。
手动触发时选择 `android-gray` 可只验证灰度流程；默认 `all` 仍检查所有平台与两个 Android 通道。

只查询灰度候选、不下载、不修改数据：

```powershell
python scripts/update-android-gray.py --check-only
```

Android 正式版流程在调用 Eden 之前会依次校验：

1. 腾讯元数据确认该包是官方 `com.tencent.mobileqq`。
2. 下载得到的体积与 SHA-256 与腾讯元数据一致。
3. Android 清单中的包名与版本号与发现的版本一致。
4. 签名证书 SHA-256 与腾讯元数据一致。
5. Eden 产出合法的 Phone（`protocol_type = 1`）与 Pad（`protocol_type = 6`）JSON。

任何一项不通过都会中止正式版 Android 提取，因此只有五项全部通过才会提交该版本的正式版数据。

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
返回 403 或包内容无法解包，脚本会依次尝试 [NapCatQQ Release](https://github.com/NapNeko/NapCatQQ/releases)
中的腾讯安装包链接，再回退到带 SHA-256 校验的
[Rodert/qq-versions 镜像](https://github.com/Rodert/qq-versions)。NapCat Release
中的链接只接受腾讯域名，并且必须精确匹配平台和架构。结果会保留官方地址、实际下载地址、
下载来源以及 NapCat Release 标签，便于追溯。

`major.node` 中的 SubID 提取兼容 NapCat 的两种格式：新版的
`QQAppId/<数字>\0` 标记，以及旧版的
`A4 09 00 00 00 35` 二进制标记。脚本仍会校验 `package.json`、QUA、架构和重复候选值；
发现冲突时不会提交该条目。桌面端结果的唯一键包含平台、架构和完整构建版本，因为 Linux
x64 与 arm64 可能共享版本号但使用不同 SubID。

工作流把 GitHub Actions 自带的 `GITHUB_TOKEN` 传给桌面端脚本，用于读取镜像发布元数据，
避免匿名 GitHub API 的低频率限制。本地运行时不需要令牌；如果遇到 GitHub API 限流，
可以设置 `GITHUB_TOKEN` 或 `GH_TOKEN` 后再运行脚本。

## 许可与归属

数据来源、Eden 的 AGPL-3.0 条款、Eden 内部打包的组件，以及转载这些常量的注意事项，
见 [NOTICE.md](NOTICE.md)。
