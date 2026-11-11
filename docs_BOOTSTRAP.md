# macOS 运行环境安装模块

此模块属于 Sugar Local MCP Desktop 0.5.0，随包核心 MCP 服务为 0.5.0。它只准备运行环境，不读取 OpenAI Runtime key，不创建隧道，也不启动 MCP 服务。GUI 负责连接、多项目与逐项目权限、重启入口、可选的系统权限检查、Chrome 集成使用说明和 Office 环境检测。网页操作由 ChatGPT 的独立 Chrome 集成完成，Sugar 不启动或管理 Chrome。

`workspace_config.py`、`permission_guide.py`、`environment_guide.py` 与 `desktop.py` 一同位于 Resources；全部核心服务源码参与运行环境摘要。MCP SDK 是核心的唯一直接依赖，其实际依赖版本记录在 `server/constraints.txt`。已移除自建浏览器需要的直接 WebSocket 依赖。Office 文档处理库由本机环境提供，不随桥接运行时自动安装。

## 调用约定

```bash
/bin/bash "<Resources>/bootstrap.sh" "<Resources>" "<用户专属 Application Support 目录>"
```

两个参数都必须是绝对路径，支持路径中的空格和中文。Resources 目录应包含 `server/pyproject.toml`、`server/src/sugar_local_mcp/`、`server/README.md` 以及 `server/constraints.txt`。为兼容打包布局，也接受 Resources 根目录下的 `constraints.txt`。

成功时退出码为 0，stdout 只输出一行实际虚拟环境 Python 的绝对路径。进度和错误输出至 stderr，同时写入 `<support>/logs/bootstrap.log`。GUI 应持续显示进度，并在失败后保留重试入口。首次联网下载可能需要数分钟。

成功后原子更新 `<support>/runtime-info.json`，字段包括：

| 字段 | 用途 |
| --- | --- |
| `python` | 已安装、已验证的虚拟环境解释器绝对路径 |
| `tunnel_client` | 可运行的官方 tunnel-client 绝对路径 |
| `arch` / `machine` | 本机架构，`arm64` 或 `x86_64` |
| `service_version` | 实际已安装的 Sugar Local MCP 版本 |
| `python_version` | 实际 Python 3.12 补丁版本 |
| `tunnel_version` | 客户端 `--version` 检测到的版本 |
| `tunnel_source` | `existing`、`homebrew` 或 `portable` |
| `uv_version` | 此包固定的 uv 版本 |
| `source_digest` | 服务源码、依赖约束和安装选项的摘要 |
| `verified_at` | 本次验证时间，UTC ISO 8601 |

GUI 的 MCP command 应显式使用 `/usr/bin/arch -arm64 <python> ...` 或 `/usr/bin/arch -x86_64 <python> ...`，其中 `<python>` 来自该 JSON。使用 `shlex.quote` 等正确处理 command 字符串中的路径。不要把 key 拼入 command。

## 架构、版本与复用

硬件检测优先读取 `sysctl hw.optional.arm64`，因此即便由 Rosetta 下的 Intel 启动程序拉起，也会安装并运行原生 Apple Silicon Python。只有确认不支持 ARM 后才采用 Intel 路径。

uv 固定为 0.13.7；下载资产的 SHA-256 固定在安装器和依赖清单中。Python 使用该 uv 版本的发行目录中最新的 CPython 3.12 补丁，解释器存放在用户专属目录。MCP 及其他 Python 依赖使用包内 constraints 精确版本；首次安装要求对应平台的预编译依赖 wheel，不会为了这些依赖自动编译 Rust/C 扩展。Sugar Local MCP 自身是纯 Python，先复制至 support 的私有暂存目录，再按正常 Python 包构建、安装；不会把 egg-info、build 或 __pycache__ 写入应用包 Resources。

运行环境路径包含服务源码与 constraints 的摘要。新源码会创建新的虚拟环境，已运行的旧服务可以继续使用旧环境。重复打开会验证 Python 架构、版本、服务导入以及全部 constraints 中的已安装版本；验证通过时无需升级或重新联网安装。未完整安装的环境会改名保留后重试安装，不会清理用户项目目录。

隧道客户端优先复用已可运行且不低于 0.0.20 的本地版本；其次复用本安装器已安装的可运行版本；仍缺失时使用本机原生 Homebrew 安装官方 `openai/tools/tunnel-client`。没有原生 Homebrew 时才尝试固定 SHA-256 的官方 0.0.20 便携 ZIP，并保留完整客户端、cloudflared 和随附清单。

上游明确说明 macOS release ZIP 尚未公证，推荐通过官方 Homebrew 安装。若 macOS 拒绝运行便携客户端，安装会停止并指向官方 Homebrew 方式；不会删除 quarantine、修改 Gatekeeper 或禁用 TLS。便携回退不意味着每台 Mac 都能零依赖、零确认安装。

## 本地目录与联网范围

所有安装器自有文件位于传入的用户专属 support 目录：`tools/`、`pythons-<arch>/`、`runtimes/`、`cache/uv/`、`logs/`、`tmp/` 和 `runtime-info.json`。目录及新文件采用仅当前用户可访问的权限。安装器无需 sudo，不修改 shell 配置，不配置登录启动项。

首次安装需要能访问 Astral 官方发行下载站、GitHub 官方 release/资产、PyPI 和 Python 依赖下载站。公司代理和证书相关环境变量会保留，但不会读取 uv 项目配置或继承额外包索引设置。安装器只使用 HTTPS 和固定二进制摘要；Python 依赖采用版本约束，不是包含全套包哈希的离线 wheel 仓库。断网的新 Mac 无法完成首次安装。

固定资产 URL、SHA-256 与校验来源记录在 `dependency-manifest.json`。该清单描述这个版本的依赖，不代表后续永远自动追踪最新上游发行版。

## 验证边界

云端可以检查 Bash 语法、JSON 格式和无真实网络下载的安装控制流；实际 macOS 架构、Gatekeeper、Homebrew 和首次联网安装仍需在目标 Mac 上运行验证。程序不读取任何用户现有隧道配置，不停止已有隧道，不注册外部连接。
