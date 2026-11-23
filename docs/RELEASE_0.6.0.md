# Codex Local MCP 0.6.0 发布与验收记录

完成日期：2026-11-23（Asia/Shanghai）。作者：[Sugar](https://sugar.makersns.com/)。

**状态：已实施、测试、安装、完成真实回连、推送 GitHub 并公开发布。** 本轮使用的实施分支已合并到 main 后删除。唯一维护仓库为 [makersns/codex-local-mcp](https://github.com/makersns/codex-local-mcp)。

[0.6.0 Release](https://github.com/makersns/codex-local-mcp/releases/tag/v0.6.0) · [下载 macOS 客户端](https://github.com/makersns/codex-local-mcp/releases/download/v0.6.0/Codex_Local_MCP_0.6.0_macos_universal.zip) · [SHA-256 校验文件](https://github.com/makersns/codex-local-mcp/releases/download/v0.6.0/SHA256SUMS.txt)

正式发布时间：2026-11-23 18:16:40（Asia/Shanghai）。GitHub Release 为公开正式版本，已标为 Latest。

## 已交付功能

| 范围 | 完成结果 |
| --- | --- |
| 原生桌面 | SwiftUI + AppKit 单窗口，概览、文件夹、连接、环境检查、运行记录和帮助 |
| 便捷配置 | 原生文件夹选择器、自动项目标识、通俗权限说明、同页首次配置、未保存草稿保护 |
| 会话活动 | 当前正在调用的会话、近 10 分钟活跃会话、运行中任务，以及按需展开的匿名明细 |
| 四语客户端 | English、简体中文、繁體中文、日本語；默认随系统，可保存手动选择 |
| 语言切换 | 保留名称和连接 ID 草稿、保存状态与后台连接；菜单、帮助、校验及常见错误一起本地化 |
| 作者主页 | 侧栏、帮助和关于中的 Sugar 署名可打开 https://sugar.makersns.com/ |
| 原创图标 | C 形本机窗口与薄荷色连接点，自绘路径，16–1024 像素完整图标；侧栏使用同一图形 |
| GitHub 展示 | 英文首页、三份语言说明、8 张真实应用预览截图、下载入口、使用要求和项目 topics |

截图使用明确标注的示例数据，不公开实际用户文件夹或连接信息。四语目录共 554 条，其中 173 条覆盖后端已知用户提示；用户输入、文件路径和原始诊断内容保持原样。

## 验证结果

| 检查 | 真实结果 | 主要证据 |
| --- | --- | --- |
| 桌面完整 Python 回归 | 124 项全部通过 | activity-desktop-regression.txt |
| MCP 核心完整回归 | 140 项运行：139 通过、1 项可选 Office 条件跳过 | activity-core-regression.txt |
| 正式 App：Apple Silicon | 77 / 77 原生自测通过 | final-packaged-self-test-arm64.json |
| 正式 App：Intel 二进制 | 77 / 77 经 Rosetta 通过 | final-packaged-self-test-x86_64.json |
| 四语专项 | 35 项，已包含在上述 77 项中 | macos/LocalizationSelfTests.swift |
| 原始真实 UI 操作 | 17 项通过 | ui/native-ax-results-2.json |
| 首次配置真实操作 | 12 项通过，含中文空格目录选择与默认权限读回 | ui/native-first-setup-results-3.json |
| 新增语言/活动/作者交互 | 22 项通过 | ui/native-i18n-acceptance-final.json |
| 四语与窄窗口渲染 | 12 场景，最终公开图 8 张 | ui/i18n-render-results.json；ui/published-screenshot-receipt.json |
| 正式发行物 | 33 项通过，含严格签名、资源、隔离安装和 ZIP 解压复验 | release-build/final-artifact-verification.json |
| 包内当前文档链接 | 93 项，零断链 | 同上 |
| 提交前当前文档链接 | 113 项，零断链 | release-source-incremental-audit.json |

完整说明见 [验证记录](../VALIDATION.md)。Apple Silicon 在实际 Mac 上运行；Intel 二进制经 Rosetta 运行，不将此称为 Intel 真机验收。可选 Office 跳过项不计为通过。

## 真实安装与连接

正式 App 已安装到 `~/Applications/Codex Local MCP.app`。先向旧界面发送正常退出请求，确认退出后安装；没有强制结束界面或丢弃草稿。

两项原有文件夹及其读写/执行权限保留。settings.json 和 workspaces.json 的 SHA-256 在安装及准备过程中保持不变，钥匙串状态为已保存。验证只读取密钥的存在状态，不把真实密钥打印或写入回执。

准备阶段确认：

- 已安装 App 版本为 0.6.0，源码绑定 ca446d2e1ad43349971906023984483b39094620。
- 新运行环境的 source_digest 与正式 App 内的服务器源码匹配，避免把同为 0.6.0 的旧候选环境误认为最终环境。
- 启动新界面时原后台 PID 保留；直到当前受管作业为空，才执行明确的后台切换。

切换后，实际 ChatGPT 工具调用返回 **server_version: 0.6.0**。本机状态为 ready、managed:true、upgrade_pending:false。当前正式后台与 MCP 子进程分别为本次验收观察到的 25063 和 25066；PID 仅是当时的验收值，不是固定配置。

真实 `workspace_list` 与 README `read_file` 串行调用均通过；随后两项并发调用也成功，在同一轮中返回版本 0.6.0 和预期 README SHA-256。工作区数量为 2。

### 实际会话统计

2026-11-23 18:08:42（Asia/Shanghai）的真实快照显示：

| 字段 | 观察值 |
| --- | --- |
| available | true |
| metadata_seen | true |
| recent_sessions | 3 |
| active_sessions | 0 |
| unknown_active_calls / unknown_recent_calls | 0 / 0 |
| coverage_complete | true |
| capacity_limited | false |
| window_seconds | 600 |

这证明本次真实连接会传递匿名会话元数据，并能聚合多个会话。数字随活动与时间窗口变化，不是固定值或精确在线人数。运行中任务的快照会包含用于验收的命令本身，不能将该数视为额外会话。详见 [活动口径](SESSION_MONITORING_DESIGN.md)。

### 升级编排中遇到的情况

回连期间有两次工具请求超时，随后串行和并发请求均已复核成功。本次临时升级任务通过 launchctl submit 注册后，被系统重复拉起；一次性 gate 已被消费，后续执行在业务动作前退出，没有重复执行升级。该临时任务已移除，移除前后正式后台 PID 相同。

临时升级脚本只用于这次安装编排，没有包含在公开客户端中；相关失败记录、任务移除回执与最终真实调用证据均保留。不存在仍注册的本次临时升级任务。

## 构建、来源和发布附件

App 和 v0.6.0 标签绑定：

```text
source_revision: ca446d2e1ad43349971906023984483b39094620
source_modified: false
version: 0.6.0
architectures: arm64 + x86_64
minimum_macos: 13.0
resource_sha256: b88008d02fe9d187c680da739df7a384750d7795bc55af10ad1252f1020f05d1
```

最终构建耗时 112.18 秒。App 为 11,185,784 bytes，包含 111 个资源文件；其中 109 个源码资源逐字节匹配上述干净提交。后续 main 上的验收文档提交不改变已发布 App 的代码来源。

ZIP 为 5,889,393 bytes，外层只有 App、INSTALL.html 和 README.txt，完整源码及说明位于 App Resources。包内当前文档、四语资源、10 个标准 ICNS 尺寸均已核对。没有运行配置、日志、虚拟环境、缓存或私有过程目录进入分发包。

ZIP SHA-256：

```text
9a2264dc97ffb7e8006e6c8792eeed758bf83d45895e35bd2cc00fd8aa247ad3
```

公开 Release 只附带 ZIP 与 SHA256SUMS.txt。GitHub 返回的两项资产摘要、文件大小和 uploaded 状态与本地文件一致；发布后再次确认 draft:false、prerelease:false、Latest 为本版本。

## 合并与清理

- 实施分支 feat/native-desktop-0.6.0 已快进合并到 main，源码与 v0.6.0 标签已推送并核对远端。
- 已删除本地已合并实施分支，当前仅保留 main；没有创建远端过程分支。
- 已删除旧 be980f7 / a6f11a5 候选包、过期 wheel、测试二进制、图标中间文件与编译缓存。
- 本轮可重建过程产物合计约 381.6 MB；最终分发文件、精简验证回执和回滚快照保留。
- 原无远端的 Sugar_Local_MCP_Desktop_0.5.0 是旧发布目录，已核验后移入 macOS 废纸篓。150 个原文件与 Finder 元数据逐项核对；3 份独有文档和 2 份原始测试日志已保留。
- 唯一维护仓库、新版安装 App、当前运行环境和其他工程未作为清理对象。

## 保留的验证边界

当前为 ad-hoc 签名，没有 Apple Developer ID 公证。未验证 Intel Mac 真机、另一台全新 Mac 的首次联网冷下载、独立隧道与 Chrome 同机配对，或复杂 Office 文档的完整保真。Chrome 仍是独立的 ChatGPT 集成。

历史 0.5.0 文档保留原文；其中旧相对链接不作为本版验证证据。

## 证据位置与官方说明

机器上的完整回执保存在 Git 忽略的 `.data/native-desktop-0.6.0/`，包括构建、原生 UI、真实安装、end-to-end-tool-calls.json、github-release-verification.json 和 final-cleanup-receipt.json。公开仓库只保留源码、说明和无敏感数据的截图。

- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [连接与测试插件](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [OpenAI Plugins Reference](https://developers.openai.com/plugins/reference)
