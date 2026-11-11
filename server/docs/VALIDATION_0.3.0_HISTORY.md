> 历史记录：以下结果属于 0.3.0 及更早版本，不是 0.4.0 验收结果。

# Sugar Local MCP 验证记录

## 0.3.0 当前版本：2026-11-10

核心服务在 macOS 15.5、原生 arm64、独立 uv Python 3.12.16 环境中 **112 项自动化测试全部通过（14.114 秒）**。Linux Python 3.12.14 中通过 99 项；执行器禁止 AF_UNIX socket，13 项真实 IPC 测试整类跳过，这 13 项在 Mac 已全部通过。

新增覆盖 macOS 权限公共 API 封装、私有诊断 socket 权限和并发/超时、显式授权请求、只读配置门控、本机 Chrome 生命周期、CDP 错误与消息大小、MCP 图像返回和退出清理。

配套 Desktop 的 55 项测试也在云端和 Mac 通过，9 份 AppleScript 原生模板编译通过。实际 stdio MCP 新版服务列出 20 个工具，启动临时前端和后端，打开本机 Google Chrome 独立资料，完成 DOM 检查、页面点击和输入、后端请求、控制台日志及 86,535 字节 PNG 图像返回。GUI IPC 与 MCP 权限工具确认同一后台 PID；当时 screen_capture=false、accessibility=true、post_event=true，没有申请或更改真实系统权限。

测试 Chrome、开发进程、socket 和临时项目均已清理。原有连接 ChatGPT 的 0.1.0 服务继续运行，新版未替换现有隧道。没有用第二台 Mac 的 Runtime key/Tunnel ID 做新版远程端到端验收，也没有实际人工确认系统授权弹窗。完整桌面发布和验签记录见安装包顶层 VALIDATION.md。

## 以下为 0.2.0 历史验证记录

验证日期：2026-11-10（Asia/Shanghai）。

## 本轮结论与范围

**0.2.0 核心服务的 62 项自动化测试，已分别在云端 Linux 和真实 macOS 15.5 上全部通过。**

macOS 验证使用单独准备的 uv Python **3.12.16 / arm64** 构建运行环境，在该环境安装新版源码并执行测试。这是隔离运行环境中的新版验证，**不代表已把当前连接 ChatGPT 的 0.1.0 服务升级为 0.2.0**。这里的“隔离运行环境”指独立的 Python 解释器和依赖环境，不代表进程受到操作系统沙箱限制。

现有 0.1.0 实例已经建立的真实 ChatGPT 工具连接，与本轮 0.2.0 测试分开记录。不能将旧实例的连通性，算作新版 macOS 安装包在其他电脑上的端到端验收。

| 验证环境 | 运行时 | 实际结果 |
| --- | --- | --- |
| 云端 Linux | Python 3.12.14，官方 MCP SDK 2.3.0 | `Ran 62 tests in 7.345s`，`OK` |
| 真实 macOS 15.5 | 隔离的 uv Python 3.12.16，原生 arm64，官方 MCP SDK 2.3.0 | `Ran 62 tests in 7.407s`，`OK` |

依赖约束见根目录 `constraints.txt`。它是版本约束文件，不是带有下载哈希的跨平台锁文件。

## 0.2.0 新增验证

本轮针对此前 macOS 上 Python 与 `pydantic_core` 架构不匹配、旧 `doctor` 仍报告通过的问题，新增了 9 项 CLI 回归测试。

| 验证项 | 已确认的行为 |
| --- | --- |
| 真实运行时导入 | `doctor` 实际导入 `.server`，包括 MCP、Pydantic 及其原生依赖；输出 `python_executable`、`process_architecture` 和 `runtime_import_ok` |
| 诊断不启动服务 | `doctor` 不调用 `build_server`，不启动 MCP 服务或托管任务 |
| 依赖导入失败 | 在全新 Python 子进程的 `pydantic_core` 导入边界注入架构 ImportError；即使安装元数据存在，`doctor` 仍返回退出码 2，并在 JSON 中保留错误、建议、配置和工作区信息 |
| stdio 错误输出 | `serve` 的依赖 ImportError 输出到 stderr，stdout 保持为空；返回退出码 2，不打印长 Traceback |
| 非导入错误 | 其他异常仍保留原有错误信息与 Traceback，不被转换为成功或普通依赖故障 |
| macOS 启动架构 | 在 Darwin 的 arm64/x86_64 环境生成 `/usr/bin/arch -对应架构 ...` 命令，避免隧道父进程影响 universal Python 的架构选择 |
| 平台与路径边界 | 含空格和单引号的路径正确引用；未知 Darwin 架构不猜测；Linux 保持直接启动；Windows 保持原有命令行引用方式 |

Darwin、Linux、Windows 命令生成的边界用例包含平台模拟测试。真实 macOS 的 62 项通过证明该版本在上述原生 arm64 运行环境中的核心测试通过，并不等于原生 Windows 或所有 macOS 版本均已验证。

## 核心服务自动化测试分布

- CLI：9 项。覆盖真实导入、故障退出码、保留诊断信息、不启动服务、stdio 输出、意外异常传播和平台命令生成。
- 配置：10 项。覆盖重复 ID、未知字段、只读默认、配置/备份不能落入导出项目、服务代码与 Python 环境不能被设为可写项目等。
- 文件工具：18 项。覆盖路径越界、符号链接和替换竞态、敏感名称过滤、UTF-8/CRLF、FIFO/二进制/大文件拒绝、分页、哈希冲突、原子创建及备份。
- 进程管理：18 项。使用真实子进程验证 stdout/stderr、非零退出、即时返回、stdin/EOF、输入背压、超时、输出限额、中文游标、环境变量过滤、并发容量、终止升级与普通进程组清理。
- MCP 接口：5 项。验证只读工具发现、禁止调用、跨项目能力检查、写入冲突对模型可见、真实 stdio、HTTP Host/Origin。
- 服务关闭：2 项。验证空闲输入和输出背压两种情况下 SIGTERM 均能关闭服务、清理作业。

以上合计 62 项，在两个环境各执行一轮。

## 历史证据与本轮复验的区别

0.1.0 曾在云端 Linux / Python 3.12.14 上完成 53 项测试，以及独立的真实 loopback HTTP 检查：启动 uvicorn，通过 127.0.0.1 TCP 和官方 MCP 客户端初始化、发现 4 个只读工具并读取中文文件，协商协议为 2025-11-25。

这项独立 TCP 检查属于 0.1.0 历史证据。本轮 62 项中包含重新执行的真实 stdio 测试和 HTTP 应用协议测试，不能据此把历史 TCP 记录改写为 0.2.0 新完成的独立 TCP 验收。历史 loopback 检查没有访问公网或公开服务端口。

## SDK 适配说明

使用官方 `MCPServer`、工具 schema 和传输协议处理，没有自行实现 MCP JSON-RPC 协议。为解决 SDK 2.3.0 的阻塞管道 I/O 在取消时可能阻止退出的问题，`stdio_transport.py` 在 POSIX 上提供可取消的字节流适配。

由于该版 SDK 的 `run_stdio_async` 不接收自定义流，这一适配使用了一个集中封装的 `_lowlevel_server` 私有接口。升级 SDK 时应先重新执行真实 stdio、EOF、SIGTERM 和输出背压验收，不能只改依赖版本。

## 安装包与尚未完成的验证

本轮已在 Mac 上生成 `.app`，但**“已生成应用”和“核心测试通过”不构成第二台电脑端到端连接或生产验证完成的结论**。

应用签名和资源完整性由 Desktop 安装包另行检查，结果见包外层的 `VALIDATION.md`。测试子进程曾在已签名应用的 `Resources/server/src` 下生成 `__pycache__`；正式包排除测试缓存，运行入口禁用 Resources 中的字节码写入，依赖构建使用 Application Support 中的源码副本。这些安装验证不属于上述 62 项核心服务测试。个人应用采用 ad-hoc 签名，未进行 Apple Developer ID 公证，也未在全新电脑上验证首次 Gatekeeper 交互。

以下边界仍需明确保留：

- **第二台 Mac 安装新版应用后，经真实隧道在 ChatGPT 中完成工具发现、读取、写入和命令执行的端到端测试尚未完成。**
- 本轮隔离运行时测试未替换当前在线的 0.1.0 实例，也未证明新版安装包已经接管现有隧道。
- Dockerfile、容器资源限制和挂载方案尚未实测，仍属于部署模板。
- WSL2 与原生 Windows 未进行现场测试；原生 Windows 的完整进程树清理、信号处理和文件竞争条件防护不能视为已验证。
- Python 依赖隔离与文件工具的项目路径检查，不会把任意终端命令限制在项目内。`allow_exec` 仍授予启动服务的系统用户权限。
- 真实账户的 Tunnel 可用性、工作区策略和工具权限，应以目标账号的实际连接结果为准；本地自动化测试不验证这些平台权限。

## 核心服务复验

在独立源码工作副本和虚拟环境中运行：

```bash
python -m pip install -c constraints.txt -e .
python -m unittest discover -s tests -v
```

这些测试使用临时项目，不需要个人项目或真实 OpenAI key。不要把已签名 `.app` 的内部资源目录作为普通可写测试工作区；安装包的运行及签名完整性应另外验收。核心测试通过后，再用可丢弃的项目工作副本验证目标电脑和 ChatGPT 的实际链路。
