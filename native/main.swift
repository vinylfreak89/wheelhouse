// Wheelhouse — a native macOS shell around the Codex app-server.
//
//   NSWindow + WKWebView  ->  bridge.py (127.0.0.1)  ->  codex app-server (stdio)
//
// The official desktop app cannot show threads created on a remote host and
// offers no way to start one (openai/codex #27284, #22438, #24280). The
// protocol is documented and fine, so we drive it ourselves.
//
// NOTE: an NSApplication with no mainMenu gets NO standard shortcuts at all --
// no Cmd-Q, no Cmd-C/V inside the web view. The menu below is load-bearing.

import AppKit
import WebKit

let PORT = 8770
// Resolve bridge.py without assuming where this checkout lives: an explicit
// override first, then the directory holding the .app (the usual layout when
// built in-tree), then the historical default.
let BRIDGE: String = {
    let fm = FileManager.default
    var tries: [String] = []
    if let env = ProcessInfo.processInfo.environment["CODEX_APP_DIR"] {
        tries.append("\(env)/bridge.py")
    }
    let appDir = Bundle.main.bundleURL.deletingLastPathComponent().path
    tries.append("\(appDir)/bridge.py")
    return tries.first(where: { fm.fileExists(atPath: $0) }) ?? tries.last!
}()

final class Delegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate,
                      WKScriptMessageHandler {
    var window: NSWindow!
    var web: WKWebView!
    var bridge: Process?

    // MARK: menu
    func buildMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Wheelhouse", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide Wheelhouse", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let hideOthers = appMenu.addItem(withTitle: "Hide Others", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(withTitle: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Wheelhouse", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let fileItem = NSMenuItem(); main.addItem(fileItem)
        let fileMenu = NSMenu(title: "File")
        fileMenu.addItem(withTitle: "New Thread", action: #selector(newThread), keyEquivalent: "n")
        fileMenu.addItem(withTitle: "Reload", action: #selector(reload), keyEquivalent: "r")
        fileMenu.addItem(.separator())
        fileMenu.addItem(withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        fileItem.submenu = fileMenu

        // Edit menu is REQUIRED for copy/paste/select-all to work in WKWebView.
        let editItem = NSMenuItem(); main.addItem(editItem)
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "Find…", action: #selector(showFind), keyEquivalent: "f")
        editMenu.addItem(withTitle: "Find Next", action: #selector(findNext), keyEquivalent: "g")
        let findPreviousItem = editMenu.addItem(withTitle: "Find Previous", action: #selector(findPrevious), keyEquivalent: "g")
        findPreviousItem.keyEquivalentModifierMask = [.command, .shift]
        editItem.submenu = editMenu

        let viewItem = NSMenuItem(); main.addItem(viewItem)
        let viewMenu = NSMenu(title: "View")
        let fullScreen = viewMenu.addItem(withTitle: "Enter Full Screen", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        fullScreen.keyEquivalentModifierMask = [.command, .control]
        viewItem.submenu = viewMenu

        let winItem = NSMenuItem(); main.addItem(winItem)
        let winMenu = NSMenu(title: "Window")
        winMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        winMenu.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        winItem.submenu = winMenu
        NSApp.windowsMenu = winMenu

        NSApp.mainMenu = main
    }

    @objc func newThread() { web.evaluateJavaScript("window.openNewThread && window.openNewThread()") }
    @objc func reload() {
        // Persist the per-thread composer draft before replacing the page.
        // callAsyncJavaScript awaits the page's Promise; evaluateJavaScript
        // would return as soon as it saw the Promise object and still race it.
        web.callAsyncJavaScript(
            "return await (window.prepareReload ? window.prepareReload() : null);",
            arguments: [:], in: nil, in: .page
        ) { [weak self] _ in
            DispatchQueue.main.async { self?.web.reload() }
        }
    }
    @objc func showFind()  { web.evaluateJavaScript("window.openFind && window.openFind()") }
    @objc func findNext()  { web.evaluateJavaScript("window.findNext && window.findNext(false)") }
    @objc func findPrevious() { web.evaluateJavaScript("window.findNext && window.findNext(true)") }

    // MARK: lifecycle
    func applicationDidFinishLaunching(_ n: Notification) {
        buildMenu()
        let rect = NSRect(x: 0, y: 0, width: 1180, height: 800)
        window = NSWindow(contentRect: rect,
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Wheelhouse"
        window.backgroundColor = NSColor(red: 0.086, green: 0.086, blue: 0.102, alpha: 1)
        window.center()
        window.setFrameAutosaveName("WheelhouseMain")
        window.minSize = NSSize(width: 820, height: 520)

        let cfg = WKWebViewConfiguration()
        cfg.defaultWebpagePreferences.allowsContentJavaScript = true
        cfg.userContentController.add(self, name: "find")
        web = WKWebView(frame: rect, configuration: cfg)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        web.autoresizingMask = [.width, .height]
        window.contentView = web
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        startBridge()
        waitThenLoad(0)
    }

    func userContentController(_ controller: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard message.name == "find",
              let payload = message.body as? [String: Any],
              let query = payload["query"] as? String else { return }
        if query.isEmpty {
            web.evaluateJavaScript("window.getSelection().removeAllRanges()")
            return
        }
        let configuration = WKFindConfiguration()
        configuration.backwards = payload["backwards"] as? Bool ?? false
        configuration.caseSensitive = false
        configuration.wraps = true
        web.find(query, configuration: configuration) { [weak self] result in
            let found = result.matchFound ? "true" : "false"
            self?.web.evaluateJavaScript(
                "window.updateFindResult && window.updateFindResult(\(found))")
        }
    }

    func startBridge() {
        if probe() { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = ["python3", BRIDGE]
        p.environment = ProcessInfo.processInfo.environment
            .merging(["CODEX_BRIDGE_PORT": String(PORT)]) { _, n in n }
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        do { try p.run(); bridge = p } catch { fail("Could not start bridge: \(error)") }
    }

    func probe() -> Bool {
        let sem = DispatchSemaphore(value: 0); var ok = false
        var r = URLRequest(url: URL(string: "http://127.0.0.1:\(PORT)/status")!)
        r.timeoutInterval = 1
        URLSession.shared.dataTask(with: r) { d,_,_ in ok = (d?.isEmpty == false); sem.signal() }.resume()
        _ = sem.wait(timeout: .now() + 1.5)
        return ok
    }

    func waitThenLoad(_ n: Int) {
        if probe() { web.load(URLRequest(url: URL(string: "http://127.0.0.1:\(PORT)/")!)); return }
        if n > 60 { fail("Bridge did not start on port \(PORT).\n\nDebug:\n  python3 \(BRIDGE)"); return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.waitThenLoad(n + 1) }
    }

    func fail(_ m: String) {
        web.loadHTMLString("""
        <meta charset=utf-8><body style="background:#16161a;color:#e6e6ea;
        font:14px -apple-system;padding:40px;line-height:1.6">
        <h2 style="color:#f7768e">Wheelhouse could not start</h2><pre>\(m)</pre></body>
        """, baseURL: nil)
    }

    func webView(_ w: WKWebView, didFail nav: WKNavigation!, withError e: Error) {
        fail("Load failed: \(e.localizedDescription)")
    }

    // WKWebView ignores JS dialogs unless the host implements these. The UI uses
    // in-page modals, but a stray confirm() would otherwise hang silently.
    func webView(_ w: WKWebView, runJavaScriptAlertPanelWithMessage m: String,
                 initiatedByFrame f: WKFrameInfo, completionHandler done: @escaping () -> Void) {
        let a = NSAlert(); a.messageText = m; a.runModal(); done()
    }
    func webView(_ w: WKWebView, runJavaScriptConfirmPanelWithMessage m: String,
                 initiatedByFrame f: WKFrameInfo, completionHandler done: @escaping (Bool) -> Void) {
        let a = NSAlert(); a.messageText = m
        a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancel")
        done(a.runModal() == .alertFirstButtonReturn)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ n: Notification) { bridge?.terminate() }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let d = Delegate()
app.delegate = d
app.run()
