import AppKit
import SwiftUI

@main
struct CodexLocalMCPMain {
    @MainActor static func main() {
        do {
            let options = try LaunchOptions()
            if options.selfTest {
                Task.detached {
                    do {
                        var checks = try NativeSelfTests.run()
                        checks += try await NativeSelfTests.runBridge()
                        emit(["ok": true, "scope": "native model, protocol and subprocess transport", "checks": checks])
                        exit(0)
                    } catch {
                        emit(["ok": false, "error": error.localizedDescription])
                        exit(1)
                    }
                }
                dispatchMain()
            }
            let app = NSApplication.shared
            app.setActivationPolicy(.regular)
            let delegate = DesktopApplicationDelegate(options: options)
            app.delegate = delegate
            withExtendedLifetime(delegate) { app.run() }
        } catch {
            emit(["ok": false, "error": error.localizedDescription])
            exit(1)
        }
    }

    static func emit(_ value: JSONObject) {
        if let data = try? jsonData(value), let text = String(data: data, encoding: .utf8) {
            print(text)
            fflush(stdout)
        }
    }
}

@MainActor
final class DesktopApplicationDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let options: LaunchOptions
    let model: AppModel
    private var window: NSWindow?
    private var askingToClose = false

    init(options: LaunchOptions) {
        self.options = options
        self.model = AppModel(options: options)
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        configureMenu()
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: options.windowWidth, height: options.windowHeight),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable],
                              backing: .buffered, defer: false)
        self.window = window
        window.title = options.preview ? "Codex Local MCP · 界面预览" : "Codex Local MCP"
        window.contentMinSize = NSSize(width: 920, height: 640)
        window.titlebarAppearsTransparent = true
        window.backgroundColor = .white
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.identifier = NSUserInterfaceItemIdentifier("CodexLocalMCPWindow")
        window.setAccessibilityIdentifier("CodexLocalMCPWindow")
        let hosting = NSHostingView(rootView: RootView(model: model))
        hosting.setAccessibilityIdentifier("CodexLocalMCPRoot")
        window.contentView = hosting
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        Task { await start() }
    }

    private func start() async {
        do {
            let bundle = Bundle.main.bundleURL.standardizedFileURL
            if bundle.pathExtension == "app" && !options.preview && !options.customSupport && options.screenshot == nil {
                model.preparationMessage = "正在检查应用安装…"
                let installed = try await model.bridge.install(bundleURL: bundle)
                if installed != bundle {
                    let configuration = NSWorkspace.OpenConfiguration()
                    configuration.activates = true
                    try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                        NSWorkspace.shared.openApplication(at: installed, configuration: configuration) { _, error in
                            if let error { continuation.resume(throwing: error) }
                            else { continuation.resume() }
                        }
                    }
                    model.preparing = false
                    NSApp.terminate(nil)
                    return
                }
            }
            await model.prepare()
            if options.exercise {
                let checks = try await model.exercisePreview()
                CodexLocalMCPMain.emit(["ok": true, "scope": "isolated preview model exercise", "checks": checks])
            }
            if let target = options.screenshot {
                // Allow the live NSHostingView to lay out and complete its first display.
                try await Task.sleep(nanoseconds: 800_000_000)
                try captureWindow(to: target)
                if options.quitAfterScreenshot { exit(0) }
            }
        } catch {
            model.preparing = false
            model.showFailure(error, title: "暂时无法完成启动")
            if options.exercise || options.quitAfterScreenshot {
                CodexLocalMCPMain.emit(["ok": false, "error": error.localizedDescription])
                exit(1)
            }
        }
    }

    private func captureWindow(to target: URL) throws {
        guard let window, let view = window.contentView else {
            throw BridgeError.message("窗口尚未创建，无法截图。")
        }
        view.layoutSubtreeIfNeeded()
        window.displayIfNeeded()
        guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else {
            throw BridgeError.message("无法创建窗口图像。")
        }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        guard let png = bitmap.representation(using: .png, properties: [:]) else {
            throw BridgeError.message("无法生成 PNG 窗口截图。")
        }
        try FileManager.default.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
        try png.write(to: target, options: .atomic)
        CodexLocalMCPMain.emit(["ok": true, "screenshot": target.path,
                               "preview": options.preview, "page": model.page.rawValue,
                               "width": bitmap.pixelsWide, "height": bitmap.pixelsHigh])
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { window?.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
        return true
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if model.busyLabel != nil { NSSound.beep(); return false }
        guard model.hasUnsavedChanges else { return true }
        guard !askingToClose else { return false }
        askingToClose = true
        Task {
            let discard = await model.confirm(title: "关闭窗口前放弃修改？",
                                              detail: "这些修改还没有保存。你可以继续编辑，或放弃后关闭窗口。",
                                              action: "放弃并关闭", cancel: "继续编辑")
            askingToClose = false
            if discard { model.cancelEdits(); sender.performClose(nil) }
        }
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if model.busyLabel != nil { NSSound.beep(); return .terminateCancel }
        guard model.hasUnsavedChanges else { return .terminateNow }
        guard !askingToClose else { return .terminateCancel }
        askingToClose = true
        Task {
            let discard = await model.confirm(title: "退出前放弃未保存的修改？",
                                              detail: "退出界面不会停止已有的本机连接。未保存的修改将被放弃。",
                                              action: "放弃并退出", cancel: "继续编辑")
            askingToClose = false
            if discard { model.cancelEdits() }
            sender.reply(toApplicationShouldTerminate: discard)
        }
        return .terminateLater
    }

    private func configureMenu() {
        let menu = NSMenu()
        let appItem = NSMenuItem()
        menu.addItem(appItem)
        let application = NSMenu()
        application.addItem(withTitle: "关于 Codex Local MCP", action: #selector(showAbout), keyEquivalent: "").target = self
        application.addItem(.separator())
        application.addItem(withTitle: "隐藏 Codex Local MCP", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        application.addItem(.separator())
        application.addItem(withTitle: "退出 Codex Local MCP", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = application

        let editItem = NSMenuItem(title: "编辑", action: nil, keyEquivalent: "")
        let edit = NSMenu(title: "编辑")
        for (title, selector, key) in [
            ("撤销", Selector(("undo:")), "z"),
            ("剪切", #selector(NSText.cut(_:)), "x"),
            ("复制", #selector(NSText.copy(_:)), "c"),
            ("粘贴", #selector(NSText.paste(_:)), "v"),
            ("全选", #selector(NSText.selectAll(_:)), "a")
        ] { edit.addItem(withTitle: title, action: selector, keyEquivalent: key) }
        editItem.submenu = edit
        menu.addItem(editItem)

        let windowItem = NSMenuItem(title: "窗口", action: nil, keyEquivalent: "")
        let windows = NSMenu(title: "窗口")
        windows.addItem(withTitle: "最小化", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windows.addItem(withTitle: "关闭窗口", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        windowItem.submenu = windows
        menu.addItem(windowItem)
        NSApp.windowsMenu = windows

        let helpItem = NSMenuItem(title: "帮助", action: nil, keyEquivalent: "")
        let help = NSMenu(title: "帮助")
        help.addItem(withTitle: "Codex Local MCP 使用帮助", action: #selector(showHelp), keyEquivalent: "?").target = self
        helpItem.submenu = help
        menu.addItem(helpItem)
        NSApp.mainMenu = menu
    }

    @objc private func showAbout() { model.openHelp("about") }
    @objc private func showHelp() { model.openHelp() }
}
