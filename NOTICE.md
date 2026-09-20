# 数据来源与声明

本仓库发布从 Android QQ 官方安装包中提取的协议常量。仓库内不含腾讯的任何代码，
也从不转载 QQ APK。

## 入库的内容

- `android_phone/<版本>.json`、`android_pad/<版本>.json` — 由下述提取工具产出的协议常量。
- `sources/<版本>.json` — 每个版本的来源记录：安装包如何取得、其哈希，以及提取工具的准确身份。

## 从不入库的内容

QQ APK 本身。它只会被下载到 runner 的临时工作区，校验、提取之后即被删除。
`.gitignore` 另外排除了 `*.apk` 与 `*.zip`。

## 提取工具

- [MrXiaoM/Eden](https://github.com/MrXiaoM/Eden) — 负责产出这些协议常量。作者以
  **AGPL-3.0** 授权。本仓库**没有**内联或转载 Eden：工作流在运行时从 GitHub 下载
  固定版本的发布包，先与 `sources/<版本>.json` 中记录的 SHA-256 核对，再拿来使用。
  本仓库中的 JSON 是运行 Eden 所得的数据，不是 Eden 本身。

## Eden 内部打包的组件

仅在提取过程中使用，本仓库不转载。

- [pxb1988/dex2jar](https://github.com/pxb1988/dex2jar) — Apache-2.0
- [mstrobel/procyon](https://github.com/mstrobel/procyon) — Apache-2.0
- [googlecode/android4me](https://code.google.com/archive/p/android4me) — Android 二进制 XML 解码

## 本仓库自身的许可

本仓库自有的脚本与工作流未声明许可证，它们是原创作品。如果你打算授予他人复用权利，
请在此处添加 `LICENSE` 文件。

## 注意事项

逆向 QQ 协议并转载由此得到的常量，未获腾讯授权。上游提取工具自己的 README 写明
本项目仅供学习参考，并应在下载后 24 小时内删除。使用者需自行对数据的使用方式负责。
