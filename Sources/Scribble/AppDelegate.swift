import AppKit
import Combine

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let settings = AppSettings.shared
    private var recents: RecentsStore!
    private var controller: SessionController!
    private var statusMenu: StatusMenuController!
    private var cancellables: Set<AnyCancellable> = []
    private var pruneTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        Paths.ensure()
        recents = RecentsStore()
        controller = SessionController(recents: recents)
        statusMenu = StatusMenuController(controller: controller, recents: recents)
        statusMenu.onShowSettings = { [weak self] in self?.showSettings() }
        statusMenu.onShowOnboarding = { [weak self] in self?.showOnboarding() }
        controller.onPhaseChange = { [weak self] phase in self?.statusMenu.update(phase: phase) }
        controller.onNeedsPermissions = { [weak self] in self?.showOnboarding() }

        settings.$hotkeys
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in MainActor.assumeIsolated { self?.registerHotkeys() } }
            .store(in: &cancellables)
        settings.$model
            .dropFirst()
            .receive(on: DispatchQueue.main)
            .sink { model in MainActor.assumeIsolated { Transcriber.shared.preload(model.rawValue) } }
            .store(in: &cancellables)
        settings.$compute
            .dropFirst()
            .receive(on: DispatchQueue.main)
            .sink { _ in MainActor.assumeIsolated { Transcriber.shared.preload(AppSettings.shared.model.rawValue) } }
            .store(in: &cancellables)

        Transcriber.shared.preload(settings.model.rawValue)

        Paths.prune(olderThanDays: settings.retentionDays)
        let timer = Timer(timeInterval: 6 * 3600, repeats: true) { _ in
            MainActor.assumeIsolated { Paths.prune(olderThanDays: AppSettings.shared.retentionDays) }
        }
        RunLoop.main.add(timer, forMode: .common)
        pruneTimer = timer

        if !settings.onboardingShown || !Permissions.screenRecordingGranted {
            settings.onboardingShown = true
            showOnboarding()
        }
        Log.app.notice("Scribble launched, screen recording granted: \(Permissions.screenRecordingGranted), mic: \(Permissions.microphoneStatus.rawValue)")
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showSettings()
        return true
    }

    private func registerHotkeys() {
        HotkeyManager.shared.setRegistrations(HotkeyAction.allCases.map { action in
            (settings.hotkey(for: action), { [weak self] in self?.controller.handle(action) })
        })
    }

    private func showSettings() {
        WindowPresenter.shared.show(
            id: "settings", title: "Scribble Settings",
            view: SettingsView(settings: settings, transcriber: Transcriber.shared)
        )
    }

    private func showOnboarding() {
        WindowPresenter.shared.show(
            id: "onboarding", title: "Welcome to Scribble",
            view: OnboardingView(settings: settings, transcriber: Transcriber.shared)
        )
    }
}
