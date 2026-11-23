import Foundation
import AppKit

typealias JSONObject = [String: Any]
func object(_ value: Any?) -> JSONObject { value as? JSONObject ?? [:] }
func objects(_ value: Any?) -> [JSONObject] { value as? [JSONObject] ?? [] }
func string(_ value: Any?, _ fallback: String = "") -> String { value as? String ?? fallback }
func flag(_ value: Any?, _ fallback: Bool = false) -> Bool { value as? Bool ?? fallback }
func jsonData(_ object: Any) throws -> Data {
    try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes])
}
func equalJSON(_ lhs: Any, _ rhs: Any) -> Bool {
    guard let a = try? jsonData(lhs), let b = try? jsonData(rhs) else { return false }
    return a == b
}

enum AppPage: String, CaseIterable, Identifiable {
    case overview, folders, connection, diagnostics, logs
    var id: String { rawValue }
    var title: String {
        switch self {
        case .overview: return "连接概览"
        case .folders: return "项目文件夹"
        case .connection: return "连接设置"
        case .diagnostics: return "环境检查"
        case .logs: return "运行记录"
        }
    }
    var symbol: String {
        switch self {
        case .overview: return "rectangle.connected.to.line.below"
        case .folders: return "folder"
        case .connection: return "slider.horizontal.3"
        case .diagnostics: return "checkmark.shield"
        case .logs: return "doc.text"
        }
    }
}

struct WorkspaceRecord {
    let raw: JSONObject
    var id: String { string(raw["id"]) }
    var root: String { string(raw["root"]) }
    var allowWrite: Bool { flag(raw["allow_write"]) }
    var allowExec: Bool { flag(raw["allow_exec"]) }
    var displayName: String {
        let name = URL(fileURLWithPath: root).lastPathComponent
        return name.isEmpty ? id : name
    }
    var displayPath: String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return root.hasPrefix(home + "/") ? "~" + root.dropFirst(home.count) : root
    }
    var accessDescription: String {
        if allowExec { return allowWrite ? "可编辑 · 可运行任务" : "可运行任务" }
        return allowWrite ? "可阅读 · 可编辑" : "仅阅读"
    }
}

struct DesktopSnapshot {
    var raw: JSONObject
    var app: JSONObject { object(raw["app"]) }
    var connection: JSONObject { object(raw["connection"]) }
    var status: JSONObject { object(raw["status"]) }
    var runtime: JSONObject { object(raw["runtime"]) }
    var activity: ActivitySnapshot { ActivitySnapshot(raw: object(raw["activity"]), connectionState: state) }
    var workspaceDocument: JSONObject { object(raw["workspace_document"]) }
    var workspaces: [WorkspaceRecord] { objects(workspaceDocument["workspaces"]).map(WorkspaceRecord.init) }
    var revision: String { string(raw["revision"]) }
    var version: String { string(app["version"], "0.6.0") }
    var configured: Bool { flag(connection["configured"]) }
    var computerName: String { string(connection["computer_name"]) }
    var tunnelID: String { string(connection["tunnel_id"]) }
    var credentialStatus: String { string(connection["credential_status"], "unavailable") }
    var state: String { string(status["state"], "needs_setup") }
    var isReady: Bool { state == "ready" }
    var isManaged: Bool { flag(status["managed"]) }
    var upgradePending: Bool { flag(status["upgrade_pending"]) }
    var title: String {
        let supplied = string(status["title"])
        if !supplied.isEmpty { return supplied }
        switch state {
        case "ready": return "本机已就绪"
        case "connecting": return "正在连接"
        case "stopped": return "连接已停止"
        case "port_conflict": return "连接暂时无法启动"
        case "error": return "需要处理连接问题"
        default: return "开始连接你的电脑"
        }
    }
    var message: String { string(status["message"]) }
    var shortTunnelID: String {
        guard tunnelID.count > 24 else { return tunnelID }
        return String(tunnelID.prefix(13)) + "…" + String(tunnelID.suffix(6))
    }
    static var empty: DesktopSnapshot {
        DesktopSnapshot(raw: [
            "app": ["name": "Codex Local MCP", "version": "0.6.0", "author": "Sugar"],
            "connection": ["configured": false, "computer_name": "", "tunnel_id": "", "credential_status": "missing"],
            "status": ["state": "needs_setup", "title": "开始连接你的电脑", "managed": false],
            "workspace_document": ["workspaces": []], "revision": "", "runtime": ["available": false]
        ])
    }
    static func preview(state: String = "ready") -> DesktopSnapshot {
        var value = empty.raw
        value["connection"] = [
            "configured": state != "needs_setup",
            "computer_name": state == "needs_setup" ? "" : L("我的 MacBook"),
            "tunnel_id": state == "needs_setup" ? "" : "tunnel_0123456789abcdef0123456789abcdef",
            "credential_status": state == "needs_setup" ? "missing" : "stored"
        ]
        value["status"] = [
            "state": state,
            "title": state == "ready" ? "本机已就绪" : state == "needs_setup" ? "开始连接你的电脑" : state == "port_conflict" ? "连接暂时无法启动" : "连接已停止",
            "message": state == "port_conflict" ? "连接端口正由其他程序使用。关闭原来的连接后再试。" : "",
            "managed": state == "ready", "upgrade_pending": false, "port": 8080
        ]
        value["workspace_document"] = [
            "workspaces": state == "needs_setup" ? [] : [
                ["id": "project", "root": "/Users/demo/Projects/" + L("创作项目"), "allow_write": true, "allow_exec": true],
                ["id": "documents", "root": "/Users/demo/Documents/" + L("工作资料"), "allow_write": false, "allow_exec": false]
            ],
            "state_dir": "/preview/state", "max_file_bytes": 1048576
        ]
        value["revision"] = String(repeating: "0", count: 64)
        value["runtime"] = ["available": true, "message": "界面预览"]
        value["activity"] = state == "ready" ? ActivitySnapshot.preview() : ActivitySnapshot.unavailable(
            reason: "not_running", message: "启动连接后，就能在这里查看会话活动。")
        return DesktopSnapshot(raw: value)
    }
}

struct APIReply {
    let ok: Bool
    let data: JSONObject
    let error: JSONObject
    init(data bytes: Data) throws {
        guard let raw = try JSONSerialization.jsonObject(with: bytes) as? JSONObject,
              let ok = raw["ok"] as? Bool else {
            throw BridgeError.message("连接组件返回了无法识别的内容。请重试，或重新安装应用。")
        }
        if let version = raw["api_version"] as? Int, version != 1 {
            throw BridgeError.message("连接组件版本与界面不匹配，请重新打开最新版应用。")
        }
        self.ok = ok
        self.data = object(raw["data"])
        self.error = object(raw["error"])
    }
    var message: String { string(error["message"], string(data["message"], "操作暂时未完成，请重试。")) }
    var saved: Bool { flag(error["saved"]) || flag(data["saved"]) }
    var fieldErrors: [String: String] { error["field_errors"] as? [String: String] ?? [:] }
}

enum BridgeError: Error, LocalizedError {
    case message(String)
    var errorDescription: String? {
        switch self { case .message(let message): return message }
    }
}

struct LaunchOptions {
    var preview = false
    var selfTest = false
    var exercise = false
    var initialPage = AppPage.overview
    var previewState = "ready"
    var previewLanguage: AppLanguage?
    var support: URL
    var customSupport = false
    var resources: URL
    var screenshot: URL?
    var windowWidth: CGFloat = 1240
    var windowHeight: CGFloat = 820
    var quitAfterScreenshot = false

    init(arguments: [String] = CommandLine.arguments) throws {
        support = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Sugar Local MCP Desktop", isDirectory: true)
        resources = Bundle.main.resourceURL ?? URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        var i = 1
        func next(_ name: String) throws -> String {
            guard i + 1 < arguments.count else { throw BridgeError.message(LF("%@ 缺少参数。", name)) }
            i += 1
            return arguments[i]
        }
        while i < arguments.count {
            switch arguments[i] {
            case "--preview": preview = true
            case "--self-test": selfTest = true
            case "--exercise": exercise = true
            case "--page":
                let value = try next("--page")
                guard let page = AppPage(rawValue: value) else { throw BridgeError.message("未知的页面。") }
                initialPage = page
            case "--preview-language":
                let value = try next("--preview-language")
                guard let language = AppLanguage.parse(value) else { throw BridgeError.message("未知的预览语言。") }
                previewLanguage = language
            case "--preview-state":
                let value = try next("--preview-state")
                guard ["ready", "needs_setup", "stopped", "port_conflict", "error"].contains(value) else { throw BridgeError.message("未知的预览状态。") }
                previewState = value
            case "--support":
                let value = try next("--support")
                guard value.hasPrefix("/") && value != "/" else { throw BridgeError.message("测试目录必须使用具体的绝对路径。") }
                support = URL(fileURLWithPath: value, isDirectory: true).standardizedFileURL
                customSupport = true
            case "--resources":
                let value = try next("--resources")
                guard value.hasPrefix("/") else { throw BridgeError.message("资源目录必须使用绝对路径。") }
                resources = URL(fileURLWithPath: value, isDirectory: true)
            case "--screenshot":
                let value = try next("--screenshot")
                guard value.hasPrefix("/") else { throw BridgeError.message("截图需要绝对路径。") }
                screenshot = URL(fileURLWithPath: value)
            case "--quit-after-screenshot": quitAfterScreenshot = true
            case "--window-width":
                guard let value = Double(try next("--window-width")), value >= 920, value <= 2400 else { throw BridgeError.message("窗口宽度应为 920–2400。") }
                windowWidth = value
            case "--window-height":
                guard let value = Double(try next("--window-height")), value >= 640, value <= 1800 else { throw BridgeError.message("窗口高度应为 640–1800。") }
                windowHeight = value
            default:
                if !arguments[i].hasPrefix("-psn_") { throw BridgeError.message("无法识别启动选项。") }
            }
            i += 1
        }
        if previewLanguage != nil && !preview { throw BridgeError.message("预览语言选项只允许在明确的预览模式使用。") }
        if exercise && !preview { throw BridgeError.message("交互自检只允许在明确的预览模式运行。") }
        if quitAfterScreenshot && screenshot == nil { throw BridgeError.message("请先指定截图文件。") }
    }
}

func suggestedWorkspaceID(path: String, existing: [String]) -> String {
    let last = URL(fileURLWithPath: path).lastPathComponent
    let filtered = last.replacingOccurrences(of: "[^A-Za-z0-9_-]+", with: "-", options: .regularExpression)
    var base = String(filtered.trimmingCharacters(in: CharacterSet(charactersIn: "-_")).lowercased().prefix(64))
    if base.isEmpty { base = "project" }
    var result = base, number = 2
    let taken = Set(existing)
    while taken.contains(result) {
        let suffix = "-\(number)"
        result = String(base.prefix(64 - suffix.count)).trimmingCharacters(in: CharacterSet(charactersIn: "-_")) + suffix
        number += 1
    }
    return result
}
