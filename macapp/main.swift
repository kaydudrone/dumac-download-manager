// Dumac Download Manager.app: a native window around extension/manager.html.
// The page talks to host/dumac_host.py exactly like the browser extension does (length-prefixed JSON on stdin/stdout);
// shim.js stands in for chrome.runtime.sendNativeMessage and the clipboard. Menu bar items call window.kd.* in the page.
// Native extras: dumac:// links (Safari button, browser takeover), clipboard watcher popup, floating drop target.
import Cocoa
import WebKit

let support = (NSHomeDirectory() as NSString).appendingPathComponent("Library/Application Support/Dumac Download Manager") as NSString
let hostPath = support.appendingPathComponent("dumac-host")   // launcher written by install.sh
let settingsPath = support.appendingPathComponent("settings.json")

func isWebLink(_ s: String) -> Bool {
    guard !s.contains(where: { $0.isWhitespace }), let u = URL(string: s), let host = u.host, !host.isEmpty else { return false }
    return ["http", "https"].contains(u.scheme?.lowercased() ?? "")
}

/// Links worth offering from the clipboard: video pages and file downloads (not every web page you copy).
func looksDownloadable(_ s: String) -> Bool {
    guard isWebLink(s), let u = URL(string: s) else { return false }
    let host = (u.host ?? "").lowercased()
    let videoSites = ["youtube.com", "youtu.be", "instagram.com", "tiktok.com", "facebook.com", "fb.watch", "x.com",
                      "twitter.com", "vimeo.com", "dailymotion.com", "xiaohongshu.com", "xhslink.com", "bilibili.com"]
    if videoSites.contains(where: { host == $0 || host.hasSuffix("." + $0) }) { return true }
    let fileTypes: Set<String> = ["zip", "rar", "7z", "gz", "tar", "dmg", "pkg", "exe", "msi", "iso", "apk", "pdf", "mp4",
                                  "mov", "mkv", "webm", "mp3", "m4a", "wav", "flac", "docx", "xlsx", "pptx", "epub"]
    return fileTypes.contains(u.pathExtension.lowercased())
}

func setting(_ key: String, default value: Bool) -> Bool {
    guard let data = FileManager.default.contents(atPath: settingsPath),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return value }
    return obj[key] as? Bool ?? value
}

final class Bridge: NSObject, WKScriptMessageHandlerWithReply {
    func userContentController(_ ucc: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        guard let body = message.body as? [String: Any],
              let request = try? JSONSerialization.data(withJSONObject: body) else {
            replyHandler(#"{"error":"Bad request"}"#, nil)
            return
        }
        if body["type"] as? String == "__clipboard" {
            let text = NSPasteboard.general.string(forType: .string) ?? ""
            let out = (try? JSONSerialization.data(withJSONObject: ["text": text])) ?? Data("{}".utf8)
            replyHandler(String(decoding: out, as: UTF8.self), nil)
            return
        }
        DispatchQueue.global(qos: .userInitiated).async {
            let reply = Bridge.callHost(request)
            DispatchQueue.main.async { replyHandler(reply, nil) }
        }
    }

    static func callHost(_ request: Data) -> String {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: hostPath)
        let input = Pipe(), output = Pipe()
        p.standardInput = input
        p.standardOutput = output
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch {
            return #"{"error":"Helper not found. Run install.sh again."}"#
        }
        var len = UInt32(request.count).littleEndian
        input.fileHandleForWriting.write(withUnsafeBytes(of: &len) { Data($0) } + request)
        try? input.fileHandleForWriting.close()
        let out = output.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        guard out.count > 4 else { return #"{"error":"No reply from helper"}"# }
        return String(decoding: out.dropFirst(4), as: UTF8.self)
    }
}

/// Drop target: a small floating box; drop a link on it to download. Drag it to move, double-click opens the app.
final class DropView: NSView {
    var onDrop: ((String) -> Void)?
    var onOpen: (() -> Void)?
    var onHide: (() -> Void)?
    private var highlight = false { didSet { needsDisplay = true } }

    override init(frame: NSRect) {
        super.init(frame: frame)
        registerForDraggedTypes([.URL, .string])
        toolTip = "Drop a link here to download it"
    }
    required init?(coder: NSCoder) { nil }

    override func draw(_ dirtyRect: NSRect) {
        let path = NSBezierPath(roundedRect: bounds.insetBy(dx: 1.5, dy: 1.5), xRadius: 14, yRadius: 14)
        (highlight ? NSColor.systemBlue.withAlphaComponent(0.35) : NSColor.windowBackgroundColor.withAlphaComponent(0.94)).setFill()
        path.fill()
        NSColor.separatorColor.setStroke()
        path.stroke()
        NSApp.applicationIconImage?.draw(in: bounds.insetBy(dx: 10, dy: 10))
    }

    private func link(_ info: NSDraggingInfo) -> String? {
        let pb = info.draggingPasteboard
        if let url = (pb.readObjects(forClasses: [NSURL.self]) as? [URL])?.first, isWebLink(url.absoluteString) {
            return url.absoluteString
        }
        if let s = pb.string(forType: .string)?.trimmingCharacters(in: .whitespacesAndNewlines), isWebLink(s) { return s }
        return nil
    }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        guard link(sender) != nil else { return [] }
        highlight = true
        return .copy
    }
    override func draggingExited(_ sender: NSDraggingInfo?) { highlight = false }
    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        highlight = false
        guard let l = link(sender) else { return false }
        onDrop?(l)
        return true
    }
    override func mouseDown(with event: NSEvent) {
        if event.clickCount == 2 { onOpen?(); return }
        window?.performDrag(with: event)
    }
    override func menu(for event: NSEvent) -> NSMenu? {
        let m = NSMenu()
        m.addItem(withTitle: "Open Dumac Download Manager", action: #selector(openApp), keyEquivalent: "").target = self
        m.addItem(withTitle: "Hide drop target", action: #selector(hide), keyEquivalent: "").target = self
        return m
    }
    @objc func openApp() { onOpen?() }
    @objc func hide() { onHide?() }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!
    let bridge = Bridge()
    var pageReady = false
    var pendingJS: [String] = []      // calls that arrived before the page finished loading
    var clipChange = NSPasteboard.general.changeCount
    var lastOffered = ""
    var clipPanel: NSPanel?
    var clipTimer: Timer?
    var dropPanel: NSPanel?
    var dropItem: NSMenuItem?

    func applicationWillFinishLaunching(_ notification: Notification) {
        // dumac://add?url=… (Safari button) and dumac://status?id=… (download caught from the browser).
        NSAppleEventManager.shared().setEventHandler(self, andSelector: #selector(handleURL(_:with:)),
                                                     forEventClass: AEEventClass(kInternetEventClass),
                                                     andEventID: AEEventID(kAEGetURL))
    }

    @objc func handleURL(_ event: NSAppleEventDescriptor, with reply: NSAppleEventDescriptor) {
        guard let raw = event.paramDescriptor(forKeyword: keyDirectObject)?.stringValue,
              let comps = URLComponents(string: raw), comps.scheme == "dumac" else { return }
        let query = { (name: String) in comps.queryItems?.first(where: { $0.name == name })?.value }
        switch comps.host {
        case "add":
            // Never download straight away: open Add URL pre-filled so the user confirms (a web page can't start downloads).
            guard let link = query("url"), isWebLink(link) else { return }
            showMain()
            runJS("add", link)
        case "status":
            guard let id = query("id"), !id.isEmpty, id.allSatisfy(\.isNumber) else { return }
            showMain()
            runJS("openStatus", id)
        default:
            return
        }
    }

    /// Calls window.kd.<name>(arg) in the page, waiting for the page to load if needed.
    func runJS(_ name: String, _ arg: String? = nil) {
        let argJSON = arg.flatMap { try? JSONSerialization.data(withJSONObject: [$0]) }
            .map { String(decoding: $0, as: UTF8.self) + "[0]" } ?? ""
        pendingJS.append("window.kd && window.kd.\(name)(\(argJSON))")
        flushJS()
    }

    func flushJS() {
        guard pageReady else { return }
        let calls = pendingJS
        pendingJS.removeAll()
        calls.forEach { web.evaluateJavaScript($0, completionHandler: nil) }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        pageReady = true
        flushJS()
    }

    func showMain() {
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let ucc = WKUserContentController()
        if let url = Bundle.main.url(forResource: "shim", withExtension: "js"),
           let shim = try? String(contentsOf: url, encoding: .utf8) {
            ucc.addUserScript(WKUserScript(source: shim, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        }
        ucc.addScriptMessageHandler(bridge, contentWorld: .page, name: "host")
        let config = WKWebViewConfiguration()
        config.userContentController = ucc
        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1100, height: 600),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Dumac Download Manager"
        window.minSize = NSSize(width: 820, height: 380)
        window.contentView = web
        window.center()
        window.setFrameAutosaveName("MainWindow")

        let dir = Bundle.main.resourceURL!.appendingPathComponent("web")
        web.loadFileURL(dir.appendingPathComponent("manager.html"), allowingReadAccessTo: dir)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        buildMenu()

        clipTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.checkClipboard() }
        if UserDefaults.standard.bool(forKey: "dropTargetVisible") { setDropTarget(visible: true) }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    // ---- clipboard watcher ----------------------------------------------------

    func checkClipboard() {
        let pb = NSPasteboard.general
        guard pb.changeCount != clipChange else { return }
        clipChange = pb.changeCount
        // Copies made inside the app itself (e.g. in Add URL) aren't offered back.
        guard !NSApp.isActive, setting("watch_clipboard", default: true),
              let text = pb.string(forType: .string)?.trimmingCharacters(in: .whitespacesAndNewlines),
              text != lastOffered, looksDownloadable(text) else { return }
        lastOffered = text
        offer(text)
    }

    func offer(_ link: String) {
        clipPanel?.close()
        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 380, height: 104),
                            styleMask: [.titled, .closable, .nonactivatingPanel, .utilityWindow],
                            backing: .buffered, defer: false)
        panel.title = "Dumac Download Manager"
        panel.level = .floating
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false

        let title = NSTextField(labelWithString: "Download this link?")
        title.font = .boldSystemFont(ofSize: 13)
        let url = NSTextField(labelWithString: link)
        url.lineBreakMode = .byTruncatingMiddle
        url.textColor = .secondaryLabelColor
        let later = NSButton(title: "Not now", target: self, action: #selector(dismissOffer))
        let go = NSButton(title: "Download", target: self, action: #selector(acceptOffer(_:)))
        go.keyEquivalent = "\r"
        go.identifier = NSUserInterfaceItemIdentifier(link)
        let buttons = NSStackView(views: [NSView(), later, go])
        let stack = NSStackView(views: [title, url, buttons])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 6
        stack.edgeInsets = NSEdgeInsets(top: 10, left: 14, bottom: 10, right: 14)
        buttons.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -28).isActive = true
        url.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -28).isActive = true
        panel.contentView = stack

        if let screen = NSScreen.main?.visibleFrame {
            panel.setFrameTopLeftPoint(NSPoint(x: screen.maxX - 396, y: screen.maxY - 12))
        }
        panel.orderFrontRegardless()
        clipPanel = panel
        DispatchQueue.main.asyncAfter(deadline: .now() + 12) { [weak self, weak panel] in
            if let p = panel, p === self?.clipPanel { p.close() }
        }
    }

    @objc func acceptOffer(_ sender: NSButton) {
        let link = sender.identifier?.rawValue ?? ""
        clipPanel?.close()
        guard isWebLink(link) else { return }
        showMain()
        runJS("add", link)
    }

    @objc func dismissOffer() { clipPanel?.close() }

    // ---- drop target ----------------------------------------------------------

    func setDropTarget(visible: Bool) {
        UserDefaults.standard.set(visible, forKey: "dropTargetVisible")
        dropItem?.state = visible ? .on : .off
        if !visible { dropPanel?.orderOut(nil); return }
        if dropPanel == nil {
            let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 72, height: 72),
                                styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
            panel.level = .floating
            panel.isFloatingPanel = true
            panel.hidesOnDeactivate = false
            panel.backgroundColor = .clear
            panel.isOpaque = false
            panel.hasShadow = true
            panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
            let view = DropView(frame: NSRect(x: 0, y: 0, width: 72, height: 72))
            view.onDrop = { [weak self] link in self?.showMain(); self?.runJS("add", link) }
            view.onOpen = { [weak self] in self?.showMain() }
            view.onHide = { [weak self] in self?.setDropTarget(visible: false) }
            panel.contentView = view
            if !panel.setFrameUsingName("DropTarget"), let screen = NSScreen.main?.visibleFrame {
                panel.setFrameOrigin(NSPoint(x: screen.maxX - 96, y: screen.midY))
            }
            panel.setFrameAutosaveName("DropTarget")
            dropPanel = panel
        }
        dropPanel?.orderFrontRegardless()
    }

    @objc func toggleDropTarget(_ sender: NSMenuItem) {
        setDropTarget(visible: !(dropPanel?.isVisible ?? false))
    }

    // ---- menu bar --------------------------------------------------------------

    /// Menu bar → the page's window.kd API (same actions as the toolbar).
    @objc func runAction(_ sender: NSMenuItem) {
        guard let name = sender.representedObject as? String else { return }
        runJS(name)
    }

    func item(_ title: String, _ action: String, _ key: String = "", _ mods: NSEvent.ModifierFlags = .command) -> NSMenuItem {
        let it = NSMenuItem(title: title, action: #selector(runAction(_:)), keyEquivalent: key)
        it.keyEquivalentModifierMask = mods
        it.representedObject = action
        it.target = self
        return it
    }

    func menu(_ title: String, _ items: [NSMenuItem], in main: NSMenu) -> NSMenu {
        let top = NSMenuItem()
        let m = NSMenu(title: title)
        items.forEach { m.addItem($0) }
        top.submenu = m
        main.addItem(top)
        return m
    }

    func buildMenu() {
        let main = NSMenu()
        _ = menu("Dumac Download Manager", [
            NSMenuItem(title: "About Dumac Download Manager",
                       action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: ""),
            .separator(),
            NSMenuItem(title: "Hide Dumac Download Manager", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h"),
            NSMenuItem(title: "Quit Dumac Download Manager", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"),
        ], in: main)
        _ = menu("Tasks", [
            item("Add new download…", "add", "n"),
            .separator(),
            item("Start queue", "startQueue"),
            item("Stop queue", "stopQueue"),
            item("Scheduler…", "scheduler"),
        ], in: main)
        _ = menu("File", [
            item("Open", "open", "o"),
            item("Open folder", "reveal", "r", [.command, .shift]),
            item("Properties", "properties", "i"),
            .separator(),
            item("Open downloads folder", "folder"),
        ], in: main)
        // Without an Edit menu, Cmd+C / Cmd+V do nothing in the Add URL box.
        _ = menu("Edit", [
            NSMenuItem(title: "Undo", action: Selector(("undo:")), keyEquivalent: "z"),
            NSMenuItem(title: "Redo", action: Selector(("redo:")), keyEquivalent: "Z"),
            .separator(),
            NSMenuItem(title: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x"),
            NSMenuItem(title: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c"),
            NSMenuItem(title: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v"),
            NSMenuItem(title: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"),
        ], in: main)
        _ = menu("Downloads", [
            item("Resume", "resume"),
            item("Stop", "stop"),
            item("Stop all", "stopAll"),
            .separator(),
            item("Delete", "del"),
            item("Delete completed", "delCompleted"),
            .separator(),
            item("Options…", "options", ","),
        ], in: main)
        let drop = NSMenuItem(title: "Show drop target", action: #selector(toggleDropTarget(_:)), keyEquivalent: "")
        drop.target = self
        drop.state = UserDefaults.standard.bool(forKey: "dropTargetVisible") ? .on : .off
        dropItem = drop
        _ = menu("View", [item("Show / hide categories", "toggleCats"), drop], in: main)
        let win = menu("Window", [
            NSMenuItem(title: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m"),
            NSMenuItem(title: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w"),
        ], in: main)
        let help = menu("Help", [item("How to use Dumac Download Manager", "help")], in: main)
        NSApp.mainMenu = main
        NSApp.windowsMenu = win
        NSApp.helpMenu = help
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
