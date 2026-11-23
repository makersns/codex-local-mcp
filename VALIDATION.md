# Codex Local MCP 0.6.0 验证记录

验证日期：2026-11-23（Asia/Shanghai）。作者：Sugar。

本记录区分源码回归、使用示例数据的原生窗口、隔离安装与真实连接。历史版本的测试不计入本轮结果；“本机已就绪”不等同于 ChatGPT 端到端调用通过。

## 测试环境

| 项目 | 本次实际环境 |
| --- | --- |
| 系统与硬件 | macOS 15.5，Apple Silicon arm64 |
| 原生界面 | Swift 6.1，SwiftUI + AppKit，部署目标 macOS 13 |
| Python / MCP | Python 3.12.16，固定 MCP SDK 2.3.0 |
| 源码版本 | 桌面与核心均为 0.6.0 |
| 运行隔离 | Python 回归使用临时目录；原生交互使用明确标识的 `--preview` 示例数据 |
| 当前权限 | 既有测试执行进程具备辅助功能、屏幕录制和事件权限；未为测试自动申请或更改权限 |

## 自动测试结果

| 检查 | 本轮结果 |
| --- | --- |
| 桌面完整 Python 回归 | **117 项全部通过**，0 失败、0 跳过 |
| 其中新增桌面 API 专项 | 32 项，已包含在上述 117 项中 |
| 其中原生安装专项 | 14 项，已包含在上述 117 项中 |
| MCP 核心完整回归 | **124 项运行：123 通过、1 跳过、0 失败** |
| 原生 Swift 自测 | **21 / 21 通过**，包含真实子进程大输入、阻塞输入超时和输出限长 |
| 已有配置预览模型 | 8 / 8 通过 |
| 首次配置预览模型 | 9 / 9 通过 |
| 实际原生按钮与键盘交互 | **17 / 17 通过**；下文说明与真实生产连接的区别 |
| 原生源码编译 | 成功，0 警告 |
| Shell 脚本 | 安装、构建、bootstrap 等 5 个脚本的 `bash -n` 通过 |

核心唯一跳过项是可选 Office 文档库未安装时的合成读写回环。它不计为通过，也不沿用历史版本 Office 或 Chrome 联调作为本版证据。

### API 与配置可靠性

桌面回归覆盖完整工作区文档保留、外部修改与 revision 冲突、并发保存、两份配置文件的写入失败回滚、钥匙串元数据、模拟密钥暂存与失败清理、保存成功但连接重启失败时的 `saved: true` 状态、隔离 support 目录、受管进程归属和未知进程保护。

运行记录固定文件范围、不跟随符号链接、限制读取长度，并遮盖常见 Runtime key、Bearer / Basic Authorization、Cookie、access_token / refresh_token 等凭据形式。测试不读取或打印真实密钥。

核心回归包含真实 stdio 初始化、工具发现、中文文件读写、哈希冲突、路径边界、进程生命周期、stdin / EOF、超时与输出分页、HTTP Host / Origin 保护、诊断 IPC 以及权限和 Office 检测。详见 [核心验证](server/docs/VALIDATION.md)。

## 原生界面实际验收

17 项验收通过 macOS Accessibility 按钮操作和真实 Unicode 键盘事件完成，运行对象是本轮最终 Swift 二进制的独立预览窗口。预览中的连接动作返回模拟结果，不触碰生产密钥或连接；配置落盘和重启失败处理由上述 Python API 临时目录测试另行覆盖。

已实际验证：

- 五个导航页可达，帮助可打开和关闭。
- 中文输入触发真实表单草稿与保存状态；切换页面时保留编辑、放弃修改均符合预期。
- 错误连接 ID 保留草稿并显示校验信息；合法示例连接可以保存。
- 文件夹权限编辑的取消与保存；运行命令也可修改文件的说明。
- 技术 ID 按需展开；移除访问有确认；剩最后一个文件夹时提供明确说明并禁用移除。
- 环境检查由用户触发；运行记录可打开和刷新。
- 停止连接的取消、预览中的停止与启动。
- 关闭窗口时保护未保存草稿。

真实首次配置的文件夹选择器测试与最终安装验收结果追加在 [发布交接记录](docs/RELEASE_0.6.0.md)。预览模型的 9 项通过不替代真实文件选择器验收。

### 布局与截图

实际渲染检查覆盖 1240 宽的概览、文件夹、连接、环境检查、运行记录，以及 920 宽的首次设置和文件夹页面。检查了文字、开关对齐、滚动区域和日志内容位置。长表单采用纵向滚动，没有依赖横向滚动完成配置。

以下图片为原生应用实际渲染，并明确显示“界面预览 · 示例数据”：

- [连接概览](docs/images/desktop-overview-0.6.0.png)
- [项目文件夹](docs/images/desktop-folders-0.6.0.png)
- [连接设置](docs/images/desktop-connection-0.6.0.png)

## 运行环境与安装验证

实际 `bootstrap.sh` 在隔离 support 目录执行两次：首次 **5.490 秒**准备并校验 0.6.0 arm64 运行环境，第二次 **0.816 秒**校验并复用。复用了本机已验证的 uv、Python 和依赖缓存；未替换生产 runtime-info，测试临时目录已清理。这不表示空白 Mac 首次联网冷下载通过。

14 项安装测试包含首次与重复安装、中文及空格路径、升级备份、同版本损坏修复、防降级、应用标识与签名校验、符号链接拒绝、活动锁与失效锁处理。正式 universal App 的构建、严格验签、归档再解压和实际安装结果以 [发布交接记录](docs/RELEASE_0.6.0.md) 及对应 GitHub Release 附件摘要为准。

## 已有用户兼容

新 API 对实际已有配置完成只读快照验证：两项原有文件夹和权限仍在，钥匙串仅检查“已保存”元数据；settings、workspaces 与 launchctl plist 的 SHA-256 均未变化。

升级路径还验证了：准备新版运行环境不会自动停止旧连接；旧受管 PID 继续按 launchctl 标识和 PID 识别，并能显示“需要重启以应用更新”。当前源码版本、安装版本与实际服务版本分别核对，不能互相替代。

## 会话数量

0.6.0 **尚未实现 ChatGPT 会话数量统计**。官方匿名会话元数据可以支持“正在调用的会话”和明示时间窗口内的“最近活跃会话”；无心跳或关闭事件时不能保证精确在线数。方案与固定 SDK 的只读验证见 [会话活动监测设计](docs/SESSION_MONITORING_DESIGN.md)。

## 可重现验证与本机证据

具备固定依赖后，在仓库根目录执行：

```bash
PYTHONPATH=server/src python -B -m unittest discover -s tests -v
PYTHONPATH=server/src python -B -m unittest discover -s server/tests -v
```

`tests/native_ui_probe.py` 和 `tests/native_ui_acceptance.py` 仅允许显式的 CodexLocalMCP `--preview` 进程。它们不会自动授予系统权限。Swift 自测由原生可执行文件的 `--self-test` 入口运行。

本机完整回执位于被 Git 忽略的 `.data/native-desktop-0.6.0/`，包括 Python 日志、`ui/self-test-results-final.json`、`ui/render-results-final.json`、`ui/native-ax-results-2.json` 和 bootstrap 验证记录。公开仓库不包含真实连接配置、凭据或个人日志。

## 未覆盖的范围

- Intel Mac 真机运行与安装；universal 架构检查不能替代真机验收。
- 第二台 Mac 的首次联网冷下载、独立隧道及 Chrome 同机配对。
- Apple Developer ID 签名、公证及所有下载渠道的 Gatekeeper 行为。
- 复杂 Office 版式、公式重算、宏、打印、PDF 转换或原生 Office 界面操作。
- 通用原生桌面控制或第三方项目的全部工具链。

## 历史记录

[0.5.0](VALIDATION_0.5.0_HISTORY.md)、[0.4.1](VALIDATION_0.4.1_HISTORY.md)、[0.4.0](VALIDATION_0.4.0_HISTORY.md)、[0.3.0](VALIDATION_0.3.0_HISTORY.md) 的验证报告独立保留。
