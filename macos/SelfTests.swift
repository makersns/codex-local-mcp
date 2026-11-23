import Foundation

enum NativeSelfTests {
    static func runBridge() async throws -> [String] {
        var checks: [String] = []
        let input = Data(repeating: 65, count: 512 * 1024)
        let copied = try await ProcessRunner.run(executable: "/bin/cat", arguments: [], input: input, timeout: 5)
        guard copied.status == 0 && copied.stdout == input else { throw BridgeError.message("Pipe roundtrip failed") }
        checks.append("large-stdin-pipe-roundtrip")
        let before = Date()
        var rejectedBlockedInput = false
        do {
            _ = try await ProcessRunner.run(executable: "/bin/sleep", arguments: ["10"], input: input, timeout: 0.3)
        } catch { rejectedBlockedInput = true }
        guard rejectedBlockedInput && Date().timeIntervalSince(before) < 4 else {
            throw BridgeError.message("Blocked stdin exceeded process timeout")
        }
        checks.append("blocked-stdin-covered-by-timeout")
        var rejectedOutput = false
        do { _ = try await ProcessRunner.run(executable: "/usr/bin/yes", arguments: ["bounded"], timeout: 5, limit: 1024) }
        catch { rejectedOutput = true }
        guard rejectedOutput else { throw BridgeError.message("Oversized subprocess output accepted") }
        checks.append("subprocess-output-bounded")
        return checks
    }

    static func run() throws -> [String] {
        var checks: [String] = []
        func check(_ condition: Bool, _ name: String) throws {
            guard condition else { throw BridgeError.message("Native self-test failed: \(name)") }
            checks.append(name)
        }
        let first = suggestedWorkspaceID(path: "/Users/demo/工作资料", existing: [])
        try check(first == "project", "non-ascii-folder-id-fallback")
        try check(suggestedWorkspaceID(path: "/Users/demo/工作资料", existing: ["project", "project-2"]) == "project-3", "folder-id-collision")
        try check(suggestedWorkspaceID(path: "/Users/demo/" + String(repeating: "Long Folder ", count: 12), existing: []).count <= 64, "folder-id-length-limit")
        let execution = WorkspaceRecord(raw: ["id": "sample", "root": "/example/project", "allow_write": false, "allow_exec": true])
        try check(execution.accessDescription != "仅阅读", "execution-is-never-described-as-readonly")
        let readonly = WorkspaceRecord(raw: ["id": "sample", "root": "/example/project", "allow_write": false, "allow_exec": false])
        try check(readonly.accessDescription == "仅阅读", "readonly-needs-both-flags-off")
        try check(readonly.displayName == "project", "folder-name-is-primary")

        let reply = try APIReply(data: jsonData(["api_version": 1, "ok": false,
            "data": ["snapshot": DesktopSnapshot.preview().raw],
            "error": ["code": "future_error", "message": "saved but needs retry", "saved": true, "retryable": true,
                      "field_errors": ["tunnel_id": "incomplete"]]]))
        try check(reply.saved && !reply.ok, "partial-save-retains-snapshot")
        try check(reply.fieldErrors["tunnel_id"] == "incomplete", "field-errors-decoded")
        var rejectedVersion = false
        do { _ = try APIReply(data: jsonData(["api_version": 2, "ok": true, "data": [:]])) }
        catch { rejectedVersion = true }
        try check(rejectedVersion, "unsupported-protocol-version-rejected")
        var rejectedShape = false
        do { _ = try APIReply(data: Data("[]".utf8)) }
        catch { rejectedShape = true }
        try check(rejectedShape, "invalid-protocol-shape-rejected")
        let future = try APIReply(data: jsonData(["api_version": 1, "ok": true, "data": ["future": 1], "future": "allowed"]))
        try check(future.ok, "future-response-fields-allowed")

        let preview = try LaunchOptions(arguments: ["app", "--preview", "--page", "folders", "--window-width", "920", "--window-height", "640"])
        try check(preview.preview && preview.initialPage == .folders, "preview-options")
        try check(!preview.customSupport, "preview-does-not-select-real-support")
        let traditional = try LaunchOptions(arguments: ["app", "--preview", "--preview-language", "zh-TW"])
        try check(traditional.previewLanguage == .zhHant, "preview-language-region-alias")
        var rejectedLiveLanguage = false
        do { _ = try LaunchOptions(arguments: ["app", "--preview-language", "en"]) }
        catch { rejectedLiveLanguage = true }
        try check(rejectedLiveLanguage, "preview-language-option-cannot-affect-normal-launch")
        var rejectedLanguage = false
        do { _ = try LaunchOptions(arguments: ["app", "--preview", "--preview-language", "unrecognized"]) }
        catch { rejectedLanguage = true }
        try check(rejectedLanguage, "invalid-preview-language-is-rejected")
        var rejectedExercise = false
        do { _ = try LaunchOptions(arguments: ["app", "--exercise"]) }
        catch { rejectedExercise = true }
        try check(rejectedExercise, "exercise-requires-explicit-preview")
        var rejectedRelative = false
        do { _ = try LaunchOptions(arguments: ["app", "--support", "relative"]) }
        catch { rejectedRelative = true }
        try check(rejectedRelative, "isolated-support-needs-absolute-path")
        try check(DesktopSnapshot.preview(state: "needs_setup").workspaces.isEmpty, "setup-preview-has-no-user-folders")
        try check(DesktopSnapshot.preview().credentialStatus == "stored", "credential-status-without-secret")
        try check(!DesktopSnapshot.preview().raw.keys.contains("runtime_key"), "snapshot-has-no-secret")
        checks += try runActivity()
        return checks
    }
}
