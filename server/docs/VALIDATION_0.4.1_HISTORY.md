# Sugar Local MCP 0.4.1 验证记录

日期：2026-11-10（Asia/Shanghai）。

## 0.4.1 云端

共发现 200 项核心测试：182 项通过；17 项真实 Unix socket 用例因执行器拒绝 AF_UNIX 整类跳过，1 项因测试虚拟环境缺少可选 Office 库跳过，无失败或错误。用时 10.500 秒。原始 unittest 摘要 Ran 183 tests / OK (skipped=2) 中，整类跳过仅算一个条目。完整输出见 test-results.txt。

其中包含 53 项浏览器回归与 20 项原生窗口模拟后端测试；新的选区检查、禁止追加、暂停与恢复边界已覆盖。配套 Desktop 75 项测试通过。官方 MCP stdio 客户端实际发现版本 0.4.1 和 45 个工具（写入和执行开启）。

新填写实现的真实浏览器验证尚未完成：Mac 隧道随后离线；云端 Playwright 的 Chromium 可执行文件缺失，启动尝试失败后跳过，未下载新依赖。

## 0.4.0 Mac 现场结果

此前安装的 0.4.0 在 macOS 15.5 / arm64 / Python 3.12.16 上真实连通。App 严格深度验签通过，applet 含两种 CPU 架构。核心实际执行 194 项：192 通过、1 失败、1 跳过；失败来自测试比较 /var 与 /private/var 的路径别名，已在 0.4.1 修正测试但尚未在 Mac 复跑。17 项真实 IPC 用例通过，Desktop 68 项及 9 份原生对话模板编译通过。

独立 MCP、临时文件编辑、前后端启动、本机 Chrome 和页面快照通过；填写出现追加旧内容，verified=false，验收正确记为失败。测试 Chrome、任务、私有 socket、临时项目均确认清理。后续原生窗口和 Office 阶段未运行完成，不计通过。

随后远程请求超时并返回隧道客户端未连接/超过 300 秒未见客户端，原因需本机日志定位。本轮没有将 0.4.1 安装替换到用户 Mac。完整记录与待补清单见 Desktop 的 VALIDATION.md。

## 复验

在独立源码工作副本中准备依赖后：

```bash
python -m pip install -c constraints.txt -e .
python -B -m unittest discover -s tests -v
```

测试使用临时项目，不需要真实 Runtime key。避免在已签名 App 的 Resources 中写入字节码或构建产物。配套 tests/native_acceptance_040.py 需要完整 Desktop/server 目录、macOS 与准备好的 MCP Python；它读取动态版本号。

MCP SDK 2.3.0 的 POSIX stdio 适配仍集中使用 _lowlevel_server；升级 SDK 要复验 EOF、SIGTERM 与输出背压。历史记录单独保留，不改写历史测试为新版本通过。
