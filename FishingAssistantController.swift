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
    private var settingsButton: NSButton!
    private var settingsPanel: NSPanel?
    private var settingFields: [String: NSTextField] = [:]
    private var baitToggle: NSButton!
    private var waitBaitCooldownCheckbox: NSButton!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        UserDefaults.standard.register(defaults: [
            "settings.buttonJitter": 20,
            "settings.bobberCooldown": 0.3,
            "settings.bobberJitter": 0.05,
            "settings.bobberTimeOffsetMs": 0.0,
            "settings.noBobberInterval": 1.0,
            "settings.noBobberJitter": 0.2,
            "settings.referenceWidth": 1080,
            "settings.referenceHeight": 1920,
            "settings.buttonX": 540,
            "settings.buttonY": 1580,
            "settings.useBait": false,
            "settings.baitCheckInterval": 60.0,
            "settings.baitMaxConsecutive": 4,
            "settings.baitUseInterval": 3.0,
            "settings.waitBaitCooldown": false
        ])
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
            contentRect: NSRect(x: 0, y: 0, width: 340, height: 164),
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

        let content = NSView(frame: NSRect(x: 0, y: 0, width: 340, height: 164))
        statusLabel = NSTextField(labelWithString: "正在检查状态…")
        statusLabel.font = .systemFont(ofSize: 14, weight: .medium)
        statusLabel.frame = NSRect(x: 24, y: 112, width: 250, height: 24)
        content.addSubview(statusLabel)

        settingsButton = NSButton(title: "⚙", target: self, action: #selector(showSettings))
        settingsButton.isBordered = false
        settingsButton.font = .systemFont(ofSize: 19)
        settingsButton.toolTip = "钓鱼助手设置"
        settingsButton.frame = NSRect(x: 288, y: 105, width: 32, height: 32)
        content.addSubview(settingsButton)

        baitToggle = NSButton(checkboxWithTitle: "使用鱼饵", target: self, action: #selector(toggleBaitUsage(_:)))
        baitToggle.state = UserDefaults.standard.bool(forKey: "settings.useBait") ? .on : .off
        baitToggle.toolTip = "识别鱼饵并确认打窝；打窝期间暂停钓鱼点击。运行中的助手下次启动时读取新状态。"
        baitToggle.frame = NSRect(x: 24, y: 59, width: 104, height: 22)
        content.addSubview(baitToggle)

        toggleButton = NSButton(title: "启动钓鱼助手", target: self, action: #selector(toggleAssistant))
        toggleButton.bezelStyle = .rounded
        toggleButton.controlSize = .large
        toggleButton.frame = NSRect(x: 136, y: 51, width: 180, height: 38)
        content.addSubview(toggleButton)

        let hint = NSTextField(labelWithString: "窗口置顶；齿轮图标可调整识别和点击参数")
        hint.font = .systemFont(ofSize: 11)
        hint.textColor = .secondaryLabelColor
        hint.frame = NSRect(x: 24, y: 18, width: 292, height: 18)
        content.addSubview(hint)
        panel.contentView = content
    }

    @objc private func toggleBaitUsage(_ sender: NSButton) {
        UserDefaults.standard.set(sender.state == .on, forKey: "settings.useBait")
    }

    @objc private func showSettings() {
        if let settingsPanel {
            settingsPanel.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }

        let width: CGFloat = 380
        let height: CGFloat = 590
        let window = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: width, height: height),
            styleMask: [.titled, .closable, .utilityWindow],
            backing: .buffered,
            defer: false
        )
        window.title = "钓鱼助手设置"
        window.level = .floating
        let content = NSView(frame: NSRect(x: 0, y: 0, width: width, height: height))
        let rows: [(String, String)] = [
            ("buttonJitter", "按钮位置随机偏移 ± 像素"),
            ("bobberCooldown", "浮标阶段最短冷却（秒）"),
            ("bobberJitter", "浮标阶段额外随机延时（秒）"),
            ("bobberTimeOffsetMs", "浮标时机偏移（ms，正延后负提前）"),
            ("noBobberInterval", "非浮标阶段间隔（秒）"),
            ("noBobberJitter", "非浮标阶段随机抖动（±秒）"),
            ("referenceWidth", "参考画布宽度（像素）"),
            ("referenceHeight", "参考画布高度（像素）"),
            ("buttonX", "钓鱼按钮中心 X（像素）"),
            ("buttonY", "钓鱼按钮中心 Y（像素）"),
            ("baitCheckInterval", "鱼饵检测冷却（秒）"),
            ("baitMaxConsecutive", "每轮最多连续打窝次数"),
            ("baitUseInterval", "连续打窝间隔（秒）")
        ]
        let defaults = UserDefaults.standard
        settingFields.removeAll()
        for (index, row) in rows.enumerated() {
            let y = height - 54 - CGFloat(index) * 32
            let label = NSTextField(labelWithString: row.1)
            label.frame = NSRect(x: 20, y: y, width: 238, height: 22)
            content.addSubview(label)

            let field = NSTextField(string: String(describing: defaults.object(forKey: "settings.\(row.0)") ?? ""))
            field.alignment = .right
            field.frame = NSRect(x: 264, y: y - 2, width: 96, height: 25)
            content.addSubview(field)
            settingFields[row.0] = field
        }

        waitBaitCooldownCheckbox = NSButton(checkboxWithTitle: "等待冷却时间后再放鱼饵",
                                            target: nil, action: nil)
        waitBaitCooldownCheckbox.state = defaults.bool(forKey: "settings.waitBaitCooldown") ? .on : .off
        waitBaitCooldownCheckbox.frame = NSRect(x: 20, y: 112, width: 340, height: 22)
        content.addSubview(waitBaitCooldownCheckbox)

        let note = NSTextField(labelWithString: "保存后下次启动生效；打窝确认期间暂停钓鱼点击。")
        note.font = .systemFont(ofSize: 11)
        note.textColor = .secondaryLabelColor
        note.frame = NSRect(x: 20, y: 68, width: 340, height: 18)
        content.addSubview(note)

        let cancel = NSButton(title: "取消", target: self, action: #selector(cancelSettings))
        cancel.frame = NSRect(x: 190, y: 22, width: 76, height: 30)
        content.addSubview(cancel)
        let save = NSButton(title: "保存", target: self, action: #selector(saveSettings))
        save.bezelStyle = .rounded
        save.keyEquivalent = "\r"
        save.frame = NSRect(x: 276, y: 22, width: 84, height: 30)
        content.addSubview(save)
        window.contentView = content
        settingsPanel = window
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func cancelSettings() {
        settingsPanel?.close()
        settingsPanel = nil
    }

    @objc private func saveSettings() {
        func number(_ key: String) -> Double? {
            guard let text = settingFields[key]?.stringValue.trimmingCharacters(in: .whitespacesAndNewlines) else { return nil }
            return Double(text)
        }
        func integer(_ key: String) -> Int? {
            guard let text = settingFields[key]?.stringValue.trimmingCharacters(in: .whitespacesAndNewlines) else { return nil }
            return Int(text)
        }
        guard let buttonJitter = integer("buttonJitter"), buttonJitter >= 0,
              let bobberCooldown = number("bobberCooldown"), bobberCooldown > 0,
              let bobberJitter = number("bobberJitter"), bobberJitter >= 0,
              let bobberTimeOffsetMs = number("bobberTimeOffsetMs"), abs(bobberTimeOffsetMs) <= 5000,
              let noBobberInterval = number("noBobberInterval"), noBobberInterval > 0,
              let noBobberJitter = number("noBobberJitter"), noBobberJitter >= 0,
              noBobberJitter < noBobberInterval,
              let referenceWidth = integer("referenceWidth"), referenceWidth >= 1,
              let referenceHeight = integer("referenceHeight"), referenceHeight >= 1,
              let buttonX = integer("buttonX"), buttonX >= 0, buttonX < referenceWidth,
              let buttonY = integer("buttonY"), buttonY >= 0, buttonY < referenceHeight,
              let baitCheckInterval = number("baitCheckInterval"), baitCheckInterval > 0,
              let baitMaxConsecutive = integer("baitMaxConsecutive"), (1...20).contains(baitMaxConsecutive),
              let baitUseInterval = number("baitUseInterval"), baitUseInterval > 0 else {
            let alert = NSAlert()
            alert.messageText = "设置值无效"
            alert.informativeText = "请填写有效的正数；按钮中心应位于参考画布范围内，非浮标抖动需小于间隔。"
            alert.runModal()
            return
        }

        let defaults = UserDefaults.standard
        defaults.set(buttonJitter, forKey: "settings.buttonJitter")
        defaults.set(bobberCooldown, forKey: "settings.bobberCooldown")
        defaults.set(bobberJitter, forKey: "settings.bobberJitter")
        defaults.set(bobberTimeOffsetMs, forKey: "settings.bobberTimeOffsetMs")
        defaults.set(noBobberInterval, forKey: "settings.noBobberInterval")
        defaults.set(noBobberJitter, forKey: "settings.noBobberJitter")
        defaults.set(referenceWidth, forKey: "settings.referenceWidth")
        defaults.set(referenceHeight, forKey: "settings.referenceHeight")
        defaults.set(buttonX, forKey: "settings.buttonX")
        defaults.set(buttonY, forKey: "settings.buttonY")
        defaults.set(baitCheckInterval, forKey: "settings.baitCheckInterval")
        defaults.set(baitMaxConsecutive, forKey: "settings.baitMaxConsecutive")
        defaults.set(baitUseInterval, forKey: "settings.baitUseInterval")
        defaults.set(waitBaitCooldownCheckbox.state == .on, forKey: "settings.waitBaitCooldown")
        cancelSettings()
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
        let settings = UserDefaults.standard
        var arguments = [
            script, "--start",
            "--no-bobber-interval", String(settings.double(forKey: "settings.noBobberInterval")),
            "--no-bobber-jitter", String(settings.double(forKey: "settings.noBobberJitter")),
            "--button", String(settings.integer(forKey: "settings.buttonX")), String(settings.integer(forKey: "settings.buttonY")),
            "--reference-size", String(settings.integer(forKey: "settings.referenceWidth")), String(settings.integer(forKey: "settings.referenceHeight")),
            "--button-jitter", String(settings.integer(forKey: "settings.buttonJitter")),
            "--hold-min-ms", "50", "--hold-max-ms", "120",
            "--bobber-cooldown", String(settings.double(forKey: "settings.bobberCooldown")),
            "--bobber-jitter", String(settings.double(forKey: "settings.bobberJitter")),
            "--bobber-time-offset-ms", String(settings.double(forKey: "settings.bobberTimeOffsetMs")),
            "--bobber-loss-grace", "0.25", "--input-latency", "0.045",
            "--max-size", "1920", "--max-fps", "60", "--seconds", "3600"
        ]
        if settings.bool(forKey: "settings.useBait") {
            arguments += [
                "--use-bait",
                "--bait-check-interval", String(settings.double(forKey: "settings.baitCheckInterval")),
                "--bait-max-consecutive", String(settings.integer(forKey: "settings.baitMaxConsecutive")),
                "--bait-use-interval", String(settings.double(forKey: "settings.baitUseInterval"))
            ]
            if settings.bool(forKey: "settings.waitBaitCooldown") {
                arguments.append("--wait-bait-cooldown")
            }
        }
        task.arguments = arguments
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
