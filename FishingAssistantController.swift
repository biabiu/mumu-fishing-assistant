import AppKit
import Foundation

final class Controller: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var projectRoot: URL {
        if let configuredRoot = Bundle.main.object(forInfoDictionaryKey: "FishingAssistantProjectRoot") as? String {
            return URL(fileURLWithPath: configuredRoot).standardizedFileURL
        }
        return Bundle.main.bundleURL
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .standardizedFileURL
    }
    private var python: String { projectRoot.appendingPathComponent(".venv/bin/python3").path }
    private var script: String { projectRoot.appendingPathComponent("mumu_fishing_clicker.py").path }
    private var logPath: String {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/MumuFishingAssistant.log").path
    }
    private var process: Process?
    private var logHandle: FileHandle?
    private var timer: Timer?
    private var panel: NSPanel!
    private var statusLabel: NSTextField!
    private var toggleButton: NSButton!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        buildWindow()
        refreshStatus()
        timer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            self?.refreshStatus()
        }
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
    }

    private func buildWindow() {
        panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 300, height: 156),
            styleMask: [.titled, .closable, .utilityWindow],
            backing: .buffered,
            defer: false
        )
        panel.title = "钓鱼助手"
        panel.level = .floating
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.delegate = self
        panel.center()

        let content = NSView(frame: NSRect(x: 0, y: 0, width: 300, height: 156))
        statusLabel = NSTextField(labelWithString: "正在检查状态…")
        statusLabel.font = .systemFont(ofSize: 14, weight: .medium)
        statusLabel.frame = NSRect(x: 24, y: 99, width: 252, height: 24)
        content.addSubview(statusLabel)

        toggleButton = NSButton(title: "启动钓鱼助手", target: self, action: #selector(toggleAssistant))
        toggleButton.bezelStyle = .rounded
        toggleButton.controlSize = .large
        toggleButton.frame = NSRect(x: 24, y: 48, width: 252, height: 38)
        content.addSubview(toggleButton)

        let hint = NSTextField(labelWithString: "窗口置顶；关闭窗口会隐藏控制器")
        hint.font = .systemFont(ofSize: 11)
        hint.textColor = .secondaryLabelColor
        hint.frame = NSRect(x: 24, y: 18, width: 252, height: 18)
        content.addSubview(hint)
        panel.contentView = content
    }

    @objc private func toggleAssistant() {
        if let process, process.isRunning {
            process.terminate()
            statusLabel.stringValue = "正在停止…"
            return
        }
        let pids = matchingAssistantPIDs()
        if !pids.isEmpty {
            for pid in pids { signal(pid: pid, signal: "-TERM") }
            statusLabel.stringValue = "正在停止…"
            return
        }
        startAssistant()
    }

    private func startAssistant() {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: python)
        task.arguments = [
            script, "--start", "--no-bobber-interval", "1", "--no-bobber-jitter", "0.2",
            "--button-jitter", "20",
            "--hold-min-ms", "50", "--hold-max-ms", "120",
            "--bobber-cooldown", "0.3",
            "--bobber-loss-grace", "0.25", "--input-latency", "0.045",
            "--max-size", "1920", "--max-fps", "60", "--seconds", "3600"
        ]
        try? FileManager.default.createDirectory(
            at: URL(fileURLWithPath: logPath).deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        FileManager.default.createFile(atPath: logPath, contents: nil)
        logHandle = FileHandle(forWritingAtPath: logPath)
        logHandle?.seekToEndOfFile()
        task.standardOutput = logHandle
        task.standardError = logHandle
        task.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                self?.process = nil
                self?.refreshStatus()
            }
        }
        do {
            try task.run()
            process = task
            statusLabel.stringValue = "正在运行"
            refreshStatus()
        } catch {
            statusLabel.stringValue = "启动失败：\(error.localizedDescription)"
        }
    }

    private func refreshStatus() {
        let running = (process?.isRunning ?? false) || !matchingAssistantPIDs().isEmpty
        statusLabel.stringValue = running ? "状态：运行中" : "状态：已停止"
        toggleButton.title = running ? "停止钓鱼助手" : "启动钓鱼助手"
    }

    private func matchingAssistantPIDs() -> [Int32] {
        let ps = Process()
        ps.executableURL = URL(fileURLWithPath: "/bin/ps")
        ps.arguments = ["-axo", "pid=,command="]
        let output = Pipe()
        ps.standardOutput = output
        ps.standardError = FileHandle.nullDevice
        do {
            try ps.run()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            ps.waitUntilExit()
            let listing = String(decoding: data, as: UTF8.self)
            return listing.split(separator: "\n").compactMap { line in
                let fields = line.split(maxSplits: 1, whereSeparator: { $0 == " " || $0 == "\t" })
                guard fields.count == 2, fields[1].contains(script),
                      let pid = Int32(fields[0]), pid != ProcessInfo.processInfo.processIdentifier else { return nil }
                return pid
            }
        } catch {
            return []
        }
    }

    private func signal(pid: Int32, signal: String) {
        let killer = Process()
        killer.executableURL = URL(fileURLWithPath: "/bin/kill")
        killer.arguments = [signal, String(pid)]
        try? killer.run()
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        panel.orderOut(nil)
        return false
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
}

let app = NSApplication.shared
let controller = Controller()
app.delegate = controller
app.run()
