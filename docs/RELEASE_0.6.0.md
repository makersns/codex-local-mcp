# Codex Local MCP 0.6.0 实施与交接记录

更新日期：2026-11-23（Asia/Shanghai）

## 本次接续范围

唯一维护仓库为 `/Users/sugar/temporaryFolder/codex-local-mcp`，远端为 `makersns/codex-local-mcp`，当前实施分支 `feat/native-desktop-0.6.0`。接续基线为 `ea6c57a292f8b9461b5defe1c20afa552783b209`；上轮尚未提交的改动完整保留。产品显示名称为 **Codex Local MCP**，作者 **Sugar**。

已确认用户要求：完成原生单窗口界面、便捷配置、实施和测试、安装打包、同步 GitHub；无远端旧发布包在保存独有文档且核验无引用后清理。后端内部兼容标识不因品牌调整而变更。

## 追加要求与当前发布状态

已完成两轮候选构建。包含原创图标的 a6f11a5 候选版已安装到 `~/Applications/Codex Local MCP.app`，0.6.0 运行环境已准备，两个原有文件夹、权限和钥匙串状态保留；原生产 0.4.0 后台尚未重启。候选版不是最终发布版。

用户随后追加以下范围，这些追加功能现已完成源码与原生交互验收，最终分发包、安装与回连按下方记录收尾：

- [x] 概览会话活动：正在调用、近 10 分钟活跃、运行中任务，未知来源与统计边界明确。
- [x] 客户端英文、简体中文、繁體中文、日本語；默认跟随系统、可手动切换、保留草稿和连接。
- [x] 侧栏、关于和帮助中的作者 Sugar 链接至 https://sugar.makersns.com。
- [x] GitHub 英文首页与三份语言文档、8 张真实最终截图、下载入口及清晰介绍。
- [ ] 完成后合并 main、推送远端、发布最终客户端，并清理已合并过程分支和可重建临时文件。

根 README 已写英文版，简体中文、繁體中文及日文文档已新增。会话后端与原生概览、554 条四语文案、语言切换和作者入口均已完成；原生自测 77 / 77 与新增真实窗口检查 22 项通过。8 张公开截图已统一为原创图标版，并核对各语言文档的图片路径。

旧的无远端发布目录已通过再次完整核验并移入 `~/.Trash/Sugar_Local_MCP_Desktop_0.5.0`。150 个原文件及新增 Finder 元数据均保持一致；3 份独有文档和 2 份原始测试日志已经保留。旧安装 App 与生产运行环境未清理。

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
| 桌面回归 | 124 项全部通过（含 API 32 项、安装 14 项及活动状态分支） | .data/native-desktop-0.6.0/activity-desktop-regression.txt |
| 原生 Swift 自测 | 77 / 77 通过，含 35 项四语专项 | ui/i18n-self-test-results.json；最终 App 将分别运行两种架构 |
| 新增原生交互 | 22 项通过：会话数字与匿名明细、语言切换草稿保留、作者主页入口 | ui/native-i18n-acceptance-final.json；原始 AX 回执分别保留 |
| 四语文案 | 554 条，UI 必要文案及 173 条后端提示完整，格式占位符一致 | release-build/localization-source-validation.json；localization-backend-coverage.json |
| 实际原生交互 | 17 / 17 通过，真实按钮和 Unicode 键盘事件，连接为隔离预览 | ui/native-ax-results-2.json |
| 首次配置真实交互 | 12 / 12 通过：中文空格目录选择、默认权限与保存读回 | ui/native-first-setup-results-3.json |
| 首轮完整 App | universal、严格签名、86 份源码资源一致；arm64 / Rosetta 各 21 项自测、隔离安装及重复安装通过 | release-build 私有回执；最终包补齐历史文档并采用专属图标 |
| 原生页面渲染 | 初版 7 场景；四语追加 12 个渲染场景，8 张最终公开截图采用原创图标 | ui/i18n-render-results.json；ui/i18n-branded-render-results.json；docs/images/ |
| 隔离 bootstrap | 首次 5.490 秒，复用 0.816 秒；使用已有缓存 | release-build/bootstrap-verification.json |
| 现有配置兼容 | 两个文件夹与权限保留，密钥仅查元数据，配置文件 SHA-256 未变 | 只读 snapshot 与 upgrade-readonly-review.json |
| 核心回归 | 140 项运行：139 通过，1 跳过，0 失败 | .data/native-desktop-0.6.0/activity-core-regression.txt |
| 可选 Office 跳过 | 测试 MCP Python 未安装可选文档库；不冒充完整 Office 验收 | 上述测试日志 |
| 当前系统权限 | 既有 MCP 进程 accessibility / screen_capture / post_event 均为 true | 只读权限检查；未申请新权限 |
| 连接指引 | 官方入口、Runtime API key 和隧道权限说明复核完成 | 见下方官方资料 |

## 实施分工

- 原生界面：`macos/` 的 App 入口、五页、帮助、可访问性标识、首次配置、预览、自测和窗口截图。
- 后端：配置完整文档保留、revision 冲突、事务回滚、密钥元数据、生命周期所有权、隔离测试、日志脱敏及 Python 回归。
- 构建：安装完整性与防降级、重复安装修复、双架构、运行环境、服务器品牌和测试记录。
- 集成：真实 UI/后端核对、用户文档、发布包、旧目录清理、Git 提交与远端验证。

## 验收清单

- [x] 原生窗口和页面完整实现并编译。
- [x] 配置 API 专项及完整桌面回归。
- [x] 首次使用与已有配置两条模型路径；首次选择器的真实操作单独验收。
- [x] 原生实际按钮、取消编辑、未保存保护与错误恢复。
- [x] 首次配置的原生文件夹选择与保存完整流程。
- [x] 正常及最小窗口尺寸截图检查；四语长文案与草稿保护追加验收。
- [ ] 双架构 App、签名、隔离安装及防降级。
- [x] 图标候选版安装到本机，核对已有文件夹和权限保留；追加功能后的最终版还需更新安装。
- [x] 旧发布包核查后清理。
- [ ] 源码提交、同步远端、发布物核对。
- [ ] 在不影响其他工程作业的前提下切换后台并核对实际回连版本。

## 新增客户端图标要求

用户进一步要求专属客户端应用图标。图标采用自绘原生路径，结合 C 形本机窗口和向外连接，沿用靛紫色并以薄荷色标识连接端点；支持 16–1024 像素原生图标尺寸，纳入最终 App 的 AppIcon.icns。图标已纳入 a6f11a5 候选版并安装，AppIcon.icns 与 Info.plist 引用核对通过；新增功能后的最终包继续使用同一原创图标。

## 会话监测结论

初始候选版尚未加入会话计数。用户再次提出需求后，本轮已将活动监测纳入实施：按官方匿名 `openai/session` 元数据与 MCP SDK 2.3.0 聚合当前及近期活动，独立报告本机任务。无法从无请求的空闲期精确判断聊天窗口在线或断开；未知来源不能误报为零。详细口径见 [会话活动监测设计](SESSION_MONITORING_DESIGN.md) 和 [API 契约](DESKTOP_API_V1.md)。

## 正式构建记录方式

App 将绑定完成上述验收的干净源码提交，构建信息记录 `source_revision` 与 `source_modified: false`。正式构建和安装完成后，仅更新发布证据文档；后续文档提交与 App 的代码提交分别标明，避免把未经核验的工作区产物称为正式版。

## 官方资料复核

复核日期：2026-11-23。运行使用具备目标隧道 Tunnels Read + Use 权限的 Runtime API key；创建/编辑隧道另需 Read + Manage，并应关联目标 ChatGPT 工作区。本机 readiness 不能替代 ChatGPT 实际工具调用。

- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [连接与测试插件](https://developers.openai.com/plugins/deploy/connect-chatgpt)

## 记录约定

后续在本文件追加实际验收结果与最终路径。只把真实执行结果标为通过；预览样例、隔离测试和生产连接分别记录。日志、个人配置、真实密钥、工作路径清单不进入公开源码。最终 App 的构建信息需绑定已提交源码；不将工作区未提交状态描述为发布完成。
