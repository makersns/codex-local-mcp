# Codex Local MCP

**Codex Local MCP** 是 Sugar Local MCP Desktop 的公开源码仓库，当前版本为 **0.5.0**。仓库名称为 `codex-local-mcp`；本版 macOS App、Python 包与命令仍使用 `Sugar Local MCP Desktop` 和 `sugar-local-mcp`，与已有安装及配置兼容。

让 ChatGPT 在你的 Mac 上读取和修改项目代码、运行开发服务和测试。网页调试使用你已连接的 **ChatGPT Chrome 集成**；本应用负责本机项目和终端。

这是个人开发桥，命令使用当前 macOS 用户的真实权限。它不是 OpenAI 官方客户端，使用 OpenAI 官方 tunnel-client 建立连接。

本仓库包含 **0.5.0 完整源码**：macOS 桌面启动器、图形配置界面、MCP 服务、安装与构建脚本、测试及验证文档。通过 GitHub 下载源码 ZIP 后，先完整解压，再按下面的源码安装方式操作。

## 主要能力

- 管理多个项目目录，分别设置文件写入和命令执行权限。
- 读取、搜索和修改代码；文本修改使用完整文件 SHA-256 检查并保留备份。
- 启动开发服务、构建和测试，分页读取输出，发送标准输入并停止托管进程。
- 检测本机 Office 环境，验证基础文档样例，并按需检查 macOS 权限。
- 配合 ChatGPT 独立连接的 Chrome 集成调试本地网页。

## 从源码安装 macOS App

需要 macOS 自带的 `osacompile` 与 `codesign`；首次打开 App 时需要联网准备 Python 和依赖。桌面安装流程会创建自己的运行环境，无需先手动安装 Python 包。已实际验证的系统为 **macOS 15.5 / Apple Silicon**；Intel 构建入口已提供，但真实 Intel 安装尚未验证，详见 [验证范围](VALIDATION.md)。

克隆源码后，在仓库根目录运行一次安装器：

```bash
git clone https://github.com/makersns/codex-local-mcp.git
cd codex-local-mcp
/bin/bash ./Install_Sugar_Local_MCP.command
```

如果通过 GitHub 下载了源码 ZIP，进入解压后的目录，直接执行最后一条安装命令即可。

安装器会在临时目录构建 App，安装到 `~/Applications/Sugar Local MCP Desktop.app`，然后打开应用。已有连接配置和钥匙串条目保留；后续双击已安装的 App 即可使用。同版本且验签通过的已安装 App 会被复用；不同版本安装时会保留旧 App 副本。源码目录中的安装器可以直接使用，无需另外创建 `Source` 文件夹。

如需仅构建 App、暂不运行安装器：

```bash
/bin/bash ./build-macos.sh "$PWD/dist/Sugar Local MCP Desktop.app"
```

输出参数必须是**尚不存在的绝对 `.app` 路径**；构建脚本拒绝覆盖已有目标。生成的 App 可在 Finder 中打开，首次启动会执行用户目录安装和运行环境准备。构建使用 ad-hoc 签名。

## 单独使用 MCP 核心

`server/` 是独立的 Python 项目，可供本地 MCP 客户端或隧道启动。需要 Python 3.11 或更新版本；完整参数与多项目配置见 [核心 README](server/README.md)。

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -c constraints.txt -e .
sugar-local-mcp --version

sugar-local-mcp init \
  --project "/absolute/path/to/your-project" \
  --name project \
  --config "$HOME/.config/sugar-local-mcp/workspaces.json"

sugar-local-mcp serve \
  --config "$HOME/.config/sugar-local-mcp/workspaces.json"
```

将示例项目路径替换为已有项目的绝对路径。首次 `init` 默认只读；需要开发写入与执行时，在新建配置命令中添加 `--allow-write --allow-exec`。`init` 不覆盖已有配置。请将此服务源码、虚拟环境和配置保存在待开发项目之外。

`serve` 默认使用 stdio，等待 MCP 客户端输入是正常行为。它本身不建立云端连接；ChatGPT 的隧道配置与启动步骤见 [连接说明](server/docs/CONNECT_CHATGPT.md)。本项目自身不调用模型 API，模型由连接它的客户端提供。

## 源码目录与验证

| 路径 | 内容 |
| --- | --- |
| `App.applescript`、`bootstrap.sh` | App 入口与首次运行环境准备 |
| `desktop.py`、`workspace_config.py` | 图形配置、后台连接与多项目管理 |
| `permission_guide.py`、`environment_guide.py`、`security_store.py` | 权限与 Office 引导、钥匙串访问 |
| `build-macos.sh`、`install-app.sh`、`Install_Sugar_Local_MCP.command` | 本机构建和安装 |
| `dependency-manifest.json`、`server/constraints.txt` | 运行环境校验信息与固定依赖 |
| `server/src/sugar_local_mcp/` | 文件、进程、权限及 Office MCP 工具 |
| `tests/`、`server/tests/` | 桌面与核心测试 |
| `VALIDATION.md`、`server/docs/VALIDATION.md` | 0.5.0 实测结果与未覆盖范围 |

0.5.0 发布验证记录包含核心套件 124 项（123 通过、1 项可选 Office 依赖相关跳过）、桌面套件 71 项通过，以及 macOS App 构建、文件与进程、同机 Chrome 和 Office 基础样例验收。结果对应文档记录的环境与日期，不代表所有机器和项目都已验证。

## 安装与首次连接

1. 源码安装器会打开 **Sugar Local MCP Desktop.app**，以后从 `~/Applications` 或桌面入口打开即可。如果使用单独获得的完整预编译安装包，完整解压后打开其中的 App；它也会安装到当前用户的 `~/Applications`，并在可用时建立桌面入口。
2. 等待首次运行环境准备。需要联网下载并校验 Python、MCP 依赖和缺少的隧道组件；无需手动创建虚拟环境。已有环境验证通过后会复用。
3. 填写电脑名称、这台 Mac 专用的 **Tunnel ID**，在隐藏输入框填写 **Runtime API key**。key 保存到这台 Mac 的登录钥匙串。
4. 选择项目目录，按需要添加多个项目并设置各自的文件写入、命令执行开关，然后保存并连接。
5. 显示“本机隧道已就绪”后，在 ChatGPT 选择对应的 Sugar 插件，要求它调用 `workspace_list` 并读取一个项目文件。成功调用才说明 ChatGPT 到本机的完整链路可用。

如果预编译 App 无法使用，保留安装包中完整的 `Source` 文件夹，双击与其同目录的 `Install_Sugar_Local_MCP.command`。它使用 macOS 自带工具重新构建并安装 App，首次会显示终端进度；本仓库的源码安装则使用上文的根目录命令。

这是 ad-hoc 签名的个人应用，未提供 Apple Developer ID 公证。若系统阻止打开，在确认下载来源后使用 macOS“隐私与安全性”的允许打开流程。应用不会更改系统安全策略。首次安装需要网络；没有提供离线依赖包。实际构建和验证范围见 [VALIDATION.md](VALIDATION.md)。

## Tunnel ID 与 Runtime key

在 [OpenAI 隧道设置](https://platform.openai.com/settings/organization/tunnels) 创建或选择隧道，并将它关联到要使用的 ChatGPT 工作区。在 [API keys](https://platform.openai.com/settings/organization/api-keys) 管理适用于此隧道的 Runtime key。运行身份需要对应隧道的 Tunnels Read + Use 权限；不要填写 Admin key、ChatGPT 登录令牌或订阅信息。

本应用连接已有隧道；不会代替你创建平台隧道或 ChatGPT 插件。每台 Mac 使用不同 Tunnel ID，同一个 stdio 隧道只运行一个客户端实例。key 只在本机输入，不需要发送到聊天。

## 多项目与前后端联调

打开 **配置 → 管理项目目录**，可以添加、改名、换目录、调整权限或从连接列表移除项目。移除条目不会删除磁盘上的代码。

如果前后端在同一个目录里，添加共同根目录即可，命令通过各自的 `cwd` 在子目录运行。如果前后端分散在不同位置，分别添加：

| 项目名称 | 目录示例 |
| --- | --- |
| `frontend` | `/Users/you/Projects/myapp-web` |
| `backend` | `/Users/you/Projects/myapp-api` |

项目名称需要唯一，使用英文字母、数字、下划线或短横线，以字母或数字开头，最长 64 个字符；最多 32 个项目。文件夹路径可以包含空格和中文。新增项目默认开启写入与执行，已有项目保留原设置；保存前可逐项调整。

保存项目变更会重启本应用管理的连接，使后台读取新配置；请先完成正在托管的测试、构建或开发服务。随后让 ChatGPT 重新调用 `workspace_list`。变更权限时还应刷新插件工具清单。

文件工具只访问已配置项目的相对路径。命令使用真实本机权限：开启执行后，程序可以修改文件，即使文件工具的写入开关关闭。不同项目名称也不提供不同聊天之间的进程或文件隔离。

## 使用 Chrome 集成调试

在 ChatGPT 中同时使用对应 Mac 的 **Sugar Local MCP** 和已连接的 **Chrome 集成**。可直接提出：

> 使用 Sugar 检查 frontend 和 backend 的启动说明，启动前后端并读取就绪日志；确认 Chrome 集成连接的是同一台 Mac，在 Chrome 打开实际前端地址，检查页面、表单和控制台；修复代码后重新测试。

Sugar 启动和管理项目进程，Chrome 集成负责页面观察、输入、点击及网页结果检查。本应用的 **配置 → Chrome 集成使用说明** 提供这一流程。Chrome 连接需要在 ChatGPT 中独立配置，Sugar 不检测或切换这个连接。

**两台 Mac 时，选择不同 Sugar 插件不会自动切换 Chrome 的设备。** 每次开始联调时，用目标 Mac 上的 `127.0.0.1` 测试页和唯一标记确认 Chrome 打开的就是这台机器；不要只凭两边都显示 localhost 判断。

普通文件、终端和这个 Chrome 集成流程无需给 Sugar 开启屏幕录制或辅助功能。停止 Sugar 连接会影响其托管的开发进程；Chrome 集成由 ChatGPT 独立管理。此版本没有另行实现浏览器控制或通用原生桌面输入。

## 日常入口

| 操作 | 入口与行为 |
| --- | --- |
| 连接 | 双击桌面入口或 `~/Applications` 中的 App |
| 关闭设置窗口 | 状态窗口点“完成”，后台连接继续运行 |
| 重启后台 | **配置 → 重启连接**；仅重新打开窗口可能复用旧后台 |
| 停止 | 状态窗口点“停止连接” |
| 修改隧道或 key | **配置 → 修改连接设置**，保留已有项目列表 |
| 管理项目 | **配置 → 管理项目目录** |
| 检查 Office 环境 | **配置 → Office 环境检测与验证** |
| 手动检查 macOS 权限 | **配置 → 系统权限检查（可选）** |

权限检查针对实际 MCP 后台进程，并显示进程和解释器信息。它仅在手动打开时提供引导；申请调用 macOS 公共 API，最终开关由用户在系统设置操作。申请成功不等于授权已生效，后台不可达也不等于被拒绝。

## Office 文件

先检测，再按需要验证 `.xlsx`、`.docx`、`.pptx` 的基础读写。验证使用内存中的样例，不打开用户文档。检测到依赖与基础验证通过是不同结果；应使用每种格式返回的 Python 解释器。

实际文档修改通过 Sugar 的命令工具调用本机程序或脚本完成。本应用不自动安装 Office 或文档库，也不保证复杂版式、公式重算、PDF 导出或原生 Office 界面操作。修改真实文件时保留原件并检查结果。

## 从 0.4.1 升级

完成当前任务并关闭旧 App 窗口，再打开新版 App，或运行随包的一次性安装器。升级保留 Application Support 配置、项目权限、钥匙串与旧运行环境；应用安装器保留带时间戳的旧 App 副本。

若旧后台仍在运行，使用 **重启连接** 使 0.5.0 生效。如果旧服务由终端手动启动，请先在原终端停止它，避免同一隧道运行两份实例。

然后打开 ChatGPT 的 **Plugins → 对应 Sugar 连接 → Refresh**，检查工具清单，再开一个新对话选择插件。允许写入和执行的配置应显示 15 个工具；纯只读配置为 4 个。0.5.0 已移除 Sugar 自建的浏览器、原生窗口输入和暂停工具，网页操作请使用 Chrome 集成。完整清单见 [核心 README](server/README.md)。官方刷新流程见 [连接与测试](https://developers.openai.com/plugins/deploy/connect-chatgpt)。

完整清单按读写注解分为 **Read 9 / Write 6**。如果 App 详情页已经显示 15 个，聊天仍只获得部分工具，请展开清单核对具体名称及启用状态；连接详情的清单与当前聊天实际可调用清单需要分别确认。只新开聊天或只看到后台版本变为 0.5.0，都不足以证明工具已更新。

## 排查连接

“本机隧道已就绪”是本地健康检查结果，不代替 ChatGPT 工具调用。若连接不可用，先看应用日志中的具体错误：配置、网络、DNS、凭据或隧道关联；不要把这些问题直接归因于屏幕录制权限。

配置与运行日志位于 `~/Library/Application Support/Sugar Local MCP Desktop/`，可从 App 的日志入口打开。启动准备日志位于其中的 `logs/bootstrap.log`。报告问题时提供版本、系统、工具名和脱敏错误，不附 Runtime key 或完整项目内容。

项目所需的 Node、Java、数据库、Git 和 Office 依赖由本机提供。两台 Mac 的运行环境、Chrome 连接和隧道需要分别验证；不同隧道仍可能共同修改远端 Git 项目，合并代码时继续使用正常分支或 worktree 流程。

[更新说明](RELEASE_NOTES.md) · [验证范围](VALIDATION.md) · [运行环境细节](docs_BOOTSTRAP.md)
