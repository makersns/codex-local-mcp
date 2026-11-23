import AppKit
import SwiftUI

struct AppNotice {
    enum Kind { case success, info, error }
    var kind: Kind
    var title: String
    var detail: String
    var retryable = false
    var errorCode = ""
    var saved = false
    var fallbackDetail: String {
        if saved { return "设置已经保存，连接还需要处理。请查看诊断信息或运行记录。" }
        switch errorCode {
        case "conflict": return "设置已被其他操作修改，请重新载入后再保存。"
        case "validation": return "请检查标出的设置后再试。"
        case "connection_failed": return "连接尚未就绪。请检查连接设置或运行记录。"
        default: return "操作还需要处理。请查看诊断信息或运行记录。"
        }
    }
}

@MainActor
final class AppModel: ObservableObject {
    let options: LaunchOptions
    let bridge: BridgeClient
    @Published var page: AppPage
    @Published var snapshot = DesktopSnapshot.empty
    @Published var preparing = true
    @Published var prepared = false
    @Published var preparationMessage = "正在准备连接组件…"
    @Published var busyLabel: String?
    @Published var notice: AppNotice?
    @Published var fieldErrors: [String: String] = [:]
    @Published var showHelp = false
    @Published var helpTopic = "getting-started"
    @Published var name = ""
    @Published var tunnelID = ""
    @Published var runtimeKey = ""
    @Published var replacingKey = false
    @Published var firstFolder: JSONObject?
    @Published var firstFolderWrite = true
    @Published var firstFolderExec = false
    @Published var folderEditing = false
    @Published var folderIndex: Int?
    @Published var folderID = ""
    @Published var folderPath = ""
    @Published var folderWrite = true
    @Published var folderExec = false
    @Published var showFolderAdvanced = false
    @Published var officeChecks: [JSONObject] = []
    @Published var permissionChecks: [JSONObject] = []
    @Published var officeSummary = ""
    @Published var permissionSummary = ""
    @Published var logSource = "errors"
    @Published var logText = ""
    @Published var logsLoaded = false
    @Published var logsTruncated = false
    private var originalConnection = ["", ""]
    private var connectionRevision = ""
    private var folderRevision = ""
    private var folderDocument: JSONObject = [:]
    private var folderOriginal: JSONObject = [:]
    private var originalFolderFields = ["", ""]
    private var originalFolderPermissions = [true, false]
    private var pollTask: Task<Void, Never>?
    private var previewGeneration = 0

    init(options: LaunchOptions) {
        self.options = options
        page = options.initialPage
        bridge = BridgeClient(resources: options.resources, support: options.support)
    }
    var isBusy: Bool { busyLabel != nil || preparing }
    var connectionDirty: Bool {
        name != originalConnection[0] || tunnelID != originalConnection[1] ||
        !runtimeKey.isEmpty || firstFolder != nil
    }
    var folderDirty: Bool {
        guard folderEditing else { return false }
        return folderIndex == nil || folderID != originalFolderFields[0] ||
            folderPath != originalFolderFields[1] ||
            folderWrite != originalFolderPermissions[0] || folderExec != originalFolderPermissions[1]
    }
    var hasUnsavedChanges: Bool { connectionDirty || folderDirty }
    var connectionSaveTitle: String { snapshot.configured ? "保存并重启" : "保存并连接" }
    var connectionCanSave: Bool {
        prepared && !isBusy && connectionDirty &&
        !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        !tunnelID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        (snapshot.configured || firstFolder != nil || !snapshot.workspaces.isEmpty) &&
        (snapshot.credentialStatus == "stored" || !runtimeKey.isEmpty)
    }
    var folderCanSave: Bool {
        prepared && !isBusy && folderDirty && !folderPath.isEmpty && !folderID.isEmpty
    }
    var restartFootnote: String {
        snapshot.isManaged
            ? "保存后会重新连接，正在运行的任务可能中断。"
            : "设置保存后会尝试连接。"
    }
    var visibleFolderName: String {
        let value = URL(fileURLWithPath: folderPath).lastPathComponent
        return value.isEmpty ? L("新的文件夹") : value
    }

    func prepare() async {
        guard !prepared else { return }
        preparing = true
        notice = nil
        do {
            if options.preview {
                snapshot = .preview(state: options.previewState)
                prepared = true
                preparing = false
                hydrateConnection()
                if !snapshot.configured && page == .overview { page = .connection }
                if page == .folders, !snapshot.workspaces.isEmpty { editFolder(0) }
                if page == .logs { await loadLogs() }
                return
            }
            preparationMessage = "正在准备连接组件…首次使用可能需要几分钟。"
            try await bridge.prepare(customSupport: options.customSupport)
            prepared = true
            preparing = false
            await refresh(hydrate: true)
            if !snapshot.configured && page == .overview { page = .connection }
            startPolling()
        } catch {
            preparing = false
            notice = AppNotice(kind: .error, title: "准备还未完成",
                               detail: error.localizedDescription, retryable: true)
        }
    }

    func startPolling() {
        guard !options.preview else { return }
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 8_000_000_000)
                guard !Task.isCancelled, let self else { return }
                if !self.isBusy && NSApp.windows.contains(where: { $0.isVisible }) {
                    await self.refresh(silent: true)
                }
            }
        }
    }

    func request(_ action: String, payload: JSONObject = [:], timeout: TimeInterval = 90) async throws -> APIReply {
        let envelope: JSONObject = ["api_version": 1, "action": action, "payload": payload]
        if options.preview { return try previewRequest(action, payload: payload) }
        let bytes = try await bridge.request(jsonData(envelope), timeout: timeout)
        return try APIReply(data: bytes)
    }

    func refresh(hydrate: Bool = false, silent: Bool = false) async {
        guard prepared else { return }
        do {
            let reply = try await request("snapshot")
            guard reply.ok else {
                snapshot.raw["activity"] = ActivitySnapshot.unavailable(reason: "unavailable", message: "暂时无法更新活动统计，连接恢复后会自动刷新。")
                if !silent { showError(reply) }
                return
            }
            snapshot = DesktopSnapshot(raw: reply.data)
            if hydrate || !connectionDirty { hydrateConnection() }
        } catch {
            snapshot.raw["activity"] = ActivitySnapshot.unavailable(reason: "unavailable", message: "暂时无法更新活动统计，连接恢复后会自动刷新。")
            if !silent {
                notice = AppNotice(kind: .error, title: "暂时无法更新状态",
                                   detail: error.localizedDescription, retryable: true)
            }
        }
    }

    private func hydrateConnection() {
        name = snapshot.computerName.isEmpty ? (options.preview ? L("我的 MacBook") : Host.current().localizedName ?? L("我的 Mac")) : snapshot.computerName
        tunnelID = snapshot.tunnelID
        originalConnection = [name, tunnelID]
        connectionRevision = snapshot.revision
        runtimeKey = ""
        replacingKey = false
        firstFolder = nil
        firstFolderWrite = true
        firstFolderExec = false
    }

    func navigate(_ next: AppPage) async {
        guard page != next, !isBusy else { return }
        if hasUnsavedChanges {
            guard await confirm(title: "要放弃未保存的修改吗？",
                                detail: "你可以继续编辑，也可以放弃这次修改后切换页面。",
                                action: "放弃修改", cancel: "继续编辑") else { return }
            cancelEdits()
        }
        page = next
        fieldErrors = [:]
        if next == .logs && !logsLoaded { await loadLogs() }
    }

    func reloadSettings() async {
        guard !isBusy else { return }
        if hasUnsavedChanges {
            guard await confirm(title: "重新载入并放弃当前修改？",
                                detail: "将载入已经保存的最新设置，当前尚未保存的修改会被放弃。",
                                action: "重新载入", cancel: "继续编辑") else { return }
        }
        folderEditing = false
        notice = nil
        await refresh(hydrate: true)
        fieldErrors = [:]
    }

    func cancelEdits() {
        hydrateConnection()
        folderEditing = false
        firstFolder = nil
        fieldErrors = [:]
    }

    func editFolder(_ index: Int) {
        guard snapshot.workspaces.indices.contains(index) else { return }
        let row = snapshot.workspaces[index]
        folderIndex = index
        folderDocument = snapshot.workspaceDocument
        folderRevision = snapshot.revision
        folderOriginal = row.raw
        folderID = row.id
        folderPath = row.root
        folderWrite = row.allowWrite
        folderExec = row.allowExec
        originalFolderFields = [folderID, folderPath]
        originalFolderPermissions = [folderWrite, folderExec]
        showFolderAdvanced = false
        folderEditing = true
        fieldErrors = [:]
    }

    func selectFolder(_ index: Int) async {
        if folderDirty {
            guard await confirm(title: "保留当前修改吗？", detail: "切换文件夹前，请保存当前修改，或放弃后继续。",
                                action: "放弃并切换", cancel: "继续编辑") else { return }
        }
        editFolder(index)
    }

    func pickFolder(forFirstSetup: Bool = false, replacing: Bool = false) async {
        let panel = NSOpenPanel()
        panel.title = L(forFirstSetup ? "选择第一个项目文件夹" : "选择项目文件夹")
        panel.message = L("选择希望 AI 协助处理的文件夹。你可以稍后调整访问权限。")
        panel.prompt = L("选择文件夹")
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        if replacing, !folderPath.isEmpty { panel.directoryURL = URL(fileURLWithPath: folderPath) }
        let result: NSApplication.ModalResponse
        if let window = NSApp.keyWindow {
            result = await withCheckedContinuation { continuation in
                panel.beginSheetModal(for: window) { continuation.resume(returning: $0) }
            }
        } else { result = panel.runModal() }
        guard result == .OK, let url = panel.url else { return }
        await chooseFolder(path: url.path, forFirstSetup: forFirstSetup, replacing: replacing)
    }

    func chooseFolder(path: String, forFirstSetup: Bool = false, replacing: Bool = false) async {
        guard !isBusy else { return }
        if !forFirstSetup && !replacing && folderDirty {
            guard await confirm(title: "要放弃当前文件夹的修改吗？", detail: "当前修改尚未保存。",
                                action: "放弃并添加", cancel: "继续编辑") else { return }
        }
        busyLabel = "正在检查文件夹…"
        defer { busyLabel = nil }
        do {
            let ids = snapshot.workspaces.enumerated().filter { !(replacing && $0.offset == folderIndex) }.map { $0.element.id }
            let reply = try await request("suggest_workspace", payload: ["root": path, "existing_ids": ids])
            guard reply.ok else { showError(reply); return }
            let row = object(reply.data["workspace"])
            guard !string(row["id"]).isEmpty, !string(row["root"]).isEmpty else {
                throw BridgeError.message("无法识别所选文件夹，请重新选择。")
            }
            if forFirstSetup {
                firstFolder = row
                firstFolderWrite = flag(row["allow_write"], true)
                firstFolderExec = flag(row["allow_exec"])
            } else if replacing {
                folderPath = string(row["root"])
            } else {
                page = .folders
                folderDocument = snapshot.workspaceDocument
                folderRevision = snapshot.revision
                folderIndex = nil
                folderOriginal = row
                folderID = string(row["id"])
                folderPath = string(row["root"])
                folderWrite = flag(row["allow_write"], true)
                folderExec = flag(row["allow_exec"])
                originalFolderFields = ["", ""]
                originalFolderPermissions = [true, false]
                folderEditing = true
                showFolderAdvanced = false
            }
            fieldErrors = [:]
        } catch { showFailure(error, title: "未能添加文件夹") }
    }

    func saveFolder() async {
        guard folderCanSave else { return }
        var document = folderDocument
        var rows = objects(document["workspaces"])
        var row = folderOriginal
        row["id"] = folderID.trimmingCharacters(in: .whitespacesAndNewlines)
        row["root"] = folderPath.trimmingCharacters(in: .whitespacesAndNewlines)
        row["allow_write"] = folderWrite
        row["allow_exec"] = folderExec
        if let index = folderIndex, rows.indices.contains(index) { rows[index] = row }
        else { rows.append(row) }
        document["workspaces"] = rows
        await mutate("save_workspaces", payload: ["document": document, "expected_revision": folderRevision, "restart": true],
                     label: "正在保存文件夹并连接…")
    }

    func removeFolder() async {
        guard let index = folderIndex, !isBusy else { return }
        guard snapshot.workspaces.count > 1 else {
            notice = AppNotice(kind: .info, title: "请保留一个文件夹",
                               detail: "至少保留一个文件夹；要暂停使用，请在连接概览中停止连接。")
            return
        }
        guard await confirm(title: LF("移除“%@”的访问权限？", visibleFolderName),
                            detail: "磁盘上的文件会保留。移除访问后会立即重新连接，正在运行的任务可能中断。",
                            action: "移除并重启", cancel: "保留") else { return }
        var document = folderDocument
        var rows = objects(document["workspaces"])
        guard rows.indices.contains(index) else { return }
        rows.remove(at: index)
        document["workspaces"] = rows
        await mutate("save_workspaces", payload: ["document": document, "expected_revision": folderRevision, "restart": true],
                     label: "正在更新文件夹访问…")
    }

    func saveConnection() async {
        guard connectionCanSave else { return }
        var payload: JSONObject = [
            "connection": ["computer_name": name.trimmingCharacters(in: .whitespacesAndNewlines),
                           "tunnel_id": tunnelID.trimmingCharacters(in: .whitespacesAndNewlines)],
            "expected_revision": connectionRevision, "restart": true
        ]
        if !runtimeKey.isEmpty { payload["runtime_key"] = runtimeKey }
        let action = snapshot.configured ? "save_connection" : "setup"
        if action == "setup" {
            var doc = snapshot.workspaceDocument
            if var first = firstFolder {
                first["allow_write"] = firstFolderWrite
                first["allow_exec"] = firstFolderExec
                var rows = objects(doc["workspaces"])
                rows.append(first)
                doc["workspaces"] = rows
            }
            payload["document"] = doc
        }
        await mutate(action, payload: payload, label: snapshot.configured ? "正在保存并重新连接…" : "正在保存并连接…")
    }

    func lifecycle(_ action: String) async {
        guard !isBusy else { return }
        if action == "stop" || action == "restart" {
            guard await confirm(title: action == "stop" ? "停止本机连接？" : "重新启动连接？",
                                detail: "正在运行的任务可能中断。请先完成当前任务。",
                                action: action == "stop" ? "停止连接" : "重新连接", cancel: "取消") else { return }
        }
        await mutate(action, payload: action == "start" ? [:] : ["confirm": true],
                     label: action == "stop" ? "正在停止连接…" : "正在连接…")
    }

    private func mutate(_ action: String, payload: JSONObject, label: String) async {
        busyLabel = label
        fieldErrors = [:]
        notice = nil
        defer { busyLabel = nil }
        do {
            let reply = try await request(action, payload: payload, timeout: 120)
            if let value = reply.data["snapshot"] as? JSONObject {
                snapshot = DesktopSnapshot(raw: value)
            }
            if reply.ok || reply.saved {
                hydrateConnection()
                folderEditing = false
            }
            if reply.ok {
                notice = AppNotice(kind: .success, title: "已完成",
                                   detail: string(reply.data["message"], "设置已保存。"))
            } else { showError(reply) }
        } catch {
            // Retain the draft on a transport failure; a subsequent snapshot can establish whether it saved.
            showFailure(error, title: "操作尚未确认完成")
        }
    }

    func runDiagnostics(kind: String, verify: Bool = false) async {
        guard !isBusy else { return }
        busyLabel = verify ? "正在验证基础文档读写…" : kind == "office" ? "正在检查文档环境…" : "正在检查系统授权…"
        defer { busyLabel = nil }
        do {
            let reply = try await request("diagnostics", payload: ["kind": kind, "verify": verify, "include_libreoffice": false], timeout: 100)
            guard reply.ok else { showError(reply); return }
            if kind == "office" {
                officeChecks = objects(reply.data["checks"])
                officeSummary = string(reply.data["summary"])
            } else {
                permissionChecks = objects(reply.data["checks"])
                permissionSummary = string(reply.data["summary"])
            }
        } catch { showFailure(error, title: "检查未完成") }
    }

    func requestPermission(_ kind: String) async {
        guard !isBusy else { return }
        let title = kind == "screen_capture" ? "屏幕录制" : kind == "post_event" ? "键鼠事件" : "辅助功能"
        guard await confirm(title: LF("申请%@权限？", L(title)),
                            detail: "这是可选权限。系统会要求你确认授权，完成后请返回此处重新检查。",
                            action: "继续申请", cancel: "取消") else { return }
        busyLabel = "正在申请系统授权…"
        defer { busyLabel = nil }
        do {
            let reply = try await request("request_permission", payload: ["kind": kind, "confirm": true], timeout: 45)
            guard reply.ok else { showError(reply); return }
            if let rows = reply.data["checks"] as? [JSONObject] { permissionChecks = rows }
            notice = AppNotice(kind: .info, title: "请在系统设置中完成授权",
                               detail: string(reply.data["message"], "完成后，点击“检查系统授权”确认状态。"))
        } catch { showFailure(error, title: "授权申请未完成") }
    }

    func loadLogs() async {
        guard !isBusy, prepared else { return }
        busyLabel = "正在读取运行记录…"
        defer { busyLabel = nil }
        do {
            let reply = try await request("logs", payload: ["source": logSource, "lines": 150])
            guard reply.ok else { showError(reply); return }
            logText = string(reply.data["text"])
            logsLoaded = true
            logsTruncated = flag(reply.data["truncated"])
        } catch { showFailure(error, title: "暂时无法读取记录") }
    }

    func openLogs() {
        guard !options.preview else {
            notice = AppNotice(kind: .info, title: "预览模式", detail: "预览不会打开本机日志目录。")
            return
        }
        NSWorkspace.shared.open(options.support.appendingPathComponent("logs", isDirectory: true))
    }
    func revealFolder(_ path: String) {
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path, isDirectory: true)])
    }
    func openHelp(_ topic: String = "getting-started") { helpTopic = topic; showHelp = true }
    func openURL(_ kind: String) {
        let links = [
            "tunnels": "https://platform.openai.com/settings/organization/tunnels",
            "keys": "https://platform.openai.com/settings/organization/api-keys",
            "repository": "https://github.com/makersns/codex-local-mcp",
            "author": "https://sugar.makersns.com/",
            "connection_guide": "https://developers.openai.com/api/docs/guides/secure-mcp-tunnels",
            "screen_capture": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
            "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            "post_event": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
        ]
        if let text = links[kind], let url = URL(string: text) { NSWorkspace.shared.open(url) }
    }
    func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }
    func showFailure(_ error: Error, title: String) {
        notice = AppNotice(kind: .error, title: title, detail: error.localizedDescription, retryable: true)
    }
    private func showError(_ reply: APIReply) {
        fieldErrors = reply.fieldErrors
        notice = AppNotice(kind: .error, title: reply.saved ? "设置已保存，连接还需处理" : "还差一步",
                           detail: reply.message, retryable: flag(reply.error["retryable"]),
                           errorCode: string(reply.error["code"]), saved: reply.saved)
    }

    func confirm(title: String, detail: String, action: String, cancel: String) async -> Bool {
        let alert = NSAlert()
        alert.messageText = L(title)
        alert.informativeText = L(detail)
        alert.alertStyle = .warning
        alert.addButton(withTitle: L(action))
        alert.addButton(withTitle: L(cancel))
        alert.buttons.last?.keyEquivalent = "\r"
        alert.buttons.first?.keyEquivalent = ""
        if let window = NSApp.keyWindow {
            return await withCheckedContinuation { continuation in
                alert.beginSheetModal(for: window) { continuation.resume(returning: $0 == .alertFirstButtonReturn) }
            }
        }
        return alert.runModal() == .alertFirstButtonReturn
    }

    private func previewRequest(_ action: String, payload: JSONObject) throws -> APIReply {
        var data: JSONObject = [:]
        var error: JSONObject?
        switch action {
        case "snapshot": data = snapshot.raw
        case "suggest_workspace":
            let path = string(payload["root"])
            data = ["workspace": ["id": suggestedWorkspaceID(path: path, existing: payload["existing_ids"] as? [String] ?? []),
                                  "root": path, "allow_write": true, "allow_exec": false]]
        case "save_connection", "save_workspaces", "setup", "start", "stop", "restart":
            if action.hasPrefix("save") || action == "setup" {
                if string(payload["expected_revision"]) != snapshot.revision {
                    error = ["code": "conflict", "message": "设置已被其他操作修改，请重新载入后再保存。", "retryable": true]
                }
                if let connection = payload["connection"] as? JSONObject {
                    if string(connection["computer_name"]).isEmpty {
                        error = ["code": "validation", "message": "请为这台电脑起一个名称。", "field_errors": ["computer_name": "请填写电脑名称。"]]
                    } else if string(connection["tunnel_id"]).range(of: "^tunnel_(?:[a-z0-9]{4}_)?[a-z0-9]{32}$", options: .regularExpression) == nil {
                        error = ["code": "validation", "message": "请复制完整的连接 ID。", "field_errors": ["tunnel_id": "连接 ID 不完整，请重新复制。"]]
                    }
                }
            }
            if error == nil {
                var raw = snapshot.raw
                if var connection = payload["connection"] as? JSONObject {
                    connection["configured"] = true
                    connection["credential_status"] = "stored"
                    raw["connection"] = connection
                }
                if let document = payload["document"] as? JSONObject { raw["workspace_document"] = document }
                var status = object(raw["status"])
                status["state"] = action == "stop" ? "stopped" : "ready"
                status["title"] = action == "stop" ? "连接已停止" : "本机已就绪"
                status["managed"] = action != "stop"
                raw["status"] = status
                raw["activity"] = action == "stop" ? ActivitySnapshot.unavailable(
                    reason: "not_running", message: "启动连接后，就能在这里查看会话活动。") : ActivitySnapshot.preview(sample: false)
                previewGeneration += 1
                raw["revision"] = String(format: "%064x", previewGeneration)
                snapshot = DesktopSnapshot(raw: raw)
                data = ["snapshot": raw, "saved": action.hasPrefix("save") || action == "setup",
                        "restarted": action != "stop", "restart_required": false,
                        "message": "预览中的更改已应用；没有修改本机配置。"]
            }
        case "diagnostics":
            let kind = string(payload["kind"])
            data = ["kind": kind, "summary": "以下为界面预览示例，不代表本机实际检查结果。",
                    "checks": kind == "office" ? [
                        ["id": "xlsx", "title": "Excel 文件", "status": "available", "detail": "基础读写已验证（示例）"],
                        ["id": "docx", "title": "Word 文件", "status": "available", "detail": "基础读写已验证（示例）"],
                        ["id": "pptx", "title": "PowerPoint 文件", "status": "unknown", "detail": "尚未检查（示例）"]
                    ] : [
                        ["id": "screen_capture", "title": "屏幕录制", "status": "unknown", "detail": "尚未检查（示例）"],
                        ["id": "accessibility", "title": "辅助功能", "status": "unknown", "detail": "尚未检查（示例）"]
                    ]]
        case "logs":
            data = ["source": string(payload["source"]), "text": "界面预览 · 示例记录\n\n本机连接已就绪。\n项目文件夹已载入。\n\n这里展示经过脱敏的最近运行记录。", "truncated": false, "available": true]
        case "request_permission":
            data = ["message": "预览模式不会申请任何系统权限。"]
        default: error = ["code": "unsupported", "message": "预览中不支持这项操作。"]
        }
        return try APIReply(data: jsonData(["api_version": 1, "ok": error == nil,
                                           "data": data, "error": error as Any? ?? NSNull()]))
    }

    func exercisePreview() async throws -> [String] {
        guard options.preview else { throw BridgeError.message("只允许对预览数据执行交互自检。") }
        let original = snapshot
        var checks: [String] = []
        if snapshot.workspaces.isEmpty {
            name = "预览工作电脑"
            tunnelID = "tunnel_0123456789abcdef0123456789abcdef"
            runtimeKey = "preview-only-no-real-credential"
            await chooseFolder(path: "/preview/创作项目", forFirstSetup: true)
            await saveConnection()
            guard snapshot.configured && snapshot.workspaces.count == 1 else {
                throw BridgeError.message("首次配置自检失败。")
            }
            checks.append("first-setup-save")
        }
        editFolder(0)
        folderWrite = false
        folderExec = true
        if folderDirty { checks.append("folder-edit-dirty") }
        await saveFolder()
        guard snapshot.workspaces[0].allowWrite == false && snapshot.workspaces[0].allowExec,
              snapshot.workspaces[0].accessDescription != "仅阅读" else { throw BridgeError.message("权限表达自检失败。") }
        checks.append("folder-save-permission-semantics")
        guard snapshot.workspaceDocument["max_file_bytes"] as? Int == 1048576 else { throw BridgeError.message("隐藏设置保留失败。") }
        checks.append("preserve-complete-document")
        editFolder(0)
        folderPath = "/preview/changed"
        cancelEdits()
        guard snapshot.workspaces[0].root != "/preview/changed" else { throw BridgeError.message("取消编辑失败。") }
        checks.append("cancel-keeps-data")
        name = "我的工作电脑"
        await saveConnection()
        guard snapshot.computerName == "我的工作电脑" else { throw BridgeError.message("连接设置保存失败。") }
        checks.append("connection-save")
        let old = original.revision
        let conflict = try await request("save_workspaces", payload: ["expected_revision": old, "document": original.workspaceDocument])
        guard !conflict.ok else { throw BridgeError.message("并发冲突未处理。") }
        checks.append("stale-revision-rejected")
        await runDiagnostics(kind: "office")
        guard !officeChecks.isEmpty else { throw BridgeError.message("环境检查失败。") }
        checks.append("diagnostics-results")
        await loadLogs()
        guard logsLoaded else { throw BridgeError.message("运行记录失败。") }
        checks.append("bounded-log-view")
        snapshot = original
        cancelEdits()
        notice = nil
        page = options.initialPage
        if page == .folders { editFolder(0) }
        return checks
    }
}
