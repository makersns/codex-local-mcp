# 连接 ChatGPT 网页版

Sugar Local MCP 0.5.0 使用 OpenAI Secure MCP Tunnel：本机 tunnel-client 主动连接 OpenAI，再把 MCP 调用转发给本地 stdio 服务。无需公开本机入站端口。

macOS 日常使用推荐配套 Desktop App：填写 Runtime key、Tunnel ID 和项目目录，保存并连接。以下手动配置供源码部署使用；不要让 App 与手动进程同时占用同一个 stdio 隧道。

官方资料核对日期：2026-11-10。菜单、账号与工作区入口可能变化，以实际界面和[官方连接说明](https://developers.openai.com/plugins/deploy/connect-chatgpt)为准。

## 1. 准备本地服务和隧道客户端

按 [README](../README.md) 安装核心，运行 `init` 与 `doctor`。记下 `doctor` 输出的 `mcp_command`，其中 Python、配置和项目路径需要为绝对路径。

macOS 官方安装方式：

```bash
brew install openai/tools/tunnel-client
tunnel-client --version
```

Linux、Windows 可从 [官方 Releases](https://github.com/openai/tunnel-client/releases/latest) 获取对应版本。本文需要完整 tunnel-client 的 profile 管理能力，名称含 runtime 的精简程序不提供同样的初始化命令。Windows 终端示例建议在 WSL2 中执行，并使用 WSL2 可访问的项目路径。

## 2. 准备自己的 Tunnel ID 与 Runtime key

在 [Platform Tunnel Settings](https://platform.openai.com/settings/organization/tunnels) 创建或选择隧道，将它关联到目标 ChatGPT 工作区，并复制真实 ID。创建/编辑身份需要 Tunnels Read + Manage；运行身份需要该隧道的 Tunnels Read + Use。

运行进程使用 Runtime API key，不使用 Admin key。Desktop 将 key 保存到本机登录钥匙串；源码启动可在本机隐藏输入。无需把 key 发送到 ChatGPT。

两台 Mac 各用独立 Tunnel ID。同一个 stdio 隧道只保持一个活动客户端，以免初始化与后续请求被路由到不同进程。

## 3. 配置并启动 stdio profile

以下示例在已激活项目虚拟环境的 macOS/Linux/WSL2 终端运行。替换隧道 ID：

```bash
tunnel-client init   --sample sample_mcp_stdio_local   --profile sugar-local   --tunnel-id tunnel_替换成实际ID   --mcp-command "'$VIRTUAL_ENV/bin/python' -m sugar_local_mcp serve --config '$HOME/.config/sugar-local-mcp/workspaces.json'"
```

外层引号在当前终端展开绝对路径，内部引号保留给命令解析。如果路径含单引号，使用 `doctor` 给出的完整命令，正确保留命令引用，不要把 JSON 的转义符复制为路径内容。

在本机隐藏输入 key 后可先运行官方诊断：

```bash
read -r -s CONTROL_PLANE_API_KEY
export CONTROL_PLANE_API_KEY
tunnel-client doctor --profile sugar-local --explain
sugar-local-mcp tunnel --profile sugar-local
```

`doctor` 检查配置与本机依赖，不代替 ChatGPT 实际调用。之后的新终端若没有此环境变量，`sugar-local-mcp tunnel` 会隐藏询问 key。此封装不将 key 写入配置或命令参数。

源码模式保持终端进程在线；退出使用 Ctrl+C，再按需要 `unset CONTROL_PLANE_API_KEY`。Desktop 模式可以关闭状态窗口保留后台，停止和重启使用 App 明确入口。

## 4. 在 ChatGPT 添加插件

1. 打开 **Plugins → Add custom MCP server**。
2. 设置可辨识的名称，例如 `Sugar · 家里 Mac`。
3. Connection 选择 **Tunnel**，选择已关联的目标隧道。
4. 本服务没有独立应用登录或 OAuth。在只授权自己使用的私有隧道中，使用无应用层认证选项，由隧道控制面限制访问；组织要求 OAuth 时，需要另行实现身份层。
5. 检查工具清单，创建并启用插件，在新对话中选择它。

这是单用户开发桥。连接此实例的会话共用真实文件、进程和系统用户权限，不应作为团队共用终端发布。账号是否可以发现或调用某项工具，以实际工作区设置、工具清单和审批结果为准。若界面只支持公开 URL，不要填写 localhost，也不要将本服务的未鉴权 HTTP 端口直接公开。

## 5. 验证文件、终端与 Chrome

先让 ChatGPT 调用 `workspace_list`，确认项目名称、路径和权限，再读取一个项目的 README。需要修改或执行时，在本机为对应项目开启权限，测试一个可丢弃文件或项目规定的测试命令，并检查读回内容、作业输出和退出码。

网页调试使用 **ChatGPT 的 Chrome 集成**：

1. Sugar 按项目说明启动前后端，读取日志并确认实际就绪地址。
2. 在 ChatGPT 中连接目标 Mac 的 Chrome。
3. 在目标 Mac 上生成含唯一标记的临时回环测试页，用 Chrome 集成打开并核对标记，确认两条连接指向同一台电脑。
4. 再打开真实开发页面，检查页面状态、表单、后端结果及集成提供的控制台信息。

**选择 Sugar 的 Tunnel 不会自动切换 Chrome 设备。** 两台电脑都可能使用 `localhost:3000`，仅看 URL 不能证明同机。Sugar 不启动或控制浏览器，普通文件、终端和此 Chrome 流程无需给 Sugar 授予屏幕录制/辅助功能。

Office 需要先检测 `office_status`，必要时验证 `office_verify`。基础样例通过不代表真实文档版式、公式或 PDF 导出通过。实际验证范围见 [VALIDATION.md](VALIDATION.md)。

## 升级为 0.5.0 后刷新工具

1. 安装新版并在 App **重启连接**，或停止旧 CLI 进程再启动新版。
2. 在 ChatGPT 的 Plugins 中打开对应 Sugar 连接，选择 **Refresh / 刷新**。
3. 核对工具清单。写入和执行均开启时有 15 个工具，纯只读时有 4 个；已移除的 Sugar 浏览器、原生输入和暂停工具不应继续作为新工具使用。
4. 开新对话，重新选择该插件并验证调用。

后台返回 `0.5.0` 只证明请求到达该版本，不证明旧聊天已加载新工具。正常升级无需重建隧道或更换 key。刷新流程依据 [OpenAI 连接与测试文档](https://developers.openai.com/plugins/deploy/connect-chatgpt)。

## 长任务和常见问题

隧道单次请求有截止时间与消息大小限制。本服务用 `run_command` 启动作业，再用 `read_process` 分页读取。启动响应不确定时先查 `process_list`，不要盲目重试安装、构建或迁移。停止使用 `stop_process`，然后检查状态与 `cleanup_error`。

| 现象 | 检查方向 |
| --- | --- |
| ChatGPT 看不到隧道 | 工作区关联、运行身份的 Tunnels Read + Use、账号实际入口 |
| 本机 ready，但 ChatGPT 调用失败 | 实际远端调用、控制面网络和 DNS、凭据与隧道关联；本地 ready 不证明端到端成功 |
| 发现工具失败 | profile 的绝对路径、MCP 是否导入成功、stdio stdout 是否被额外打印污染 |
| 只有读工具 | 本地项目写入/执行开关、工作区策略，刷新连接后开新对话 |
| 重开 App 仍是旧后台 | 使用明确的“重启连接”入口；关闭窗口不等于停止后台 |
| `npm`、`java` 找不到 | 本机工具是否安装、启动时 PATH；需要时使用绝对可执行路径 |
| 项目代理或工具链变量缺失 | 作业环境只继承少量变量，不会默认透传所有 token、代理或 JAVA_HOME |
| 日志仍未读完 | 按 `next_cursor` 分页，直到 `output_complete && !has_more`，检查退出码与清理错误 |
| Chrome 打不开开发地址 | 检查 Chrome 集成设备与 Sugar 所在 Mac，核对本机服务与端口；没有必要因此申请 Sugar 屏幕权限 |
| Office 依赖已安装却没检测到 | 查看各解释器的 `capabilities`，按格式使用对应 Python；可刷新缓存 |
| LibreOffice 检查超时 | 只说明版本探测未完成，不能据此承诺转换或重算可用 |

## 官方参考

- [ChatGPT 自定义 MCP](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt)
- [插件连接与测试](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [Secure MCP Tunnel 指南](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [tunnel-client 安装与进程管理](https://github.com/openai/tunnel-client)
- [tunnel-client onboarding](https://github.com/openai/tunnel-client/blob/master/docs/onboarding.md)
- [公开端点的 MCP OAuth 要求](https://developers.openai.com/plugins/build/auth)
