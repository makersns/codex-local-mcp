import Foundation

extension NativeSelfTests {
    static func runActivity() throws -> [String] {
        var checks: [String] = []
        func check(_ condition: Bool, _ name: String) throws {
            guard condition else { throw BridgeError.message("Native activity self-test failed: \(name)") }
            checks.append(name)
        }
        let legacy = ActivitySnapshot(raw: [:])
        try check(!legacy.available && legacy.reason == "unsupported" &&
                  legacy.activeSessionValue == "—" && legacy.recentSessionValue == "—" &&
                  legacy.runningTaskValue == "—", "activity-legacy-backend-is-unknown-not-zero")

        let unavailable = ActivitySnapshot(raw: ["available": false, "reason": "unavailable",
                                                "active_sessions": 0, "recent_sessions": 0, "running_tasks": 0])
        try check(unavailable.activeSessionValue == "—" && unavailable.runningTaskValue == "—",
                  "activity-unavailable-never-shows-fallback-zero")
        let stopped = DesktopSnapshot.preview(state: "stopped").activity
        try check(!stopped.available && stopped.reason == "not_running", "activity-stopped-preview-has-no-statistics")

        let sample = ActivitySnapshot.preview()
        let known = ActivitySnapshot(raw: sample)
        try check(known.available && known.activeSessionValue == "2" && known.recentSessionValue == "3" &&
                  known.activeCalls == 3 && known.runningTaskValue == "1",
                  "activity-distinguishes-sessions-calls-and-tasks")
        let emptyRaw = ActivitySnapshot.preview(sample: false)
        let empty = ActivitySnapshot(raw: emptyRaw)
        try check(empty.available && empty.activeSessionValue == "0" && empty.recentSessionValue == "0" &&
                  empty.runningTaskValue == "0" && !empty.hasActivity,
                  "activity-valid-empty-snapshot-shows-real-zero")
        try check(known.instanceID != empty.instanceID && empty.sessions.isEmpty,
                  "activity-restart-resets-instance-and-session-history")

        var unknownRaw = emptyRaw
        unknownRaw["active_calls"] = 2
        unknownRaw["unknown_active_calls"] = 2
        unknownRaw["unknown_recent_calls"] = 0
        unknownRaw["coverage_complete"] = false
        let longUnknown = ActivitySnapshot(raw: unknownRaw)
        try check(longUnknown.activeSessionValue == "未识别" && longUnknown.recentSessionValue == "未识别" &&
                  longUnknown.sessions.isEmpty && longUnknown.coverageMessage != nil,
                  "activity-long-unknown-call-is-recent-without-invented-session")

        unknownRaw = emptyRaw
        unknownRaw["unknown_recent_calls"] = 2
        unknownRaw["coverage_complete"] = false
        let recentUnknown = ActivitySnapshot(raw: unknownRaw)
        try check(recentUnknown.activeSessionValue == "0" && recentUnknown.recentSessionValue == "未识别",
                  "activity-recent-unknown-call-does-not-imply-current-call")

        unknownRaw = sample
        unknownRaw["unknown_active_calls"] = 1
        unknownRaw["unknown_recent_calls"] = 1
        unknownRaw["coverage_complete"] = false
        let partial = ActivitySnapshot(raw: unknownRaw)
        try check(partial.activeSessionValue == "≥2" && partial.recentSessionValue == "≥3",
                  "activity-partial-session-counts-are-lower-bounds")

        var limitedRaw = emptyRaw
        limitedRaw["capacity_limited"] = true
        limitedRaw["coverage_complete"] = false
        let limited = ActivitySnapshot(raw: limitedRaw)
        try check(limited.activeSessionValue == "—" && limited.recentSessionValue == "—" &&
                  limited.coverageMessage != nil, "activity-capacity-loss-is-not-zero")

        var invalidRaw = sample
        invalidRaw["active_calls"] = NSNull()
        let missing = ActivitySnapshot(raw: invalidRaw)
        try check(!missing.available && missing.reason == "invalid_response" && missing.activeSessionValue == "—",
                  "activity-incomplete-response-is-unavailable")
        invalidRaw = sample
        invalidRaw["running_tasks"] = -1
        try check(!ActivitySnapshot(raw: invalidRaw).available, "activity-negative-count-is-rejected")
        invalidRaw["running_tasks"] = true
        try check(!ActivitySnapshot(raw: invalidRaw).available, "activity-boolean-count-is-rejected")
        invalidRaw = sample
        invalidRaw["window_seconds"] = 300
        try check(!ActivitySnapshot(raw: invalidRaw).available, "activity-window-contract-is-not-silently-relabelled")

        var many = sample
        many["sessions"] = (1...40).map { ["number": $0, "active_calls": 0,
                                          "last_activity_at": "2026-11-23T09:00:00Z",
                                          "last_operation": "read_file", "last_workspace": "missing"] as JSONObject }
        let bounded = ActivitySnapshot(raw: many)
        try check(bounded.sessions.count == 32 && bounded.sessionsTruncated, "activity-session-detail-is-bounded")
        let row = bounded.sessions[0]
        try check(row.operationTitle == "读取文件" && row.workspaceTitle(in: []) == "本地项目",
                  "activity-details-use-plain-language-and-folder-fallback")
        let future = SessionActivity(["number": 1, "active_calls": 0, "last_operation": "future_private_tool",
                                      "session_id": "must-not-appear", "last_workspace": "hidden-id"])
        try check(future?.operationTitle == "使用本机工具" && future?.title == "会话 1",
                  "activity-details-never-display-unknown-identifiers")
        try check(ActivitySnapshot.displayTime("2026-11-23T09:00:00.123+00:00") != nil &&
                  ActivitySnapshot.displayTime("invalid") == nil,
                  "activity-timestamps-support-fractions-and-invalid-fallback")
        return checks
    }
}
