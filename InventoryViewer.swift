import Cocoa
import WebKit

class AppDelegate: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var htmlPath: String = ""

    func applicationDidFinishLaunching(_ notification: Notification) {
        if offerMoveToApplications() { return }
        showScanningWindow()
        DispatchQueue.global(qos: .userInitiated).async {
            self.ensureDependencies()
            self.runScan()
            DispatchQueue.main.async { self.loadReport() }
        }
    }

    func offerMoveToApplications() -> Bool {
        guard let bundlePath = Bundle.main.bundlePath as NSString? else { return false }
        let appName = Bundle.main.bundlePath.components(separatedBy: "/").last ?? "inventory.app"
        let dest = "/Applications/\(appName)"
        let current = bundlePath as String

        if current.hasPrefix("/Applications") { return false }
        if FileManager.default.fileExists(atPath: dest) { return false }

        let alert = NSAlert()
        alert.messageText = "Move to Applications?"
        alert.informativeText = "Would you like to move inventory to your Applications folder?"
        alert.addButton(withTitle: "Move to Applications")
        alert.addButton(withTitle: "Run from here")
        alert.alertStyle = .informational

        if alert.runModal() == .alertFirstButtonReturn {
            do {
                try FileManager.default.moveItem(atPath: current, toPath: dest)
                NSWorkspace.shared.open(URL(fileURLWithPath: dest))
                NSApp.terminate(nil)
                return true
            } catch {
                let errAlert = NSAlert()
                errAlert.messageText = "Couldn't move to Applications"
                errAlert.informativeText = "You can drag it manually. The app will continue from its current location."
                errAlert.runModal()
            }
        }
        return false
    }

    func showScanningWindow() {
        let rect = NSRect(x: 0, y: 0, width: 960, height: 820)
        window = NSWindow(contentRect: rect,
                          styleMask: [.titled, .closable, .resizable, .miniaturizable],
                          backing: .buffered, defer: false)
        window.title = "inventory"
        window.center()
        window.minSize = NSSize(width: 600, height: 400)

        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "nativeExport")
        webView = WKWebView(frame: window.contentView!.bounds, configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        window.contentView?.addSubview(webView)

        let loadingHTML = """
        <html><body style="background:#040d1a;color:#888;font-family:-apple-system,sans-serif;
        display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center"><p style="font-size:18px;color:#41b6e6">Scanning system…</p>
        <p style="font-size:13px;margin-top:8px">This may take a moment</p></div></body></html>
        """
        webView.loadHTMLString(loadingHTML, baseURL: nil)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func resourcePath() -> String {
        let bundle = Bundle.main.resourcePath ?? "."
        if FileManager.default.fileExists(atPath: bundle + "/inventory_mac.py") {
            return bundle
        }
        return (ProcessInfo.processInfo.arguments.first.flatMap {
            URL(fileURLWithPath: $0).deletingLastPathComponent().path
        }) ?? "."
    }

    func ensureDependencies() {
        func which(_ cmd: String) -> Bool {
            let paths = [
                "/opt/homebrew/bin/\(cmd)",
                "/usr/local/bin/\(cmd)",
                "/usr/bin/\(cmd)",
                "/bin/\(cmd)"
            ]
            return paths.contains { FileManager.default.isExecutableFile(atPath: $0) }
        }

        if !which("brew") {
            let install = showAlert(
                "Homebrew is not installed.",
                info: "It's needed to read your full App Store inventory (app names, versions, and IDs). Without it, the App Store section will be limited or missing.\n\nInstall Homebrew now?",
                buttons: ["Install", "Skip"]
            )
            if install == .alertFirstButtonReturn {
                runTerminalCommand(
                    "/bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\" && "
                    + "brew install mas && echo '\\n\\nDone. You can close this window.' && read -rp ''"
                )
                return
            }
        }

        if which("brew") && !which("mas") {
            let brewPath = shellOutput("/usr/bin/env which brew")
            _ = shellOutput("\(brewPath) install mas")
        }
    }

    func showAlert(_ message: String, info: String, buttons: [String]) -> NSApplication.ModalResponse {
        var result: NSApplication.ModalResponse = .alertSecondButtonReturn
        DispatchQueue.main.sync {
            let alert = NSAlert()
            alert.messageText = message
            alert.informativeText = info
            for b in buttons { alert.addButton(withTitle: b) }
            alert.alertStyle = .informational
            result = alert.runModal()
        }
        return result
    }

    func runTerminalCommand(_ cmd: String) {
        let script = "tell application \"Terminal\" to do script \"\(cmd.replacingOccurrences(of: "\"", with: "\\\""))\""
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        proc.arguments = ["-e", script]
        try? proc.run()
        proc.waitUntilExit()
    }

    func shellOutput(_ cmd: String) -> String {
        let p = Process(); let pipe = Pipe()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = ["-l", "-c", cmd]
        var env = ProcessInfo.processInfo.environment
        let brewPaths = "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin"
        env["PATH"] = brewPaths + ":" + (env["PATH"] ?? "/usr/bin:/bin")
        p.environment = env
        p.standardOutput = pipe; p.standardError = FileHandle.nullDevice
        try? p.run(); p.waitUntilExit()
        return String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    func runScan() {
        let script = resourcePath() + "/inventory_mac.py"
        let tmpDir = FileManager.default.temporaryDirectory
        htmlPath = tmpDir.appendingPathComponent("inventory_report.html").path
        let output = shellOutput("/usr/bin/env python3 \"\(script)\"")
        try? output.write(toFile: htmlPath, atomically: true, encoding: .utf8)
    }

    func loadReport() {
        let url = URL(fileURLWithPath: htmlPath)
        webView.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if navigationAction.navigationType == .linkActivated, let url = navigationAction.request.url {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
        } else {
            decisionHandler(.allow)
        }
    }

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any],
              let action = body["action"] as? String else { return }
        if action == "pdf" { exportPDF() }
        else if action == "html", let html = body["html"] as? String { exportHTML(html) }
    }

    func exportPDF() {
        webView.evaluateJavaScript(
            "document.querySelectorAll('details').forEach(function(d){d.open=true})"
        ) { [weak self] _, _ in
            guard let self = self else { return }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                let panel = NSSavePanel()
                panel.nameFieldStringValue = "software_inventory.pdf"
                panel.allowedContentTypes = [.pdf]
                panel.begin { response in
                    guard response == .OK, let url = panel.url else {
                        self.webView.evaluateJavaScript(
                            "document.querySelectorAll('details').forEach(function(d){d.open=false})"
                        )
                        return
                    }
                    let config = WKPDFConfiguration()
                    self.webView.createPDF(configuration: config) { result in
                        DispatchQueue.main.async {
                            if case .success(let data) = result {
                                try? data.write(to: url)
                                NSWorkspace.shared.open(url)
                            }
                            self.webView.evaluateJavaScript(
                                "document.querySelectorAll('details').forEach(function(d){d.open=false})"
                            )
                        }
                    }
                }
            }
        }
    }

    func exportHTML(_ html: String) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "software_inventory.html"
        panel.allowedContentTypes = [.html]
        panel.begin { response in
            guard response == .OK, let url = panel.url else { return }
            try? html.write(to: url, atomically: true, encoding: .utf8)
        }
    }
}

func buildMenuBar() {
    let mainMenu = NSMenu()

    let appItem = NSMenuItem()
    let appMenu = NSMenu()
    appMenu.addItem(NSMenuItem(title: "About inventory",
                               action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: ""))
    appMenu.addItem(.separator())
    appMenu.addItem(NSMenuItem(title: "Quit inventory",
                               action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
    appItem.submenu = appMenu
    mainMenu.addItem(appItem)

    let editItem = NSMenuItem()
    let editMenu = NSMenu(title: "Edit")
    editMenu.addItem(NSMenuItem(title: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
    editMenu.addItem(NSMenuItem(title: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"))
    editItem.submenu = editMenu
    mainMenu.addItem(editItem)

    let winItem = NSMenuItem()
    let winMenu = NSMenu(title: "Window")
    winMenu.addItem(NSMenuItem(title: "Minimize", action: #selector(NSWindow.miniaturize(_:)), keyEquivalent: "m"))
    winMenu.addItem(NSMenuItem(title: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w"))
    winItem.submenu = winMenu
    mainMenu.addItem(winItem)

    NSApplication.shared.mainMenu = mainMenu
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate
buildMenuBar()
app.run()
