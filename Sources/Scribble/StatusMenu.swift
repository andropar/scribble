import AppKit
import Combine
import ScribbleCore

final class ClosureMenuItem: NSMenuItem {
    private let handler: () -> Void

    init(_ title: String, key: String = "", modifiers: NSEvent.ModifierFlags = [], handler: @escaping () -> Void) {
        self.handler = handler
        super.init(title: title, action: #selector(fire), keyEquivalent: key)
        keyEquivalentModifierMask = modifiers
        target = self
    }

    @available(*, unavailable)
    required init(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    @objc private func fire() { handler() }
}

@MainActor
final class StatusMenuController: NSObject, NSMenuDelegate {
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    private let controller: SessionController
    private let recents: RecentsStore
    private let settings = AppSettings.shared
    var onShowSettings: () -> Void = {}
    var onShowOnboarding: () -> Void = {}
    private var cancellables: Set<AnyCancellable> = []

    init(controller: SessionController, recents: RecentsStore) {
        self.controller = controller
        self.recents = recents
        super.init()
        let menu = NSMenu()
        menu.delegate = self
        item.menu = menu
        update(phase: .idle)
        Transcriber.shared.$status
            .receive(on: DispatchQueue.main)
            .sink { [weak self] status in
                MainActor.assumeIsolated { self?.updateTooltip(status) }
            }
            .store(in: &cancellables)
    }

    func update(phase: SessionController.Phase) {
        guard let button = item.button else { return }
        let symbol: String
        switch phase {
        case .idle: symbol = "viewfinder"
        case .snapshot, .snapshotNavigating: symbol = "camera.viewfinder"
        case .walkthrough: symbol = "record.circle"
        case .dictation: symbol = "mic.circle"
        case .processing: symbol = "hourglass"
        }
        let image = NSImage(systemSymbolName: symbol, accessibilityDescription: "Scribble")
        image?.isTemplate = true
        button.image = image
        button.contentTintColor = phase == .walkthrough || phase == .dictation ? .systemRed : nil
    }

    private func updateTooltip(_ status: Transcriber.Status) {
        item.button?.toolTip = "Scribble — \(status.title)"
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        let phase = controller.phase

        if phase.isCapturing {
            menu.addItem(ClosureMenuItem("Finish capture  \(settings.hotkey(for: .finish).display)") { [weak self] in self?.controller.finish() })
            menu.addItem(ClosureMenuItem("Cancel capture") { [weak self] in self?.controller.cancel() })
        } else {
            let snap = ClosureMenuItem("Snapshot  \(settings.hotkey(for: .snapshot).display)") { [weak self] in
                self?.controller.startSnapshot()
            }
            let walk = ClosureMenuItem("Walkthrough  \(settings.hotkey(for: .walkthrough).display)") { [weak self] in
                self?.controller.startWalkthrough()
            }
            let dictate = ClosureMenuItem("Dictation  \(settings.hotkey(for: .dictation).display)") { [weak self] in
                self?.controller.startDictation()
            }
            snap.isEnabled = phase == .idle
            walk.isEnabled = phase == .idle
            dictate.isEnabled = phase == .idle
            menu.addItem(snap)
            menu.addItem(walk)
            menu.addItem(dictate)
        }

        menu.addItem(.separator())
        let status = NSMenuItem(title: Transcriber.shared.status.title, action: nil, keyEquivalent: "")
        status.isEnabled = false
        menu.addItem(status)
        if case .failed = Transcriber.shared.status {
            menu.addItem(ClosureMenuItem("Retry model download") { [weak self] in
                guard let self else { return }
                Transcriber.shared.preload(self.settings.model.rawValue)
            })
        }
        if !Permissions.screenRecordingGranted {
            menu.addItem(ClosureMenuItem("⚠️ Screen Recording permission needed…") { [weak self] in self?.onShowOnboarding() })
        }

        menu.addItem(.separator())
        recents.pruneMissing()
        let recentItem = NSMenuItem(title: "Recent captures", action: nil, keyEquivalent: "")
        let recentMenu = NSMenu()
        if recents.items.isEmpty {
            let empty = NSMenuItem(title: "No captures yet", action: nil, keyEquivalent: "")
            empty.isEnabled = false
            recentMenu.addItem(empty)
        }
        let f = DateFormatter()
        f.dateFormat = "MMM d, HH:mm"
        for capture in recents.items {
            let entry = NSMenuItem(title: "\(capture.mode.title) · \(f.string(from: capture.date)) · \(capture.summary)", action: nil, keyEquivalent: "")
            let sub = NSMenu()
            for format in ClipboardFormat.allCases {
                sub.addItem(ClosureMenuItem("Copy: \(format.title)") {
                    do {
                        try ClipboardWriter.write(capture, format: format)
                        Toast.shared.show("Copied to clipboard", detail: format.title)
                    } catch {
                        Toast.shared.show("Copy failed", detail: error.localizedDescription, success: false, duration: 4)
                    }
                })
            }
            sub.addItem(.separator())
            sub.addItem(ClosureMenuItem("Open capture.md") { NSWorkspace.shared.open(capture.markdownURL) })
            sub.addItem(ClosureMenuItem("Reveal in Finder") { NSWorkspace.shared.activateFileViewerSelecting([capture.markdownURL]) })
            entry.submenu = sub
            recentMenu.addItem(entry)
        }
        recentItem.submenu = recentMenu
        menu.addItem(recentItem)
        menu.addItem(ClosureMenuItem("Open captures folder") { NSWorkspace.shared.open(Paths.captures) })

        menu.addItem(.separator())
        menu.addItem(ClosureMenuItem("Settings…", key: ",", modifiers: .command) { [weak self] in self?.onShowSettings() })
        menu.addItem(ClosureMenuItem("Permissions & help…") { [weak self] in self?.onShowOnboarding() })
        menu.addItem(.separator())
        menu.addItem(ClosureMenuItem("Quit Scribble", key: "q", modifiers: .command) { NSApp.terminate(nil) })
    }
}
