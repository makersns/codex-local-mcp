# Codex Local MCP 0.6.0 核心验证记录

验证日期：2026-11-23（Asia/Shanghai）。作者：Sugar。

## 本轮结论

当前 0.6.0 核心源码完整回归运行 **124 项：123 通过、1 跳过、0 失败，12.812 秒**。测试包含真实 stdio 初始化和中文文件读取、MCP 工具发现、文件写入冲突、受管进程、HTTP Host/Origin 防护、诊断 IPC、权限与 Office 检测。

本轮使用 macOS 15.5 / arm64 的既有 Python 3.12 依赖环境，通过 `PYTHONPATH=server/src` 加载本仓库 0.6.0 源码。没有替换该解释器中原有服务包，没有把源码回归称为全新机器安装验证。测试使用独立临时项目和本地进程，不使用用户文档或真实隧道凭据。

执行上述隔离回归时，生产 MCP 连接仍在旧 0.4.0 运行；该回归没有停止或升级它。应用安装、原生窗口及正式发布验证另见仓库根目录的 [VALIDATION.md](../../VALIDATION.md) 和 [0.6.0 实施记录](../../docs/RELEASE_0.6.0.md)。

## 可重现命令

在具备 `server/constraints.txt` 所列依赖的 Python 环境中，从仓库根目录执行：

```bash
PYTHONPATH=server/src python -B -m unittest discover -s server/tests -v
```

实际本机原始日志留在维护仓库的私有测试目录 `.data/native-desktop-0.6.0/release-build/server-unittest-20261123-0815.log`，不作为应用数据或公开源码提交。

## 验证范围

| 范围 | 本轮结果 |
| --- | --- |
| 版本与显示名称 | 源码 0.6.0；MCP 服务名为 Codex Local MCP |
| 兼容入口 | 模块及旧命令 `sugar_local_mcp` / `sugar-local-mcp` 保留；新增 `codex-local-mcp` |
| 工具发现 | 写入及执行开启时 15 个工具；纯只读时 4 个 |
| 真实 stdio | 初始化、工具发现和中文文件读取通过 |
| 文件可靠性 | 路径越界、符号链接、大小限制、旧哈希拒绝、修改备份通过 |
| 受管进程 | 启停、stdin/EOF、超时、分页输出、退出清理及凭据环境隔离通过 |
| HTTP | 非法 Host 与 Origin 拒绝；合法本机请求通过 |
| 诊断 IPC | 请求大小、并发上限、所有权、超时与关闭清理通过 |
| macOS 权限 | 仅检查不弹窗、明确请求、未知状态和异常脱敏通过 |
| Office 检测 | 环境分离、格式分别选择解释器、缓存、取消与超时通过 |

套件中的 `test_real_synthetic_edit_roundtrip_when_libraries_are_present` 按条件跳过，原因是运行测试的 MCP Python 没有可选 Office 文档库。此记录不将该项计为通过；也不继承历史版本的真实 Office、Chrome 联调或安装结果作为本轮证据。

## 平台与发布边界

以上为当前 Mac 的源码与协议回归，不代表 Intel 真机、Windows、Docker、第二台 Mac 首次联网安装或生产 ChatGPT 隧道已验证。原生 App 构建的双架构结构、签名和安装应以对应的本轮桌面验证记录为准。

## 历史记录

- [0.5.0](VALIDATION_0.5.0_HISTORY.md)
- [0.4.1](VALIDATION_0.4.1_HISTORY.md)
- [0.4.0](VALIDATION_0.4.0_HISTORY.md)
- [0.3.0](VALIDATION_0.3.0_HISTORY.md)

历史结果不计入 0.6.0 的通过数量。
