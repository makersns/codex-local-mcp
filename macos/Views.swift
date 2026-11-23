import AppKit
import SwiftUI

@MainActor
private enum BrandArtwork {
    private static let bundled = Bundle.main.url(forResource: "AppIcon", withExtension: "icns")
        .flatMap { NSImage(contentsOf: $0) }
    private static let sourcePreview: NSImage? = {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        return NSImage(contentsOf: root.appendingPathComponent("docs/images/app-icon-0.6.0.png"))
    }()
    static func icon(preview: Bool) -> NSImage? {
        if let bundled { return bundled }
        guard preview, Bundle.main.bundleURL.pathExtension != "app" else { return nil }
        return sourcePreview
    }
}

struct RootView: View {
    @ObservedObject private var localization = Localization.shared
    @ObservedObject var model: AppModel
    var body: some View {
        GeometryReader { geometry in
            HStack(spacing: 0) {
                sidebar.frame(width: geometry.size.width < 1050 ? 196 : 216)
                Rectangle().fill(Palette.line).frame(width: 1)
                VStack(spacing: 0) {
                    topbar
                    Rectangle().fill(Palette.line).frame(height: 1)
                    if model.preparing {
                        VStack(spacing: 18) {
                            ProgressView().controlSize(.large)
                            Text(L("正在准备 Codex Local MCP")).font(.system(size: 20, weight: .semibold))
                            Text(L(model.preparationMessage)).font(.system(size: 13)).foregroundColor(Palette.secondary)
                                .multilineTextAlignment(.center).frame(maxWidth: 420)
                        }.frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else {
                        ScrollView {
                            VStack(alignment: .leading, spacing: 22) {
                                if let notice = model.notice { noticeView(notice) }
                                if !model.prepared {
                                    EmptyState(symbol: "wifi.exclamationmark", title: "准备还未完成",
                                               detail: "请检查网络后重新准备。已保存的连接和文件夹会保留。")
                                    HStack {
                                        Button(L("重新准备")) { Task { await model.prepare() } }
                                            .buttonStyle(PrimaryActionStyle()).accessibilityIdentifier("retryPrepare")
                                        Button(L("打开启动记录")) { model.openLogs() }.buttonStyle(SecondaryActionStyle())
                                    }.frame(maxWidth: .infinity)
                                } else {
                                    switch model.page {
                                    case .overview: OverviewPage(model: model)
                                    case .folders: FoldersPage(model: model)
                                    case .connection: ConnectionPage(model: model)
                                    case .diagnostics: DiagnosticsPage(model: model)
                                    case .logs: LogsPage(model: model)
                                    }
                                }
                            }.frame(maxWidth: 1100, alignment: .leading)
                                .padding(geometry.size.width < 1050 ? 24 : 32)
                                .frame(maxWidth: .infinity)
                        }
                    }
                    if let busy = model.busyLabel {
                        HStack(spacing: 10) {
                            ProgressView().controlSize(.small)
                            Text(L(busy)).font(.system(size: 12)).foregroundColor(Palette.secondary)
                            Spacer()
                        }.padding(.horizontal, 28).frame(height: 38).background(Palette.sidebar)
                    }
                }.background(Color.white)
            }
        }.foregroundColor(Palette.ink).tint(Palette.accent)
            .preferredColorScheme(.light)
            .environment(\.locale, localization.resolvedLocale)
            .sheet(isPresented: $model.showHelp) { HelpView(model: model) }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                Group {
                    if let icon = BrandArtwork.icon(preview: model.options.preview) {
                        Image(nsImage: icon).resizable().interpolation(.high).aspectRatio(contentMode: .fit)
                    } else {
                        ZStack {
                            RoundedRectangle(cornerRadius: 11).fill(Palette.accent)
                            Text("C").font(.system(size: 25, weight: .bold)).foregroundColor(.white)
                        }
                    }
                }.frame(width: 40, height: 40).accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Codex Local").font(.system(size: 16, weight: .bold))
                    Text("MCP").font(.system(size: 12, weight: .semibold)).foregroundColor(Palette.secondary)
                }
            }.padding(.horizontal, 21).padding(.top, 27).padding(.bottom, 32)
            Text(L("工作空间")).font(.system(size: 10, weight: .semibold)).tracking(1)
                .foregroundColor(Palette.secondary).padding(.horizontal, 24).padding(.bottom, 12)
            ForEach(AppPage.allCases) { page in
                Button {
                    Task { await model.navigate(page) }
                } label: {
                    HStack(spacing: 11) {
                        Image(systemName: page.symbol).font(.system(size: 15)).frame(width: 20)
                        Text(L(page.title)).font(.system(size: 13, weight: model.page == page ? .semibold : .medium))
                        Spacer(minLength: 0)
                    }.padding(.horizontal, 12).frame(height: 43)
                        .foregroundColor(model.page == page ? Palette.accent : Palette.secondary)
                        .background(model.page == page ? Palette.accent.opacity(0.09) : Color.clear)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                        .contentShape(Rectangle())
                }.buttonStyle(.plain).padding(.horizontal, 12).padding(.bottom, 4)
                    .disabled(model.isBusy).accessibilityIdentifier("nav-\(page.rawValue)")
            }
            Spacer(minLength: 28)
            languageMenu
            Button { model.openHelp() } label: {
                Label(L("使用帮助"), systemImage: "questionmark.circle").font(.system(size: 13))
            }.buttonStyle(.plain).foregroundColor(Palette.secondary)
                .padding(.horizontal, 25).padding(.vertical, 18).accessibilityIdentifier("openHelp")
            Rectangle().fill(Palette.line).frame(height: 1).padding(.horizontal, 21)
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 7) {
                    Circle().fill(model.snapshot.isReady ? Palette.success : Palette.secondary.opacity(0.5))
                        .frame(width: 6, height: 6)
                    Text(L(model.preparing ? "正在准备" : model.snapshot.isReady ? "本机已就绪" : "尚未就绪"))
                        .font(.system(size: 11, weight: .medium)).foregroundColor(Palette.secondary)
                }
                HStack(spacing: 5) {
                    Text("v\(model.snapshot.version)  ·").font(.system(size: 10))
                    Button("Sugar") { model.openURL("author") }.buttonStyle(.plain)
                        .font(.system(size: 10)).accessibilityIdentifier("sidebarAuthorHomepage")
                }.foregroundColor(Palette.secondary)
            }.padding(.horizontal, 24).padding(.vertical, 22)
        }.frame(maxHeight: .infinity).background(Palette.sidebar)
    }

    private var languageMenu: some View {
        Menu {
            ForEach(AppLanguage.allCases) { language in
                Button {
                    localization.setLanguage(language, persist: !model.options.preview)
                } label: {
                    if localization.language == language {
                        Label(language == .system ? L("跟随系统") : language.nativeName, systemImage: "checkmark")
                    } else {
                        Text(language == .system ? L("跟随系统") : language.nativeName)
                    }
                }.accessibilityIdentifier("language-\(language.rawValue)")
            }
        } label: {
            Label(localization.language == .system ? L("跟随系统") : localization.language.nativeName, systemImage: "globe")
                .font(.system(size: 12)).frame(maxWidth: .infinity, alignment: .leading)
        }.menuStyle(.borderlessButton).fixedSize(horizontal: false, vertical: true)
            .foregroundColor(Palette.secondary).padding(.horizontal, 24).padding(.vertical, 9)
            .accessibilityLabel(L("界面语言")).accessibilityIdentifier("languageMenu")
    }

    private var topbar: some View {
        HStack {
            Text(L(model.page.title)).font(.system(size: 12, weight: .medium)).foregroundColor(Palette.secondary)
            Spacer()
            if model.options.preview {
                Text(L("界面预览 · 示例数据")).font(.system(size: 11)).foregroundColor(Palette.accent)
                    .padding(.horizontal, 9).padding(.vertical, 5).background(Palette.tint).clipShape(Capsule())
            }
            Button { Task { await model.refresh() } } label: {
                Label(L("刷新状态"), systemImage: "arrow.clockwise").font(.system(size: 12))
            }.buttonStyle(.plain).foregroundColor(Palette.secondary)
                .padding(.leading, 12).disabled(model.isBusy || !model.prepared)
                .accessibilityIdentifier("refreshSnapshot")
        }.padding(.horizontal, 28).frame(height: 56)
    }

    private func noticeView(_ notice: AppNotice) -> some View {
        let detail = notice.kind == .error
            ? L(notice.detail, fallback: notice.fallbackDetail) : L(notice.detail)
        let hasOriginalDiagnostic = notice.kind == .error && detail != L(notice.detail)
        let color = notice.kind == .error ? Color(red: 0.69, green: 0.35, blue: 0.16) :
            notice.kind == .success ? Palette.success : Palette.accent
        return HStack(alignment: .top, spacing: 11) {
            Image(systemName: notice.kind == .error ? "exclamationmark.circle" :
                    notice.kind == .success ? "checkmark.circle" : "info.circle").foregroundColor(color)
            VStack(alignment: .leading, spacing: 6) {
                Text(L(notice.title)).font(.system(size: 13, weight: .semibold))
                Text(detail).font(.system(size: 12)).foregroundColor(Palette.secondary)
                    .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                if hasOriginalDiagnostic {
                    DisclosureGroup(L("诊断信息")) {
                        Text(notice.detail).font(.system(size: 11)).foregroundColor(Palette.secondary)
                            .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                            .padding(.top, 6)
                    }.font(.system(size: 11)).accessibilityIdentifier("originalDiagnostic")
                }
                if notice.kind == .error && model.prepared {
                    HStack(spacing: 18) {
                        Button(L("重新载入设置")) { Task { await model.reloadSettings() } }
                            .buttonStyle(QuietActionStyle()).accessibilityIdentifier("reloadSettings")
                        Button(L("查看运行记录")) { Task { await model.navigate(.logs) } }.buttonStyle(QuietActionStyle())
                    }.disabled(model.isBusy)
                }
            }
            Spacer(minLength: 0)
            Button { model.notice = nil } label: { Image(systemName: "xmark").font(.system(size: 10)) }
                .buttonStyle(.plain).foregroundColor(Palette.secondary).help(L("关闭提示"))
                .accessibilityLabel(L("关闭提示"))
        }.padding(15).frame(maxWidth: .infinity, alignment: .leading)
            .background(color.opacity(0.055)).clipShape(RoundedRectangle(cornerRadius: 9))
            .overlay(RoundedRectangle(cornerRadius: 9).stroke(color.opacity(0.2), lineWidth: 1))
    }
}

struct OverviewPage: View {
    @ObservedObject private var localization = Localization.shared
    @ObservedObject var model: AppModel
    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            PageTitle(title: "本机工作，从这里连接", subtitle: "连接你的电脑，让 AI 在你授权的文件夹中协助工作。")
            VStack(alignment: .leading, spacing: 22) {
                HStack(alignment: .top, spacing: 18) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 15).fill(Palette.tint).frame(width: 64, height: 64)
                        Image(systemName: "laptopcomputer").font(.system(size: 31, weight: .light)).foregroundColor(Palette.accent)
                    }
                    VStack(alignment: .leading, spacing: 8) {
                        HStack(spacing: 8) {
                            Circle().fill(model.snapshot.isReady ? Palette.success : Palette.secondary).frame(width: 8, height: 8)
                            Text(L(model.snapshot.title)).font(.system(size: 22, weight: .semibold))
                        }
                        Text(model.snapshot.computerName.isEmpty ? L("先完成一次连接设置，就可以开始使用。") : model.snapshot.computerName)
                            .font(.system(size: 14)).foregroundColor(Palette.secondary).textSelection(.enabled)
                        if !model.snapshot.message.isEmpty {
                            Text(L(model.snapshot.message)).font(.system(size: 12)).foregroundColor(Palette.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    Spacer(minLength: 0)
                }
                HStack(spacing: 10) {
                    if !model.snapshot.configured {
                        Button(L("开始设置")) { Task { await model.navigate(.connection) } }.buttonStyle(PrimaryActionStyle())
                            .accessibilityIdentifier("beginSetup")
                    } else if model.snapshot.isReady {
                        Button { Task { await model.navigate(.folders) } } label: { Label(L("管理文件夹"), systemImage: "folder") }
                            .buttonStyle(PrimaryActionStyle()).accessibilityIdentifier("manageFolders")
                        Button(L("停止连接")) { Task { await model.lifecycle("stop") } }.buttonStyle(SecondaryActionStyle())
                            .accessibilityIdentifier("stopConnection")
                        Button(L("重新连接")) { Task { await model.lifecycle("restart") } }.buttonStyle(QuietActionStyle())
                            .accessibilityIdentifier("restartConnection")
                    } else {
                        Button(L("启动连接")) { Task { await model.lifecycle("start") } }.buttonStyle(PrimaryActionStyle())
                            .accessibilityIdentifier("startConnection")
                        Button(L("检查连接设置")) { Task { await model.navigate(.connection) } }.buttonStyle(SecondaryActionStyle())
                    }
                }.disabled(model.isBusy)
                Text(L("“本机已就绪”表示本地连接已启动。请在 ChatGPT 中调用一次工具，确认完整连接。"))
                    .font(.system(size: 11)).foregroundColor(Palette.secondary).fixedSize(horizontal: false, vertical: true)
            }.panel(padding: 26)

            if model.snapshot.upgradePending {
                InformationLine(text: "新版已安装。下一次重新连接时会应用更新；当前连接可以继续使用。")
            }
            ActivityPanel(activity: model.snapshot.activity, workspaces: model.snapshot.workspaces)
                .id(model.snapshot.activity.instanceID ?? "unavailable")
            HStack(alignment: .top, spacing: 18) {
                VStack(alignment: .leading, spacing: 18) {
                    HStack {
                        Text(L("已授权文件夹")).font(.system(size: 15, weight: .semibold))
                        Spacer()
                        Text("\(model.snapshot.workspaces.count)").font(.system(size: 12, weight: .semibold))
                            .foregroundColor(Palette.accent).padding(.horizontal, 8).padding(.vertical, 4)
                            .background(Palette.tint).clipShape(Capsule())
                    }
                    if model.snapshot.workspaces.isEmpty {
                        Text(L("还没有添加文件夹。选择一个文件夹后，即可让 AI 协助阅读和处理其中的内容。"))
                            .font(.system(size: 12)).foregroundColor(Palette.secondary).fixedSize(horizontal: false, vertical: true)
                    } else {
                        ForEach(Array(model.snapshot.workspaces.prefix(3).enumerated()), id: \.offset) { _, row in
                            HStack(alignment: .top, spacing: 10) {
                                Image(systemName: "folder").font(.system(size: 17)).foregroundColor(Palette.accent).padding(.top, 2)
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(row.displayName).font(.system(size: 13, weight: .medium)).lineLimit(1)
                                    Text(L(row.accessDescription)).font(.system(size: 11)).foregroundColor(Palette.secondary)
                                }
                                Spacer(minLength: 0)
                            }
                        }
                    }
                    Button(L(model.snapshot.workspaces.isEmpty ? "添加文件夹" : "查看全部文件夹")) {
                        Task { await model.navigate(.folders) }
                    }.buttonStyle(QuietActionStyle())
                }.frame(maxWidth: .infinity, alignment: .leading).panel()
                VStack(alignment: .leading, spacing: 18) {
                    Text(L("准备好之后")).font(.system(size: 15, weight: .semibold))
                    guideRow("1", title: "打开 ChatGPT", detail: "选择已连接的 Codex Local MCP 插件。")
                    guideRow("2", title: "告诉 AI 你的目标", detail: "例如：“查看项目目录，帮我了解当前进度。”")
                    guideRow("3", title: "按需调整权限", detail: "在项目文件夹中控制编辑和运行任务的能力。")
                    Button(L("查看完整使用指南")) { model.openHelp() }.buttonStyle(QuietActionStyle())
                }.frame(maxWidth: .infinity, alignment: .leading).panel()
            }
            HStack(spacing: 10) {
                Image(systemName: "checkmark.shield").foregroundColor(Palette.secondary)
                Text(L("由你选择文件夹，由你决定权限。连接密钥保存在 macOS 钥匙串。"))
                    .font(.system(size: 12)).foregroundColor(Palette.secondary).fixedSize(horizontal: false, vertical: true)
            }.padding(.horizontal, 2)
        }
    }
    private func guideRow(_ number: String, title: String, detail: String) -> some View {
        HStack(alignment: .top, spacing: 11) {
            Text(number).font(.system(size: 10, weight: .semibold)).foregroundColor(Palette.accent)
                .frame(width: 22, height: 22).background(Palette.tint).clipShape(Circle())
            VStack(alignment: .leading, spacing: 5) {
                Text(L(title)).font(.system(size: 12, weight: .medium))
                Text(L(detail)).font(.system(size: 11)).foregroundColor(Palette.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

struct FoldersPage: View {
    @ObservedObject private var localization = Localization.shared
    @ObservedObject var model: AppModel
    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            HStack(alignment: .top, spacing: 18) {
                PageTitle(title: "项目文件夹", subtitle: "选择 AI 可以访问的内容，并为每个文件夹设置权限。")
                Spacer(minLength: 10)
                Button { Task { await model.pickFolder() } } label: { Label(L("添加文件夹"), systemImage: "plus") }
                    .buttonStyle(PrimaryActionStyle()).disabled(model.isBusy || model.snapshot.workspaces.count >= 32)
                    .accessibilityIdentifier("addFolder")
            }
            if model.snapshot.workspaces.isEmpty && !model.folderEditing {
                VStack(spacing: 12) {
                    EmptyState(symbol: "folder.badge.plus", title: "从一个文件夹开始", detail: "直接选择你平时工作的文件夹。文件会保留在原来的位置，不需要导入。")
                    Button(L("选择文件夹")) { Task { await model.pickFolder() } }
                        .buttonStyle(PrimaryActionStyle()).disabled(model.isBusy)
                }.padding(.bottom, 35).frame(maxWidth: .infinity).panel()
            } else {
                HStack(alignment: .top, spacing: 18) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(LF("文件夹  %d", model.snapshot.workspaces.count)).font(.system(size: 11, weight: .medium))
                            .foregroundColor(Palette.secondary).padding(.horizontal, 10).padding(.bottom, 8)
                        ForEach(Array(model.snapshot.workspaces.enumerated()), id: \.offset) { index, row in
                            Button { Task { await model.selectFolder(index) } } label: {
                                VStack(alignment: .leading, spacing: 8) {
                                    HStack(spacing: 8) {
                                        Image(systemName: "folder").foregroundColor(Palette.accent)
                                        Text(row.displayName).font(.system(size: 13, weight: .medium)).lineLimit(1)
                                        Spacer(minLength: 0)
                                    }
                                    Text(row.displayPath).font(.system(size: 10)).foregroundColor(Palette.secondary)
                                        .lineLimit(2).truncationMode(.middle).multilineTextAlignment(.leading)
                                    Text(L(row.accessDescription)).font(.system(size: 10)).foregroundColor(Palette.secondary)
                                }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                                    .background(model.folderEditing && model.folderIndex == index ? Palette.tint : Color.clear)
                                    .clipShape(RoundedRectangle(cornerRadius: 9)).contentShape(Rectangle())
                            }.buttonStyle(.plain).disabled(model.isBusy).accessibilityIdentifier("folder-\(index)")
                        }
                        if model.folderEditing && model.folderIndex == nil {
                            Label(L("新的文件夹"), systemImage: "folder.badge.plus").font(.system(size: 12, weight: .medium))
                                .foregroundColor(Palette.accent).padding(12)
                        }
                    }.frame(width: 205)
                    if model.folderEditing { editor.frame(maxWidth: .infinity) }
                    else {
                        EmptyState(symbol: "folder", title: "选择一个文件夹", detail: "在左侧选择文件夹，查看位置或调整访问权限。")
                            .frame(maxWidth: .infinity, minHeight: 340).panel()
                    }
                }
            }
            Text(L("移除访问权限不会删除磁盘上的文件。最多可添加 32 个文件夹。"))
                .font(.system(size: 11)).foregroundColor(Palette.secondary)
        }
    }
    private var editor: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(model.visibleFolderName).font(.system(size: 18, weight: .semibold)).lineLimit(2)
                    Text(L(model.folderIndex == nil ? "确认位置和权限后保存" : "文件夹设置"))
                        .font(.system(size: 12)).foregroundColor(Palette.secondary)
                }
                Spacer(minLength: 0)
                Image(systemName: "folder.fill").font(.system(size: 22)).foregroundColor(Palette.accent.opacity(0.7))
            }
            VStack(alignment: .leading, spacing: 9) {
                Text(L("文件夹位置")).font(.system(size: 12, weight: .semibold))
                Text(model.folderPath).font(.system(size: 12)).foregroundColor(Palette.secondary)
                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 16) {
                    Button(L("更换文件夹")) { Task { await model.pickFolder(replacing: true) } }
                        .buttonStyle(QuietActionStyle()).accessibilityIdentifier("replaceFolder")
                    Button(L("在访达中显示")) { model.revealFolder(model.folderPath) }.buttonStyle(QuietActionStyle())
                }
            }
            Divider()
            VStack(spacing: 0) {
                HStack {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(L("阅读文件")).font(.system(size: 13, weight: .semibold))
                        Text(L("查看和搜索所选文件夹中的内容")).font(.system(size: 12)).foregroundColor(Palette.secondary)
                    }
                    Spacer()
                    AccessPill(text: "已允许")
                }.padding(.vertical, 9)
                PermissionToggle(title: "允许编辑文件", detail: "允许 AI 创建、修改或整理文件内容。", value: $model.folderWrite, identifier: "folderAllowWrite")
                PermissionToggle(title: "允许运行任务", detail: "运行本机程序，例如测试项目、处理文档。", value: $model.folderExec, identifier: "folderAllowExec")
            }
            if model.folderExec {
                InformationLine(text: "运行任务使用你的本机账户权限，也可能修改文件；关闭“允许编辑文件”不会限制程序自身的写入能力。")
            }
            DisclosureGroup(isExpanded: $model.showFolderAdvanced) {
                FieldBlock("项目标识", hint: "已自动生成。已有连接可能引用此标识，通常无需修改。",
                           error: model.fieldErrors["id"]) {
                    TextField("project", text: $model.folderID).appField(error: model.fieldErrors["id"] != nil)
                        .accessibilityIdentifier("folderID")
                }.padding(.top, 12)
            } label: { Text(L("更多设置")).font(.system(size: 12)).foregroundColor(Palette.secondary) }
                .accessibilityIdentifier("folderAdvanced")
            Divider()
            VStack(alignment: .leading, spacing: 12) {
                Text(L(model.restartFootnote)).font(.system(size: 11)).foregroundColor(Palette.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 10) {
                    Button(L("保存并重启")) { Task { await model.saveFolder() } }.buttonStyle(PrimaryActionStyle())
                        .disabled(!model.folderCanSave).accessibilityIdentifier("saveFolder")
                    Button(L("取消")) { model.cancelEdits() }.buttonStyle(SecondaryActionStyle())
                        .accessibilityIdentifier("cancelFolder")
                    Spacer(minLength: 0)
                }
                if model.folderIndex != nil {
                    Button(L("移除访问权限")) { Task { await model.removeFolder() } }
                        .buttonStyle(.plain).font(.system(size: 11))
                        .foregroundColor(model.snapshot.workspaces.count > 1 ? Color.red.opacity(0.85) : Palette.secondary)
                        .padding(.top, 3).disabled(model.snapshot.workspaces.count <= 1)
                        .accessibilityIdentifier("removeFolder")
                    if model.snapshot.workspaces.count <= 1 {
                        Text(L("至少保留一个文件夹；要暂停使用，请在连接概览中停止连接。"))
                            .font(.system(size: 11)).foregroundColor(Palette.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }.panel(padding: 21).disabled(model.isBusy)
    }
}

struct ConnectionPage: View {
    @ObservedObject private var localization = Localization.shared
    @ObservedObject var model: AppModel
    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            PageTitle(title: model.snapshot.configured ? "连接设置" : "连接你的第一台电脑",
                      subtitle: model.snapshot.configured ? "管理电脑名称和连接信息。已有的连接密钥无需重复填写。" : "完成一次设置，之后打开应用就能管理本机连接。")
            VStack(alignment: .leading, spacing: 23) {
                HStack {
                    Label(L("连接信息"), systemImage: "link").font(.system(size: 15, weight: .semibold))
                    Spacer()
                    Button(L("这些信息在哪里？")) { model.openHelp("connection") }.buttonStyle(QuietActionStyle())
                        .accessibilityIdentifier("connectionHelp")
                }
                FieldBlock("电脑名称", hint: "起一个容易辨认的名称，例如“我的 MacBook”或“办公室电脑”。",
                           error: model.fieldErrors["computer_name"]) {
                    TextField(L("我的 MacBook"), text: $model.name).appField(error: model.fieldErrors["computer_name"] != nil)
                        .accessibilityIdentifier("computerName")
                }
                FieldBlock("连接 ID（Tunnel ID）", hint: "从 OpenAI 的连接配置中复制完整 ID，以 tunnel_ 开头。",
                           error: model.fieldErrors["tunnel_id"]) {
                    TextField("tunnel_…", text: $model.tunnelID).appField(error: model.fieldErrors["tunnel_id"] != nil)
                        .accessibilityIdentifier("tunnelID")
                }
                if model.snapshot.credentialStatus == "stored" && !model.replacingKey {
                    HStack(spacing: 10) {
                        Image(systemName: "lock.shield").font(.system(size: 22)).foregroundColor(Palette.success)
                        VStack(alignment: .leading, spacing: 5) {
                            Text(L("连接密钥已安全保存")).font(.system(size: 13, weight: .semibold))
                            Text(L("保存在 macOS 钥匙串中，不会显示密钥内容。")).font(.system(size: 12)).foregroundColor(Palette.secondary)
                        }
                        Spacer(minLength: 5)
                        Button(L("更换密钥")) { model.replacingKey = true }.buttonStyle(SecondaryActionStyle())
                            .accessibilityIdentifier("replaceKey")
                    }.padding(14).background(Palette.sidebar).clipShape(RoundedRectangle(cornerRadius: 9))
                } else {
                    FieldBlock("连接密钥（Runtime key）",
                               hint: model.snapshot.credentialStatus == "stored" ? "留空将继续使用已保存的密钥。新密钥只会在保存时写入钥匙串。" : "使用具有 Tunnels Read + Use 权限的连接密钥；无需为模型调用配置密钥。",
                               error: model.fieldErrors["runtime_key"]) {
                        SecureField(L(model.snapshot.credentialStatus == "stored" ? "留空可沿用已有密钥" : "粘贴连接密钥"), text: $model.runtimeKey)
                            .appField(error: model.fieldErrors["runtime_key"] != nil).accessibilityIdentifier("runtimeKey")
                    }
                    if model.snapshot.credentialStatus == "unavailable" {
                        InformationLine(text: "暂时无法确认钥匙串状态。请在系统提示中解锁钥匙串，再刷新状态；也可以填写新的连接密钥。")
                    }
                }
            }.panel(padding: 24)

            if !model.snapshot.configured {
                VStack(alignment: .leading, spacing: 17) {
                    Label(L("选择第一个文件夹"), systemImage: "folder").font(.system(size: 15, weight: .semibold))
                    Text(L("选择平时工作的文件夹。可以稍后添加更多文件夹和调整权限。"))
                        .font(.system(size: 12)).foregroundColor(Palette.secondary).fixedSize(horizontal: false, vertical: true)
                    if let folder = model.firstFolder {
                        HStack(alignment: .top, spacing: 12) {
                            Image(systemName: "folder.fill").foregroundColor(Palette.accent).font(.system(size: 20))
                            VStack(alignment: .leading, spacing: 6) {
                                Text(URL(fileURLWithPath: string(folder["root"])).lastPathComponent).font(.system(size: 13, weight: .semibold))
                                Text(string(folder["root"])).font(.system(size: 11)).foregroundColor(Palette.secondary)
                                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                            }
                            Spacer(minLength: 0)
                            Button(L("更换")) { Task { await model.pickFolder(forFirstSetup: true) } }.buttonStyle(QuietActionStyle())
                        }.padding(14).background(Palette.sidebar).clipShape(RoundedRectangle(cornerRadius: 9))
                        PermissionToggle(title: "允许编辑文件", detail: "允许创建、修改和整理文件。", value: $model.firstFolderWrite, identifier: "firstFolderAllowWrite")
                        PermissionToggle(title: "允许运行任务", detail: "使用本机程序完成工作，可能修改文件。", value: $model.firstFolderExec, identifier: "firstFolderAllowExec")
                        if model.firstFolderExec {
                            InformationLine(text: "运行任务使用本机账户权限，也可能修改所选文件夹以外的文件。")
                        }
                    } else if model.snapshot.workspaces.isEmpty {
                        Button { Task { await model.pickFolder(forFirstSetup: true) } } label: {
                            Label(L("选择文件夹"), systemImage: "plus")
                        }.buttonStyle(SecondaryActionStyle()).accessibilityIdentifier("firstFolder")
                    } else {
                        Text(LF("将沿用已有的 %d 个项目文件夹。", model.snapshot.workspaces.count))
                            .font(.system(size: 13)).foregroundColor(Palette.success)
                    }
                }.panel(padding: 24)
            }

            HStack(alignment: .top, spacing: 18) {
                VStack(alignment: .leading, spacing: 7) {
                    Text(L(model.restartFootnote)).font(.system(size: 12)).foregroundColor(Palette.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    if model.connectionDirty {
                        Text(L("有未保存的修改")).font(.system(size: 11)).foregroundColor(Palette.accent)
                    }
                }
                Spacer(minLength: 0)
                if model.connectionDirty {
                    Button(L("取消修改")) { model.cancelEdits() }.buttonStyle(SecondaryActionStyle())
                        .accessibilityIdentifier("cancelConnection")
                }
                Button(L(model.connectionSaveTitle)) { Task { await model.saveConnection() } }
                    .buttonStyle(PrimaryActionStyle()).disabled(!model.connectionCanSave)
                    .accessibilityIdentifier("saveConnection")
            }
        }.frame(maxWidth: 760, alignment: .leading).disabled(model.isBusy)
    }
}

struct DiagnosticsPage: View {
    @ObservedObject private var localization = Localization.shared
    @ObservedObject var model: AppModel
    var body: some View {
        VStack(alignment: .leading, spacing: 23) {
            PageTitle(title: "环境检查", subtitle: "需要时检查本机能力。检查不会读取你的工作文档，也不会自动申请系统授权。")
            VStack(alignment: .leading, spacing: 18) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 7) {
                        Label(L("文档处理"), systemImage: "doc.on.doc").font(.system(size: 15, weight: .semibold))
                        Text(L("查看 Excel、Word、PowerPoint 的基础读写环境。"))
                            .font(.system(size: 12)).foregroundColor(Palette.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer()
                    Button(L("检查环境")) { Task { await model.runDiagnostics(kind: "office") } }
                        .buttonStyle(SecondaryActionStyle()).accessibilityIdentifier("checkOffice")
                }
                if model.officeChecks.isEmpty {
                    Text(L("尚未检查")).font(.system(size: 12)).foregroundColor(Palette.secondary).padding(.vertical, 12)
                } else {
                    ForEach(Array(model.officeChecks.enumerated()), id: \.offset) { _, row in diagnosticRow(row) }
                    Text(L(model.officeSummary)).font(.system(size: 11)).foregroundColor(Palette.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Divider()
                HStack {
                    Text(L("用临时示例验证基础读写，不会打开个人文件。")).font(.system(size: 11))
                        .foregroundColor(Palette.secondary).fixedSize(horizontal: false, vertical: true)
                    Spacer()
                    Button(L("验证基础读写")) { Task { await model.runDiagnostics(kind: "office", verify: true) } }
                        .buttonStyle(QuietActionStyle()).accessibilityIdentifier("verifyOffice")
                }
            }.panel()
            VStack(alignment: .leading, spacing: 18) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 7) {
                        Label(L("系统授权"), systemImage: "hand.raised").font(.system(size: 15, weight: .semibold))
                        Text(L("常规文件、终端和独立 Chrome 集成无需开启；仅在特定工作流需要时申请。"))
                            .font(.system(size: 12)).foregroundColor(Palette.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer()
                    Button(L("检查系统授权")) { Task { await model.runDiagnostics(kind: "permissions") } }
                        .buttonStyle(SecondaryActionStyle()).accessibilityIdentifier("checkPermissions")
                }
                if model.permissionChecks.isEmpty {
                    Text(L("尚未检查")).font(.system(size: 12)).foregroundColor(Palette.secondary)
                } else {
                    ForEach(Array(model.permissionChecks.enumerated()), id: \.offset) { _, row in diagnosticRow(row) }
                    Text(L(model.permissionSummary)).font(.system(size: 11)).foregroundColor(Palette.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Divider()
                ForEach(["screen_capture", "accessibility", "post_event"], id: \.self) { kind in
                    HStack {
                        Text(L(kind == "screen_capture" ? "屏幕录制" : kind == "accessibility" ? "辅助功能" : "键鼠事件"))
                            .font(.system(size: 12))
                        Spacer()
                        Button(L("申请授权")) { Task { await model.requestPermission(kind) } }
                            .buttonStyle(QuietActionStyle()).accessibilityIdentifier("request-\(kind)")
                        Button(L("打开系统设置")) { model.openURL(kind) }
                            .buttonStyle(QuietActionStyle()).padding(.leading, 8)
                    }
                }
                InformationLine(text: "检测结果属于当前连接进程。授权后如仍未生效，请重新启动连接，再检查一次。")
            }.panel()
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: "globe").font(.system(size: 24, weight: .light)).foregroundColor(Palette.accent)
                VStack(alignment: .leading, spacing: 7) {
                    Text(L("需要操作浏览器？")).font(.system(size: 14, weight: .semibold))
                    Text(L("ChatGPT 的 Chrome 集成是独立连接，请在 ChatGPT 中启用。这里不会代替它显示连接状态。"))
                        .font(.system(size: 12)).foregroundColor(Palette.secondary).fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
                Button(L("查看指引")) { model.openHelp("browser") }.buttonStyle(QuietActionStyle())
            }.panel()
        }.disabled(model.isBusy)
    }
    private func diagnosticRow(_ row: JSONObject) -> some View {
        let status = string(row["status"])
        let good = status == "available"
        let neutral = status == "unknown" || status == "unavailable"
        let color = good ? Palette.success : neutral ? Palette.secondary : Color(red: 0.69, green: 0.35, blue: 0.16)
        let label = good ? "可用" : status == "denied" ? "未授权" : status == "missing" ? "缺少组件" : status == "unknown" ? "未确认" : status == "unavailable" ? "暂不可用" : "需处理"
        return HStack(alignment: .top, spacing: 12) {
            Image(systemName: good ? "checkmark.circle" : neutral ? "minus.circle" : "exclamationmark.circle")
                .foregroundColor(color).padding(.top, 2)
            VStack(alignment: .leading, spacing: 5) {
                Text(L(string(row["title"]))).font(.system(size: 13, weight: .medium))
                Text(L(string(row["detail"]))).font(.system(size: 12)).foregroundColor(Palette.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            Text(L(label)).font(.system(size: 11, weight: .medium)).foregroundColor(color).padding(.top, 2)
        }.padding(.vertical, 7)
    }
}

struct LogsPage: View {
    @ObservedObject private var localization = Localization.shared
    @ObservedObject var model: AppModel
    var body: some View {
        VStack(alignment: .leading, spacing: 23) {
            PageTitle(title: "运行记录", subtitle: "遇到问题时查看最近的记录。这里只显示有限条目，并隐藏可识别的密钥内容。")
            HStack(spacing: 12) {
                Picker(L("记录类型"), selection: $model.logSource) {
                    Text(L("连接问题")).tag("errors")
                    Text(L("连接运行")).tag("runtime")
                    Text(L("启动准备")).tag("bootstrap")
                }.pickerStyle(.segmented).frame(maxWidth: 370).accessibilityIdentifier("logSource")
                    .onChange(of: model.logSource) { _ in Task { await model.loadLogs() } }
                Spacer(minLength: 0)
                Button { Task { await model.loadLogs() } } label: { Label(L("刷新"), systemImage: "arrow.clockwise") }
                    .buttonStyle(SecondaryActionStyle()).accessibilityIdentifier("refreshLogs")
                Button { model.copy(model.logText) } label: { Label(L("复制"), systemImage: "doc.on.doc") }
                    .buttonStyle(SecondaryActionStyle()).disabled(model.logText.isEmpty)
                    .accessibilityIdentifier("copyLogs")
            }.disabled(model.isBusy)
            VStack(alignment: .leading, spacing: 0) {
                HStack {
                    Circle().fill(Palette.secondary.opacity(0.3)).frame(width: 6, height: 6)
                    Text(L(model.logSource == "errors" ? "最近的连接问题" : model.logSource == "runtime" ? "最近的连接运行记录" : "最近的启动准备记录"))
                        .font(.system(size: 11, weight: .medium)).foregroundColor(Palette.secondary)
                    Spacer()
                    Text(L("最多 150 行")).font(.system(size: 10)).foregroundColor(Palette.secondary)
                }.padding(15).background(Palette.sidebar)
                Divider()
                if model.logText.isEmpty {
                    EmptyState(symbol: "text.badge.checkmark", title: model.logsLoaded ? "暂无记录" : "还没有读取记录",
                               detail: model.logsLoaded ? "此类型暂时没有可显示的运行信息。" : "点击刷新，读取最近的运行信息。").frame(minHeight: 320)
                } else {
                    GeometryReader { geometry in
                        ScrollView([.horizontal, .vertical]) {
                            Text(model.logText).font(.system(size: 11, design: .monospaced))
                                .foregroundColor(Palette.ink).textSelection(.enabled)
                                .fixedSize(horizontal: true, vertical: true).padding(18)
                                .frame(minWidth: geometry.size.width, minHeight: geometry.size.height, alignment: .topLeading)
                        }
                    }.frame(height: 360).accessibilityIdentifier("logContent")
                }
            }.clipShape(RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(Palette.line, lineWidth: 1))
            HStack {
                Text(L(model.logsTruncated ? "内容较长，已仅显示最近部分。" : "需要更多细节时，可打开本机日志文件夹。"))
                    .font(.system(size: 11)).foregroundColor(Palette.secondary)
                Spacer()
                Button(L("打开日志文件夹")) { model.openLogs() }.buttonStyle(QuietActionStyle())
            }
        }
    }
}

struct HelpView: View {
    @ObservedObject private var localization = Localization.shared
    @ObservedObject var model: AppModel
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(L("使用 Codex Local MCP")).font(.system(size: 18, weight: .semibold))
                Spacer()
                Button(L("完成")) { model.showHelp = false }.buttonStyle(SecondaryActionStyle()).keyboardShortcut(.cancelAction)
                    .accessibilityIdentifier("closeHelp")
            }.padding(25)
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 25) {
                    if model.helpTopic == "about" {
                        VStack(alignment: .leading, spacing: 10) {
                            Text("Codex Local MCP  \(model.snapshot.version)").font(.system(size: 20, weight: .semibold))
                            HStack(spacing: 6) {
                                Text(L("作者")).foregroundColor(Palette.secondary)
                                Button("Sugar") { model.openURL("author") }.buttonStyle(QuietActionStyle())
                                    .accessibilityIdentifier("aboutAuthorHomepage")
                            }
                            Text(L("由 Sugar 维护的社区项目，非 OpenAI 官方客户端。"))
                                .font(.system(size: 12)).foregroundColor(Palette.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Divider()
                    }
                    if model.helpTopic == "browser" {
                        helpSection("连接浏览器", "如需查看和操作 Chrome 页面，请在 ChatGPT 中启用浏览器集成，并按集成提示授权。浏览器连接与本机文件连接分别管理。")
                    }
                    helpSection("1. 准备连接信息", "在 OpenAI 中创建本机连接，取得 Tunnel ID 和连接密钥（Runtime key）。连接密钥需要 Tunnels Read + Use 权限。将两项内容粘贴到“连接设置”，密钥会写入 macOS 钥匙串。")
                    HStack(spacing: 12) {
                        Button(L("打开连接设置")) { model.openURL("tunnels") }.buttonStyle(SecondaryActionStyle())
                        Button(L("查看连接文档")) { model.openURL("connection_guide") }.buttonStyle(QuietActionStyle())
                    }
                    helpSection("2. 选择项目文件夹", "选择你希望 AI 协助处理的文件夹，无需移动文件。新文件夹默认允许阅读和编辑；需要运行程序时，再开启“允许运行任务”。项目标识会自动生成。")
                    helpSection("3. 保存并连接", "点击“保存并连接”。看到“本机已就绪”后，回到 ChatGPT 选择插件，并让 AI 列出项目文件夹，验证完整连接。修改设置后重新连接，正在执行的任务可能中断。")
                    helpSection("如何理解权限", "“允许编辑文件”控制文件工具的修改能力。“允许运行任务”允许以本机账户权限执行程序，程序自身也可能修改文件。仅在两个开关都关闭时，文件工具提供只读访问。")
                    helpSection("会话活动如何统计", "概览按本次连接启动后的工具调用统计。最近 10 分钟为 600 秒，包含仍在进行的调用；活跃记录不表示会话仍在线。没有会话标识的调用会单独说明，重新连接后重新统计。")
                    helpSection("遇到问题时", "先检查连接设置，再查看“运行记录”。文档处理能力可以在“环境检查”中单独验证。常规文件、终端和独立 Chrome 集成无需这些可选系统授权；仅在特定工作流需要时申请。")
                    Divider()
                    HStack {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Codex Local MCP  \(model.snapshot.version)").font(.system(size: 13, weight: .semibold))
                            HStack(spacing: 5) {
                                Text(L("作者")).font(.system(size: 12)).foregroundColor(Palette.secondary)
                                Button("Sugar") { model.openURL("author") }.buttonStyle(QuietActionStyle())
                                    .accessibilityIdentifier("helpAuthorHomepage")
                            }
                        }
                        Spacer()
                        Button(L("GitHub 项目")) { model.openURL("repository") }.buttonStyle(QuietActionStyle())
                    }
                }.padding(25)
            }
        }.frame(width: 620, height: 650).background(Color.white).preferredColorScheme(.light)
    }
    private func helpSection(_ title: String, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L(title)).font(.system(size: 14, weight: .semibold))
            Text(L(detail)).font(.system(size: 13)).foregroundColor(Palette.secondary).lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
        }
    }
}
