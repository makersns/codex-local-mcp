import Foundation
import Darwin

struct ProcessOutput: Sendable {
    let status: Int32
    let stdout: Data
    let stderr: Data
}

private final class BoundedData: @unchecked Sendable {
    private let lock = NSLock()
    private var bytes = Data()
    private var tooLarge = false
    let limit: Int
    init(limit: Int) { self.limit = limit }
    func append(_ data: Data) {
        lock.lock(); defer { lock.unlock() }
        guard !tooLarge else { return }
        if bytes.count + data.count > limit {
            tooLarge = true
            return
        }
        bytes.append(data)
    }
    var result: (Data, Bool) {
        lock.lock(); defer { lock.unlock() }
        return (bytes, tooLarge)
    }
}

private final class InputWriteStatus: @unchecked Sendable {
    private let lock = NSLock()
    private var failed = false
    func markFailed() { lock.lock(); failed = true; lock.unlock() }
    var didFail: Bool { lock.lock(); defer { lock.unlock() }; return failed }
}

enum ProcessRunner {
    static func run(executable: String, arguments: [String], input: Data = Data(),
                    timeout: TimeInterval = 90, limit: Int = 2 * 1024 * 1024) async throws -> ProcessOutput {
        try await Task.detached(priority: .userInitiated) {
            try runBlocking(executable: executable, arguments: arguments, input: input, timeout: timeout, limit: limit)
        }.value
    }

    private static func runBlocking(executable: String, arguments: [String], input: Data,
                                    timeout: TimeInterval, limit: Int) throws -> ProcessOutput {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: executable)
            process.arguments = arguments
            let stdinPipe = Pipe(), stdoutPipe = Pipe(), stderrPipe = Pipe()
            process.standardInput = stdinPipe
            process.standardOutput = stdoutPipe
            process.standardError = stderrPipe
            var environment = ProcessInfo.processInfo.environment
            // Never inherit an unrelated Python environment into the bundled adapter.
            environment.removeValue(forKey: "PYTHONHOME")
            environment.removeValue(forKey: "PYTHONPATH")
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            process.environment = environment
            let stdout = BoundedData(limit: limit), stderr = BoundedData(limit: limit)
            let readers = DispatchGroup()
            let inputStatus = InputWriteStatus()
            let deadline = Date().addingTimeInterval(timeout)
            try process.run()
            // Close our copies of child-only pipe ends so process termination releases writers/readers.
            try? stdinPipe.fileHandleForReading.close()
            try? stdoutPipe.fileHandleForWriting.close()
            try? stderrPipe.fileHandleForWriting.close()
            _ = fcntl(stdinPipe.fileHandleForWriting.fileDescriptor, F_SETNOSIGPIPE, 1)
            for (handle, collector) in [(stdoutPipe.fileHandleForReading, stdout), (stderrPipe.fileHandleForReading, stderr)] {
                readers.enter()
                DispatchQueue.global(qos: .userInitiated).async {
                    defer { readers.leave() }
                    while true {
                        do {
                            guard let bytes = try handle.read(upToCount: 16384), !bytes.isEmpty else { break }
                            collector.append(bytes)
                        } catch { break }
                    }
                }
            }
            readers.enter()
            DispatchQueue.global(qos: .userInitiated).async {
                defer {
                    try? stdinPipe.fileHandleForWriting.close()
                    readers.leave()
                }
                do { try stdinPipe.fileHandleForWriting.write(contentsOf: input) }
                catch { inputStatus.markFailed() }
            }
            var timedOut = false
            while process.isRunning {
                if Date() >= deadline || stdout.result.1 || stderr.result.1 || inputStatus.didFail {
                    timedOut = Date() >= deadline
                    process.terminate()
                    let grace = Date().addingTimeInterval(2)
                    while process.isRunning && Date() < grace { Thread.sleep(forTimeInterval: 0.05) }
                    if process.isRunning { kill(process.processIdentifier, SIGKILL) }
                    break
                }
                Thread.sleep(forTimeInterval: 0.05)
            }
            process.waitUntilExit()
            _ = readers.wait(timeout: .now() + 3)
            try? stdoutPipe.fileHandleForReading.close()
            try? stderrPipe.fileHandleForReading.close()
            if timedOut {
                throw BridgeError.message("这次操作等待时间较长。请查看连接状态后重试；已保存的设置会保留。")
            }
            if inputStatus.didFail {
                throw BridgeError.message("无法将设置交给连接组件，请检查连接状态后重试。")
            }
            if stdout.result.1 || stderr.result.1 {
                throw BridgeError.message("连接组件返回的内容过多，已停止读取。请重新打开应用后重试。")
            }
            return ProcessOutput(status: process.terminationStatus, stdout: stdout.result.0, stderr: stderr.result.0)
    }
}

actor BridgeClient {
    let resources: URL
    let support: URL
    private var python: String?

    init(resources: URL, support: URL) {
        self.resources = resources
        self.support = support
    }

    func install(bundleURL: URL) async throws -> URL {
        let script = resources.appendingPathComponent("install-app.sh")
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw BridgeError.message("安装包缺少安装组件，请重新下载完整应用。")
        }
        let result = try await ProcessRunner.run(executable: "/bin/bash",
                                               arguments: [script.path, bundleURL.path], timeout: 120)
        if result.status != 0 {
            let safeReasons = [
                "应用完整性验证失败，请重新下载完整安装包。",
                "安装副本完整性验证失败。",
                "这台 Mac 已安装更新版本，已阻止旧安装包覆盖。",
                "另一个安装正在进行，请稍后重新打开应用。",
                "安装位置已有其他应用，未覆盖。",
                "安装位置已有其他文件，未覆盖。",
                "请安装到当前用户拥有的目录。"
            ]
            let lines = String(data: result.stderr, encoding: .utf8)?.components(separatedBy: .newlines) ?? []
            if let reason = safeReasons.first(where: { lines.contains($0) }) { throw BridgeError.message(reason) }
            throw BridgeError.message("应用尚未完成安装。请检查安装目录的可用空间和写入权限，再重新打开应用。")
        }
        guard let output = String(data: result.stdout, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
              output.hasPrefix("/"), output.hasSuffix(".app"), !output.contains("\n") else {
            throw BridgeError.message("应用尚未完成安装。请检查“应用程序”目录权限，再重新打开应用。")
        }
        return URL(fileURLWithPath: output, isDirectory: true).standardizedFileURL
    }

    func prepare(customSupport: Bool) async throws {
        let infoURL = support.appendingPathComponent("runtime-info.json")
        if customSupport, let data = try? Data(contentsOf: infoURL),
           let info = try? JSONSerialization.jsonObject(with: data) as? JSONObject,
           let path = info["python"] as? String,
           path.hasPrefix("/"), FileManager.default.isExecutableFile(atPath: path) {
            python = path
            return
        }
        let script = resources.appendingPathComponent("bootstrap.sh")
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw BridgeError.message("应用缺少运行组件，请使用完整安装包。")
        }
        let result = try await ProcessRunner.run(executable: "/bin/bash",
                                               arguments: [script.path, resources.path, support.path], timeout: 1800)
        guard result.status == 0,
              let path = String(data: result.stdout, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
              path.hasPrefix("/"), !path.contains("\n"),
              FileManager.default.isExecutableFile(atPath: path) else {
            throw BridgeError.message("运行环境准备未完成。请检查网络后重试，或打开启动记录查看原因。")
        }
        python = path
    }

    func request(_ requestData: Data, timeout: TimeInterval = 90) async throws -> Data {
        guard let python else { throw BridgeError.message("运行环境尚未准备好，请稍候再试。") }
        let api = resources.appendingPathComponent("desktop_api.py")
        guard FileManager.default.fileExists(atPath: api.path) else {
            throw BridgeError.message("应用组件不完整，请重新安装最新版。")
        }
        guard requestData.count <= 2 * 1024 * 1024 else {
            throw BridgeError.message("设置内容过多，请减少文件夹或检查配置。")
        }
        let result = try await ProcessRunner.run(executable: python,
                                               arguments: ["-B", api.path, "--support", support.path],
                                               input: requestData, timeout: timeout)
        guard !result.stdout.isEmpty else {
            throw BridgeError.message("连接组件未能完成这次操作。请重试，或查看运行记录。")
        }
        // The API returns a safe error object even for nonzero exit status.
        // stderr is deliberately never echoed into a view, clipboard or log.
        return result.stdout
    }
}
