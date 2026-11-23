# Codex Local MCP

<img src="docs/images/app-icon-0.6.0.png" alt="Codex Local MCP 应用图标" width="96" height="96">

**让 AI 使用你自己的 Mac，处理文件、运行项目和完成本机任务。**

作者：**Sugar** · 桌面版本：**0.6.0** · macOS 13 及以上

Codex Local MCP 提供原生 macOS 桌面界面，用于管理本机连接与授权文件夹。选择文件夹、设置可用能力、保存连接信息，都可以在同一个窗口完成。网页操作使用 ChatGPT 独立连接的 Chrome 集成。

这是 Sugar 开发的社区项目，并非 OpenAI 官方客户端。应用使用官方隧道组件连接已有隧道；项目本身不调用模型 API。

![Codex Local MCP 原生连接概览](docs/images/desktop-overview-0.6.0.png)

*原生应用实际渲染，图中为明确标注的示例预览数据。*

## 开始使用

1. 打开 **Codex Local MCP.app**。应用安装到当前用户的 Applications，首次运行自动准备所需环境。
2. 在“连接设置”填写电脑名称、连接 ID 和连接密钥。页面提供获取指引，密钥保存在 macOS 登录钥匙串。
3. 点击“选择文件夹”，添加允许 AI 访问的本地文件夹。项目 ID 自动生成，可以在“更多设置”中调整。
4. 选择是否允许编辑文件、运行本机任务，点击“保存并连接”。
5. 本机就绪后，在 ChatGPT 中选择对应连接，让它读取一个项目文件，确认整条连接可用。

已有用户直接进入概览，原来的文件夹、权限和密钥继续使用。关闭窗口后，后台连接继续运行。

## 日常操作

| 想做什么 | 去哪里 |
| --- | --- |
| 查看本机是否就绪、开始或重启连接 | 连接概览 |
| 添加、更换、移除可访问的文件夹 | 项目文件夹 |
| 修改电脑名称、连接 ID 或密钥 | 连接设置 |
| 检查 Office 文件处理能力、可选系统权限 | 环境检查 |
| 查看脱敏运行信息、复制问题详情 | 运行记录 |
| 查看连接步骤、Chrome 使用方法、版本与作者 | 使用帮助 / 关于 |

### 文件夹权限

每个文件夹都可以单独设置：

- **读取文件**：查看目录和文本内容。
- **编辑文件**：允许文件工具创建、修改文件。
- **运行本机任务**：允许运行命令、开发服务和文档处理程序。运行的程序也可能修改文件，因此关闭“编辑文件”不等于限制命令只能读取。

新增文件夹默认允许编辑、不默认运行命令；已有文件夹保持原设置。移除访问只移除连接中的条目，不删除磁盘文件。最多可配置 32 个文件夹，中文名称和带空格的路径可用。

配置变更在保存后生效。保存按钮会明确说明是否需要重启连接；正在执行的项目任务可能被中断，请先结束相关工作。发现配置被其他窗口或程序修改时，应用会提示重新加载，不直接覆盖。

### 连接信息与密钥

连接设置提供 [隧道设置](https://platform.openai.com/settings/organization/tunnels) 和 [官方连接指南](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) 的入口。连接密钥使用具备目标隧道 Tunnels Read + Use 权限的 Runtime API key。每台电脑使用独立的 Tunnel ID；同一个 stdio 隧道只运行一个客户端实例。应用不会替用户创建平台隧道或 ChatGPT 插件。

密钥只在本机输入并写入登录钥匙串，不进入命令行参数、普通配置或 UI 响应。已有密钥无需重新填写；需要变更时使用“更换密钥”。“本机已就绪”仅表示本机隧道状态，不能替代 ChatGPT 的实际工具调用验证。

### Chrome 与 Office

Chrome 连接由 ChatGPT 独立管理，本应用提供使用指南。多台 Mac 时，应确认本机工具和 Chrome 连接对应同一台电脑。普通文件、终端和 Chrome 集成流程不要求本应用获得屏幕录制或辅助功能权限。

Office 检查会区分检测到依赖与基础读写验证。基础验证使用样例，不打开用户文档；不会自动安装 Office 或文档库。复杂版式、公式重算、PDF 导出等需要针对实际文件另外验证。

## 安装与构建

源码仓库是唯一维护入口：

https://github.com/makersns/codex-local-mcp

从源码构建需要 macOS Command Line Tools（Swift 编译器与 SDK）。预编译 App 的使用者无需安装开发工具。首次准备运行环境需要网络；实际测试覆盖范围见 [VALIDATION.md](VALIDATION.md)。

~~~bash
git clone https://github.com/makersns/codex-local-mcp.git
cd codex-local-mcp
/bin/bash ./Install_Codex_Local_MCP.command
~~~

源码安装器完成构建、安装并打开应用。安装目标为：

~~~text
~/Applications/Codex Local MCP.app
~~~

只构建、不安装：

~~~bash
/bin/bash ./build-macos.sh "$PWD/dist/Codex Local MCP.app"
~~~

输出必须是尚不存在的绝对 .app 路径。安装会验证应用标识与签名，保留可回退副本；不会因打开新窗口自动停止旧连接。

当前构建使用 ad-hoc 签名，未提供 Apple Developer ID 公证。在新 Mac 上遇到系统打开限制时，请按 macOS 的正常允许打开流程操作。

## 从旧版本升级

0.6.0 将用户可见名称统一为 **Codex Local MCP**，作者署名 **Sugar**。

为了保留已有配置，内部继续使用旧版的 Application Support 目录、Keychain 服务标识和受管连接标识。无需复制密钥或重新添加文件夹。旧 Python 模块和命令保留兼容，同时提供 codex-local-mcp 命令入口。

~~~text
~/Library/Application Support/Sugar Local MCP Desktop/
~~~

新界面读取现有配置，并显示是否需要重启后台。完成正在运行的任务后，通过“重启连接”应用新后台。必要时在 ChatGPT 刷新该连接的工具清单，再实际调用 workspace_list 核对版本和目录。

## 技术结构

| 路径 | 用途 |
| --- | --- |
| macos/ | SwiftUI / AppKit 原生界面、状态与交互 |
| desktop_api.py | stdin / stdout JSON 控制接口 |
| desktop.py | 既有连接生命周期与兼容逻辑 |
| workspace_config.py | 完整配置校验、并发检查、原子保存 |
| security_store.py | macOS 登录钥匙串 |
| bootstrap.sh | 独立运行环境准备与校验 |
| build-macos.sh、install-app.sh | 构建、验签和安装 |
| server/ | MCP 核心服务、文件及进程工具 |
| tests/、server/tests/ | 配置、连接、核心与发布测试 |
| docs/IMPLEMENTATION_PLAN_0.6.0.md | 本轮实施方案与验收要求 |
| docs/DESKTOP_API_V1.md | 原生界面与后台接口契约 |

原生界面使用短生命周期子进程连接控制 API，不开放管理 HTTP 端口。不同文件夹或聊天之间不构成操作系统进程沙箱；命令使用当前 macOS 用户权限。工具边界详见 [核心说明](server/README.md) 与 [安全说明](server/docs/SECURITY.md)。

## MCP 核心单独使用

核心仍支持 Python 3.11 及以上，使用固定依赖。把服务源码、运行环境与配置放在待开发项目目录之外：

~~~bash
cd server
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -c constraints.txt -e .
codex-local-mcp --version
~~~

完整配置、stdio、进程管理及 Office 使用方式见 [server/README.md](server/README.md)。旧命令 sugar-local-mcp 保留兼容。

## 验证与问题反馈

测试数量、构建校验、实际原生窗口截图与未覆盖的平台，以 [当前验证记录](VALIDATION.md) 为准。旧版本记录独立保留，不计入新版通过结果。

问题反馈请附版本、macOS 版本、操作步骤与脱敏错误。不要公开 Runtime key 或私有项目内容。先查看界面内的恢复建议，必要时从“运行记录”打开本机日志目录。

[更新说明](RELEASE_NOTES.md) · [0.6.0 实施方案](docs/IMPLEMENTATION_PLAN_0.6.0.md) · [历史验证](VALIDATION_0.5.0_HISTORY.md)
