# Sugar Local MCP 0.5.0

让 ChatGPT 通过 MCP 使用真实本机项目：读取与修改代码、运行构建和测试、管理开发进程，并调用本机环境处理 Office 文件。网页调试使用独立的 **ChatGPT Chrome 集成**。

macOS 日常使用推荐配套 **Sugar Local MCP Desktop 0.5.0**：双击 App，填写 Runtime key 和 Tunnel ID，选择一个或多个项目即可连接。下面保留源码安装、配置和工具说明。

本项目使用 `mcp==2.3.0`，依赖版本由 `constraints.txt` 固定。本项目本身不调用模型 API；模型由 ChatGPT 或其他 MCP 客户端提供。连接方式见 [CONNECT_CHATGPT.md](docs/CONNECT_CHATGPT.md)，本版实际验证范围见 [VALIDATION.md](docs/VALIDATION.md)。

## 工具清单

| 工具 | 用途与启用条件 |
| --- | --- |
| `workspace_list` | 列出项目及权限，默认启用 |
| `list_directory` | 分页列出项目目录，默认启用 |
| `read_file` | 按行读取 UTF-8 文件，返回整文件 SHA-256 |
| `search_text` | 在项目中搜索单行字面文本 |
| `write_file`、`edit_file` | 新建/修改文件，需要对应项目 `allow_write` |
| `run_command` | 启动测试、构建或开发服务，需要对应项目 `allow_exec` |
| `process_list`、`read_process` | 查看作业及分页输出，需要执行权限 |
| `send_process_input`、`stop_process` | 发送 stdin、EOF 或停止作业，需要所属项目执行权限 |
| `macos_permissions_status`、`macos_permissions_request` | 手动检查/申请实际 MCP 进程的 macOS 权限，需要至少一个项目允许执行 |
| `office_status`、`office_verify` | 检测本机文档环境、验证基础样例，需要至少一个项目允许执行 |

写入与执行均启用时最多 15 个工具；纯只读配置为 4 个。工具会检查所属项目的开关，ChatGPT 实际可调用范围还取决于账号、工作区设置及连接发现的工具清单。0.5.0 不再提供自建浏览器控制、原生桌面输入或自动操作暂停工具。

## 源码安装

macOS、Linux 或 Windows 的 WSL2 环境，需要 Python 3.11 或更新版本。Desktop 自带的安装流程使用独立 Python 3.12，不要求先运行这些命令。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -c constraints.txt -e .
sugar-local-mcp --version
```

把服务源码和虚拟环境放在待开发项目之外。项目使用的 Node、Java、Git、数据库等工具链由本机提供，不会随桥接服务自动安装。

### 配置项目

把以下路径换成已有项目的绝对路径；首次默认只读：

```bash
sugar-local-mcp init   --project /absolute/path/to/your-project   --name project   --config "$HOME/.config/sugar-local-mcp/workspaces.json"

sugar-local-mcp doctor   --config "$HOME/.config/sugar-local-mcp/workspaces.json"
```

`init` 不覆盖已有配置。新建配置时可添加 `--allow-write --allow-exec`。`doctor` 检查本地配置并输出绝对路径的 `mcp_command`，不验证 ChatGPT 远端连接。

配置支持多个项目，例如：

```json
{
  "workspaces": [
    {
      "id": "frontend",
      "root": "/Users/you/Projects/myapp-web",
      "allow_write": true,
      "allow_exec": true
    },
    {
      "id": "backend",
      "root": "/Users/you/Projects/myapp-api",
      "allow_write": true,
      "allow_exec": true
    }
  ]
}
```

前后端在同一个根目录时只需添加共同根，命令通过 `cwd` 选择子目录。配置和 `state_dir` 必须位于项目之外；可写项目不得覆盖服务源码或 Python 环境。Desktop 可以管理这些设置；保存后重启后台，再让 ChatGPT 查询 `workspace_list`。变更工具权限时同时刷新插件清单。

**命令具有启动服务的系统用户权限。** 工作目录与项目列表不构成进程沙箱。开启 `allow_exec` 后程序可以写入文件，即使 `allow_write` 为 false。进一步说明见 [SECURITY.md](docs/SECURITY.md)；需要容器时可使用尚需自行验收的 [CONTAINER.md](docs/CONTAINER.md)。

### 启动连接

按 [连接说明](docs/CONNECT_CHATGPT.md) 一次性创建官方隧道 profile 后，运行：

```bash
sugar-local-mcp tunnel --profile sugar-local
```

若环境中没有 `CONTROL_PLANE_API_KEY`，此命令在本机隐藏输入 Runtime key，不写入 argv 或配置。它是前台进程，关闭终端会影响连接。源码目录的 `bash start-local.sh` 可使用同目录 `.venv` 启动默认 profile；Desktop 则管理后台连接。

## 文件修改

路径是项目内的相对路径，根目录使用 `.`。服务拒绝路径越界、符号链接、非普通文件及部分常见敏感名称。名称排除不是通用凭据检测。

`read_file` 默认 200 行、单次最多 1,000 行；通过 `next_line` 继续读取。其 `sha256` 对应完整文件。修改已有文件必须提供此哈希，新建使用 `expected_sha256: "missing"`。不要用截断内容覆盖完整文件。

修改前的旧内容保存在项目外的 `state_dir/backups/<workspace>/`，`backup_id` 对应原内容和元信息。备份不自动清理，恢复由用户在本机核对后进行。哈希校验降低覆盖并发修改的风险，但不能锁住外部编辑器。

`search_text` 区分大小写、按字面匹配，使用 `next_offset` 翻页；`scan_truncated: true` 表示扫描已达限额。新文件的父目录需要存在；本服务没有独立的目录移动、删除或备份恢复工具。

## 进程与长任务

命令参数使用数组，无隐式 shell 展开，例如：

```json
{
  "workspace": "frontend",
  "argv": ["npm", "test"],
  "cwd": ".",
  "timeout_seconds": 300
}
```

`run_command` 立即返回 `job_id`。用 `read_process` 读取，第一次 `cursor: 0`，之后使用返回的 `next_cursor`。直到 `output_complete: true` 且 `has_more: false`，才表示输出已生产并读取完毕；再检查 `exit_code` 与 `cleanup_error`。

游标按规范化 UTF-8 字节计数，不要按中文字符数推算。`truncated: true` 表示较早输出已超出保留上限。启动响应不确定时先查 `process_list`，避免重复构建、安装或迁移。

默认最多 4 个并发作业、100 个记录，每作业保留最近 1 MiB 输出；默认超时 300 秒，调用可指定 1–86,400 秒。开发服务保持前台运行，不要使用 `&` 或自行 daemonize。stdin 是管道而非 PTY，不支持完整终端界面。作业保存在内存，重启不恢复；退出服务会尝试停止其管理的普通进程组。若出现清理错误，应按返回结果检查，不把退出状态等同于所有后代进程已结束。

## Chrome 网页调试

使用 Sugar 启动本机项目并检查端口就绪，使用 ChatGPT 已连接的 Chrome 集成打开该地址、操作页面并检查实际结果。Sugar 不管理 Chrome 的连接、标签页或登录资料。

同一聊天需要分别选择正确的 Sugar 隧道与 Chrome 连接。尤其在两台 Mac 之间切换时，选择 MCP 不会自动选择 Chrome 设备。先用目标 Mac 上含唯一标记的 `127.0.0.1` 页面验证同机对应，再做真实项目操作。

普通文件/终端与此浏览器流程不需要授予 Sugar 屏幕录制或辅助功能。两条连接分别管理；停止 Sugar 不等于停止 ChatGPT 的 Chrome 控制。

## 可选系统权限与 Office 检测

macOS 权限工具检查实际 MCP 进程的辅助功能、输入事件发送与屏幕录制预检结果。检查不截图、不弹出申请；请求仅在明确调用时使用系统公开 API，返回申请状态和实际授权状态。Desktop 的私有 IPC 用于同进程检查，避免把另一个诊断进程的权限误认为后台权限。全只读配置不开放申请能力。

`office_status` 区分 MCP 运行环境与本机已有 Python，`detected` 表示检测到依赖。按 `capabilities.xlsx/docx/pptx.python` 使用每种格式对应的解释器。`office_verify` 在内存样例上创建、保存、重读、修改并再次读取文件，分别返回 `verified`、`missing`、`failed` 或 `timed_out`。

可选 `include_libreoffice: true` 使用临时独立配置检查 LibreOffice 版本；`version_verified` 不表示转换、公式重算或 PDF 导出通过。检测不自动安装依赖。

实际 Office 修改通过 `run_command` 使用本机脚本完成。`.docx/.xlsx/.pptx` 是二进制容器，不能当 UTF-8 文本覆盖。脚本写入需要自行保留原件、检查冲突和验证实际内容与版式，不自动继承文本工具的备份保护。

## 其他启动方式

直接 stdio，供官方隧道或本地 MCP 客户端启动：

```bash
sugar-local-mcp serve --config "$HOME/.config/sugar-local-mcp/workspaces.json"
```

在终端等待输入是正常现象，stdout 专供 MCP，日志写 stderr。本地调试可选：

```bash
sugar-local-mcp serve   --config "$HOME/.config/sugar-local-mcp/workspaces.json"   --transport http --port 8765
```

端点为 `http://127.0.0.1:8765/mcp`，只监听本机，没有应用层 OAuth，不应直接公开转发。

## 版本升级与验证

启动 0.5.0 后，在 ChatGPT **Plugins → 对应 Sugar 连接 → Refresh**，核对工具再开新对话。后台版本变新不表示原聊天已经更新工具。官方流程见 [连接与测试](https://developers.openai.com/plugins/deploy/connect-chatgpt)。

测试和平台覆盖范围均按 [当前验证记录](docs/VALIDATION.md) 列示，历史报告单独保留。源码中的工具测试可以运行：

```bash
python -m unittest discover -s tests -v
```
