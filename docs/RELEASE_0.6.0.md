# Codex Local MCP 0.6.0 实施与交接记录

更新日期：2026-11-23（Asia/Shanghai）

## 本次接续范围

唯一维护仓库为 `/Users/sugar/temporaryFolder/codex-local-mcp`，远端为 `makersns/codex-local-mcp`，当前实施分支 `feat/native-desktop-0.6.0`。接续基线为 `ea6c57a292f8b9461b5defe1c20afa552783b209`；上轮尚未提交的改动完整保留。产品显示名称为 **Codex Local MCP**，作者 **Sugar**。

已确认用户要求：完成原生单窗口界面、便捷配置、实施和测试、安装打包、同步 GitHub；无远端旧发布包在保存独有文档且核验无引用后清理。后端内部兼容标识不因品牌调整而变更。

## 接续时实际状态

- 原生 `macos/` 已有模型、通信、设计变量，尚缺 App 入口和页面，不能视为完整 UI。
- `desktop_api.py` 已有协议实现，尚需专项验证及修复。
- 构建与安装脚本已改造，但尚无本轮完整原生 App 验收。
- 本次会话仍由已安装 **0.4.0** 后台提供连接；不得把源码或安装包版本视为当前服务版本。
- 发现其他工程仍通过同一 MCP 执行作业，真实后台切换安排在最后核查之后。
- 本机剩余空间约 1.52 GiB，编译缓存及 QA 安装副本按需使用，只清理本任务自己创建的可重建内容。

## 源码与界面验收结果

| 范围 | 当前结果 | 证据 |
| --- | --- | --- |
| 仓库身份 | main 基线与唯一 origin 核对完成 | git status / remote / worktree |
| 旧包独有文档 | 3 份已归档，旧包文件摘要已保存 | docs/archive/0.5.0-release/；本机私有 inventory |
| 桌面回归 | 117 项全部通过（含 API 32 项、安装 14 项） | .data/native-desktop-0.6.0/backend-test-results.txt |
| 原生 Swift 自测 | 21 / 21 通过，编译 0 警告 | ui/self-test-results-final.json |
| 实际原生交互 | 17 / 17 通过，真实按钮和 Unicode 键盘事件，连接为隔离预览 | ui/native-ax-results-2.json |
| 首次配置真实交互 | 12 / 12 通过：中文空格目录选择、默认权限与保存读回 | ui/native-first-setup-results-3.json |
| 首轮完整 App | universal、严格签名、86 份源码资源一致；arm64 / Rosetta 各 21 项自测、隔离安装及重复安装通过 | release-build 私有回执；最终包补齐历史文档并采用专属图标 |
| 原生页面渲染 | 1240 / 920 宽，7 张页面检查 | ui/render-results-final.json；docs/images/ |
| 隔离 bootstrap | 首次 5.490 秒，复用 0.816 秒；使用已有缓存 | release-build/bootstrap-verification.json |
| 现有配置兼容 | 两个文件夹与权限保留，密钥仅查元数据，配置文件 SHA-256 未变 | 只读 snapshot 与 upgrade-readonly-review.json |
| 核心回归 | 124 项运行：123 通过，1 跳过，0 失败 | .data/native-desktop-0.6.0/release-build/server-unittest-20261123-0815.log |
| 可选 Office 跳过 | 测试 MCP Python 未安装可选文档库；不冒充完整 Office 验收 | 上述测试日志 |
| 当前系统权限 | 既有 MCP 进程 accessibility / screen_capture / post_event 均为 true | 只读权限检查；未申请新权限 |
| 连接指引 | 官方入口、Runtime API key 和隧道权限说明复核完成 | 见下方官方资料 |

## 实施分工

- 原生界面：`macos/` 的 App 入口、五页、帮助、可访问性标识、首次配置、预览、自测和窗口截图。
- 后端：配置完整文档保留、revision 冲突、事务回滚、密钥元数据、生命周期所有权、隔离测试、日志脱敏及 Python 回归。
- 构建：安装完整性与防降级、重复安装修复、双架构、运行环境、服务器品牌和测试记录。
- 集成：真实 UI/后端核对、用户文档、发布包、旧目录清理、Git 提交与远端验证。

## 尚待验收，未宣称完成

- [x] 原生窗口和页面完整实现并编译。
- [x] 配置 API 专项及完整桌面回归。
- [x] 首次使用与已有配置两条模型路径；首次选择器的真实操作单独验收。
- [x] 原生实际按钮、取消编辑、未保存保护与错误恢复。
- [x] 首次配置的原生文件夹选择与保存完整流程。
- [x] 正常及最小窗口尺寸截图检查。
- [ ] 双架构 App、签名、隔离安装及防降级。
- [ ] 安装到本机，核对已有文件夹和权限保留。
- [ ] 旧发布包核查后清理。
- [ ] 源码提交、同步远端、发布物核对。
- [ ] 在不影响其他工程作业的前提下切换后台并核对实际回连版本。

## 新增客户端图标要求

用户进一步要求专属客户端应用图标。图标采用自绘原生路径，结合 C 形本机窗口和向外连接，沿用靛紫色并以薄荷色标识连接端点；支持 16–1024 像素原生图标尺寸，纳入最终 App 的 AppIcon.icns。最终预览与打包结果以完成后的发布记录为准。

## 会话监测结论

0.6.0 尚未加入 ChatGPT 会话计数。已核对官方匿名 `openai/session` 元数据及固定 MCP SDK 2.3.0 的读取方式，可支持“正在调用”“最近 10 分钟活跃”等指标；无法从空闲工具调用链路精确判断聊天窗口在线或断开。完整方案见 [会话活动监测设计](SESSION_MONITORING_DESIGN.md)。本轮仅记录设计，没有暗中增加数据采集。

## 正式构建记录方式

App 将绑定完成上述验收的干净源码提交，构建信息记录 `source_revision` 与 `source_modified: false`。正式构建和安装完成后，仅更新发布证据文档；后续文档提交与 App 的代码提交分别标明，避免把未经核验的工作区产物称为正式版。

## 官方资料复核

复核日期：2026-11-23。运行使用具备目标隧道 Tunnels Read + Use 权限的 Runtime API key；创建/编辑隧道另需 Read + Manage，并应关联目标 ChatGPT 工作区。本机 readiness 不能替代 ChatGPT 实际工具调用。

- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [连接与测试插件](https://developers.openai.com/plugins/deploy/connect-chatgpt)

## 记录约定

后续在本文件追加实际验收结果与最终路径。只把真实执行结果标为通过；预览样例、隔离测试和生产连接分别记录。日志、个人配置、真实密钥、工作路径清单不进入公开源码。最终 App 的构建信息需绑定已提交源码；不将工作区未提交状态描述为发布完成。
