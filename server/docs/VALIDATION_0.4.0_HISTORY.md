> 历史记录：这是 0.4.0 初始交付时的记录；后续 Mac 现场结果见当前 VALIDATION.md。

# Sugar Local MCP 0.4.0 验证记录

验证日期：2026-11-10。

## 本轮结果

Linux / Python 3.12.14 / 官方 MCP SDK 2.3.0 下共发现 194 项核心测试：176 项通过，17 项真实 Unix socket 用例因执行器禁止 AF_UNIX 整类跳过，1 项可选 Office 依赖测试跳过。没有失败或错误。unittest 的原始摘要为 `Ran 177 tests`、`OK (skipped=2)`，其中整类跳过只显示为一个条目；不能据此把全部 194 项记为已运行。完整日志见 `test-results.txt`。

配套桌面应用 68 项测试全部通过。官方 MCP stdio 客户端实际发现版本 0.4.0 和 45 个工具（开启写入/执行的配置），未暴露自动操作恢复工具。通过新 Office 工具在云端另一个已具备文档库的 Python 中实际完成 XLSX、DOCX、PPTX 的内存创建、保存、读取、修改与中文往返；不涉及用户文档，也不代表实际 Mac 环境。

浏览器 47 项回归与原生窗口 20 项回归包含在上述总数中。它们使用模拟 CDP/系统后端；真实 Chrome 和 macOS API 验收尚未完成。私有控制线程与探针进程清理已通过实际线程/子进程测试，仍需在 Mac 补齐真实 IPC 测试。

## macOS 状态

本轮向 Mac 临时目录上传候选源码后，测试启动和查询请求超时。用户重启 App 后，隧道仍报告未连接或超过 300 秒未见客户端，未取得 0.4.0 的真机结果，也未执行线上安装替换。完整安装包记录与待补清单见 Desktop 的 `VALIDATION.md`。

## 复验

在独立源码工作副本中安装约束依赖后运行：

```bash
python -m pip install -c constraints.txt -e .
python -B -m unittest discover -s tests -v
```

测试使用临时项目，不需要真实 OpenAI key。不要在已签名 `.app` 的 Resources 中运行会写入字节码或构建产物的开发命令。实际 Office、浏览器和窗口验收由配套 `tests/native_acceptance_040.py` 执行；它需要 macOS、准备好的 MCP Python 和完整的 Desktop/server 目录布局。

MCP SDK 2.3.0 的 POSIX stdio 适配仍集中使用其 `_lowlevel_server` 接口。升级 SDK 时应重新验证 stdio、EOF、SIGTERM 和输出背压。

0.3.0 及以前的记录已保留为 `VALIDATION_0.3.0_HISTORY.md`，不属于本版验收结果。
