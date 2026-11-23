import Foundation
import CoreFoundation
import SwiftUI

/// Activity is reported by the running service; the desktop never infers sessions
/// from its own polling, a tunnel connection, or retained terminal jobs.
struct SessionActivity: Identifiable {
    let number: Int
    let activeCalls: Int
    let lastActivityAt: String
    let operation: String
    let workspace: String
    var id: Int { number }
    var title: String { "会话 \(number)" }

    init?(_ raw: JSONObject) {
        guard let number = ActivitySnapshot.count(raw["number"]), number > 0,
              let active = ActivitySnapshot.count(raw["active_calls"]) else { return nil }
        self.number = number
        activeCalls = active
        lastActivityAt = string(raw["last_activity_at"])
        operation = string(raw["last_operation"])
        workspace = string(raw["last_workspace"])
    }

    var operationTitle: String {
        switch operation {
        case "workspace_list": return "查看可用文件夹"
        case "list_directory", "stat_path": return "查看文件夹"
        case "read_file": return "读取文件"
        case "search_text": return "搜索内容"
        case "write_file", "edit_file": return "编辑文件"
        case "create_directory": return "新建文件夹"
        case "move_path", "copy_path", "delete_path": return "整理文件"
        case "run_command": return "运行任务"
        case "process_list", "read_process": return "查看任务"
        case "write_process": return "继续任务"
        case "stop_process": return "停止任务"
        default: return "使用本机工具"
        }
    }

    func workspaceTitle(in workspaces: [WorkspaceRecord]) -> String {
        workspaces.first(where: { $0.id == workspace })?.displayName ?? "本地项目"
    }
}

struct ActivitySnapshot {
    let raw: JSONObject
    let connectionState: String

    init(raw: JSONObject, connectionState: String = "ready") {
        self.raw = raw
        self.connectionState = connectionState
    }

    static func count(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID(),
              let integer = Int(number.stringValue), integer >= 0 else { return nil }
        return integer
    }

    private var validFlags: Bool {
        ["available", "metadata_seen", "coverage_complete", "capacity_limited"].allSatisfy {
            guard let value = raw[$0] as? NSNumber else { return false }
            return CFGetTypeID(value) == CFBooleanGetTypeID()
        }
    }
    private var validCounts: Bool {
        ["active_sessions", "recent_sessions", "active_calls", "unknown_active_calls",
         "unknown_recent_calls", "running_tasks"].allSatisfy { Self.count(raw[$0]) != nil }
    }
    var available: Bool {
        flag(raw["available"]) && validFlags && validCounts && Self.count(raw["window_seconds"]) == 600
    }
    var instanceID: String? {
        guard available, let value = raw["instance_id"] as? String, !value.isEmpty else { return nil }
        return value
    }
    var activeSessions: Int? { available ? Self.count(raw["active_sessions"]) : nil }
    var recentSessions: Int? { available ? Self.count(raw["recent_sessions"]) : nil }
    var activeCalls: Int? { available ? Self.count(raw["active_calls"]) : nil }
    var unknownActiveCalls: Int? { available ? Self.count(raw["unknown_active_calls"]) : nil }
    var unknownRecentCalls: Int? { available ? Self.count(raw["unknown_recent_calls"]) : nil }
    var runningTasks: Int? { available ? Self.count(raw["running_tasks"]) : nil }
    var capacityLimited: Bool { available && flag(raw["capacity_limited"]) }
    var coverageComplete: Bool { available && flag(raw["coverage_complete"]) }
    var sessionsTruncated: Bool { available && (flag(raw["sessions_truncated"]) || objects(raw["sessions"]).count > 32) }
    var sessions: [SessionActivity] {
        guard available else { return [] }
        var seen = Set<Int>()
        return objects(raw["sessions"]).compactMap(SessionActivity.init).filter {
            seen.insert($0.number).inserted
        }.prefix(32).map { $0 }
    }

    var reason: String {
        if available { return "" }
        if flag(raw["available"]) { return "invalid_response" }
        if raw.isEmpty { return connectionState == "ready" ? "unsupported" : "not_running" }
        return string(raw["reason"], "unavailable")
    }

    var unavailableMessage: String {
        guard !available else { return "" }
        if reason != "invalid_response", !raw.isEmpty {
            let supplied = string(raw["message"]).trimmingCharacters(in: .whitespacesAndNewlines)
            if !supplied.isEmpty { return String(supplied.prefix(400)) }
        }
        switch reason {
        case "not_running": return "启动连接后，就能在这里查看会话活动。"
        case "unsupported": return "当前连接尚不提供活动统计。重新连接以应用最新版组件。"
        case "isolated": return "隔离测试不会读取日常连接的活动。"
        case "invalid_response": return "活动统计暂时无法读取，请稍后重试。"
        default: return "暂时无法更新活动统计，连接恢复后会自动刷新。"
        }
    }

    private func sessionValue(_ known: Int?, unknown: Bool) -> String {
        guard available, let known else { return "—" }
        if unknown && known == 0 { return "未识别" }
        if capacityLimited && known == 0 { return "—" }
        return unknown || capacityLimited ? "≥\(known)" : "\(known)"
    }
    var activeSessionValue: String {
        sessionValue(activeSessions, unknown: (unknownActiveCalls ?? 0) > 0)
    }
    var recentSessionValue: String {
        // A still-running request belongs in the recent window even if it began
        // more than 600 seconds ago.
        sessionValue(recentSessions, unknown: (unknownRecentCalls ?? 0) > 0 || (unknownActiveCalls ?? 0) > 0)
    }
    var runningTaskValue: String { runningTasks.map(String.init) ?? "—" }
    var activeCallCaption: String {
        guard let activeCalls else { return "等待活动数据" }
        return activeCalls == 0 ? L("当前没有工具调用") : LF("%d 个工具调用正在进行", activeCalls)
    }
    var coverageMessage: String? {
        guard available else { return nil }
        let unknownNow = unknownActiveCalls ?? 0
        let unknownRecent = unknownRecentCalls ?? 0
        if unknownNow > 0 || unknownRecent > 0 {
            let scope = unknownNow > 0 ? LF("有 %d 个正在进行的调用未提供会话标识", unknownNow) : L("最近有调用未提供会话标识")
            let message = LF("%@，无法归入会话。会话数量仅包含已识别来源。", scope)
            return capacityLimited ? message + " " + L("活动较多，统计也可能不完整。") : message
        }
        if capacityLimited || !coverageComplete {
            return "活动较多或来源信息不完整，会话数量仅显示已识别部分。"
        }
        return nil
    }
    var hasActivity: Bool {
        available && ((recentSessions ?? 0) > 0 || (activeCalls ?? 0) > 0 ||
                      (unknownRecentCalls ?? 0) > 0 || (runningTasks ?? 0) > 0)
    }

    static func displayTime(_ source: Any?, withDate: Bool = false) -> String? {
        guard let text = source as? String, !text.isEmpty else { return nil }
        let parser = ISO8601DateFormatter()
        var date = parser.date(from: text)
        if date == nil {
            parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            date = parser.date(from: text)
        }
        guard let date else { return nil }
        return LT(date, dateStyle: withDate ? .short : .none, timeStyle: withDate ? .short : .medium)
    }

    static func unavailable(reason: String, message: String) -> JSONObject {
        [
            "available": false, "reason": reason, "message": message, "window_seconds": 600,
            "observed_at": NSNull(), "statistics_started_at": NSNull(), "instance_id": NSNull(),
            "active_sessions": NSNull(), "recent_sessions": NSNull(), "active_calls": NSNull(),
            "unknown_active_calls": NSNull(), "unknown_recent_calls": NSNull(),
            "running_tasks": NSNull(), "metadata_seen": NSNull(), "coverage_complete": NSNull(),
            "capacity_limited": NSNull(), "last_activity_at": NSNull(), "last_operation": NSNull(),
            "last_workspace": NSNull(), "sessions": [], "sessions_truncated": false
        ]
    }

    static func preview(sample: Bool = true, now: Date = Date()) -> JSONObject {
        let formatter = ISO8601DateFormatter()
        func time(_ seconds: Double) -> String { formatter.string(from: now.addingTimeInterval(seconds)) }
        return [
            "available": true, "reason": NSNull(), "message": "界面预览 · 示例活动",
            "window_seconds": 600, "observed_at": time(0),
            "statistics_started_at": time(sample ? -1320 : 0), "instance_id": UUID().uuidString,
            "active_sessions": sample ? 2 : 0, "recent_sessions": sample ? 3 : 0,
            "active_calls": sample ? 3 : 0, "unknown_active_calls": 0, "unknown_recent_calls": 0,
            "running_tasks": sample ? 1 : 0, "metadata_seen": sample,
            "coverage_complete": true, "capacity_limited": false,
            "last_activity_at": sample ? time(-2) as Any : NSNull(),
            "last_operation": sample ? "read_file" as Any : NSNull(),
            "last_workspace": sample ? "project" as Any : NSNull(),
            "sessions": sample ? [
                ["number": 1, "active_calls": 2, "last_activity_at": time(-2), "last_operation": "read_file", "last_workspace": "project"],
                ["number": 2, "active_calls": 1, "last_activity_at": time(-12), "last_operation": "run_command", "last_workspace": "project"],
                ["number": 3, "active_calls": 0, "last_activity_at": time(-180), "last_operation": "search_text", "last_workspace": "documents"]
            ] : [], "sessions_truncated": false
        ]
    }
}

struct ActivityPanel: View {
    @ObservedObject private var localization = Localization.shared
    let activity: ActivitySnapshot
    let workspaces: [WorkspaceRecord]
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 10) {
                Label(L("会话活动"), systemImage: "waveform.path").font(.system(size: 15, weight: .semibold))
                Spacer(minLength: 8)
                if activity.available, let updated = ActivitySnapshot.displayTime(activity.raw["observed_at"]) {
                    Text(LF("更新于 %@", updated)).font(.system(size: 10)).foregroundColor(Palette.secondary)
                } else {
                    Text(L("暂无统计")).font(.system(size: 10)).foregroundColor(Palette.secondary)
                }
            }
            HStack(alignment: .top, spacing: 16) {
                metric(activity.activeSessionValue, title: "正在调用的会话",
                       caption: activity.activeCallCaption, identifier: "activityActiveSessions")
                metric(activity.recentSessionValue, title: "近 10 分钟活跃会话",
                       caption: "有过调用，不表示仍在线", identifier: "activityRecentSessions")
                metric(activity.runningTaskValue, title: "运行中任务",
                       caption: "已启动且尚未结束的程序", identifier: "activityRunningTasks")
            }
            if !activity.available {
                Text(L(activity.unavailableMessage)).font(.system(size: 12)).foregroundColor(Palette.secondary)
                    .fixedSize(horizontal: false, vertical: true).accessibilityIdentifier("activityUnavailable")
            } else {
                if let message = activity.coverageMessage {
                    HStack(alignment: .top, spacing: 7) {
                        Image(systemName: "info.circle").padding(.top, 1)
                        Text(L(message)).fixedSize(horizontal: false, vertical: true)
                    }.font(.system(size: 11)).foregroundColor(Palette.secondary)
                        .accessibilityElement(children: .combine).accessibilityIdentifier("activityCoverage")
                }
                if activity.hasActivity {
                    Button {
                        expanded.toggle()
                    } label: {
                        HStack(spacing: 7) {
                            Image(systemName: expanded ? "chevron.down" : "chevron.right").font(.system(size: 9, weight: .semibold))
                            Text(L(expanded ? "收起会话活动" : "查看匿名会话活动"))
                            Spacer(minLength: 0)
                            if !activity.sessions.isEmpty {
                                Text(LF("%d 个已识别会话", activity.sessions.count)).foregroundColor(Palette.secondary)
                            }
                        }.font(.system(size: 11)).contentShape(Rectangle())
                    }.buttonStyle(.plain).foregroundColor(Palette.accent)
                        .accessibilityIdentifier("activityDetails")
                        .accessibilityLabel(L(expanded ? "收起匿名会话活动" : "展开匿名会话活动"))
                        .accessibilityValue(L(expanded ? "已展开" : "已收起"))
                    if expanded { details }
                } else {
                    Text(L("尚无活动记录。在 ChatGPT 中调用一次工具后，这里会开始更新。"))
                        .font(.system(size: 11)).foregroundColor(Palette.secondary)
                        .fixedSize(horizontal: false, vertical: true).accessibilityIdentifier("activityEmpty")
                }
                Text(L("最近窗口为 10 分钟（600 秒），包含仍在进行的调用。重新连接后重新统计。"))
                    .font(.system(size: 10)).foregroundColor(Palette.secondary)
                    .fixedSize(horizontal: false, vertical: true).accessibilityIdentifier("activityWindow")
            }
        }.panel(padding: 20).accessibilityElement(children: .contain).accessibilityIdentifier("activityPanel")
    }

    private func metric(_ value: String, title: String, caption: String, identifier: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(L(value)).font(.system(size: value.count > 3 ? 22 : 28, weight: .semibold, design: .rounded))
                .monospacedDigit().foregroundColor(activity.available ? Palette.ink : Palette.secondary)
            Text(L(title)).font(.system(size: 12, weight: .medium))
            Text(L(caption)).font(.system(size: 10)).foregroundColor(Palette.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }.frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityElement(children: .combine).accessibilityLabel("\(L(title)): \(L(value))")
            .accessibilityHint(L(caption)).accessibilityIdentifier(identifier)
    }

    private func displayWorkspace(_ session: SessionActivity) -> String {
        workspaces.first(where: { $0.id == session.workspace })?.displayName ?? L("本地项目")
    }

    private var details: some View {
        VStack(alignment: .leading, spacing: 12) {
            Divider()
            if activity.sessions.isEmpty {
                Text(L("已收到调用，但还没有可识别的会话记录。"))
                    .font(.system(size: 11)).foregroundColor(Palette.secondary)
            }
            ForEach(activity.sessions) { session in
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(LF("会话 %d", session.number)).font(.system(size: 12, weight: .medium))
                        Text("\(L(session.operationTitle)) · \(displayWorkspace(session))")
                            .font(.system(size: 11)).foregroundColor(Palette.secondary).lineLimit(2)
                    }
                    Spacer(minLength: 8)
                    VStack(alignment: .trailing, spacing: 5) {
                        Text(session.activeCalls > 0 ? LF("%d 个调用进行中", session.activeCalls) : L("当前空闲"))
                            .font(.system(size: 11)).foregroundColor(session.activeCalls > 0 ? Palette.accent : Palette.secondary)
                        if let time = ActivitySnapshot.displayTime(session.lastActivityAt) {
                            Text(LF("最近调用 %@", time)).font(.system(size: 10)).foregroundColor(Palette.secondary)
                        }
                    }
                }.accessibilityElement(children: .combine).accessibilityIdentifier("activitySession-\(session.number)")
            }
            if activity.sessionsTruncated {
                Text(L("这里只展示最近的部分会话；上方数量包含其他已识别会话。"))
                    .font(.system(size: 10)).foregroundColor(Palette.secondary)
            }
            if let start = ActivitySnapshot.displayTime(activity.raw["statistics_started_at"], withDate: true) {
                Text(LF("本次统计始于 %@。编号为本次连接中的匿名序号。", start))
                    .font(.system(size: 10)).foregroundColor(Palette.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }.padding(.top, 1)
    }
}
