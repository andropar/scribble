import AVFoundation
import AppKit
import SwiftUI
import VoiceAnnotateCore

@MainActor
final class WindowPresenter {
    static let shared = WindowPresenter()
    private var windows: [String: NSWindow] = [:]

    func show<V: View>(id: String, title: String, view: V) {
        NSApp.activate(ignoringOtherApps: true)
        if let window = windows[id] {
            window.makeKeyAndOrderFront(nil)
            return
        }
        let host = NSHostingController(rootView: view)
        let window = NSWindow(contentViewController: host)
        window.title = title
        window.styleMask = [.titled, .closable, .miniaturizable]
        window.isReleasedWhenClosed = false
        window.center()
        windows[id] = window
        window.makeKeyAndOrderFront(nil)
    }
}

// MARK: Settings

struct SettingsView: View {
    @ObservedObject var settings: AppSettings
    @ObservedObject var transcriber: Transcriber

    var body: some View {
        TabView {
            general.tabItem { Label("General", systemImage: "gearshape") }
            capture.tabItem { Label("Capture", systemImage: "viewfinder") }
            transcription.tabItem { Label("Transcription", systemImage: "waveform") }
            shortcuts.tabItem { Label("Shortcuts", systemImage: "keyboard") }
        }
        .frame(width: 560, height: 540)
    }

    /// Value text plus a bare stepper, so the row label stays short and aligned.
    private func stepper(_ title: String, value: Binding<Int>, in range: ClosedRange<Int>, format: @escaping (Int) -> String) -> some View {
        LabeledContent(title) {
            HStack(spacing: 6) {
                Text(format(value.wrappedValue)).monospacedDigit()
                Stepper(title, value: value, in: range).labelsHidden()
            }
        }
    }

    private var general: some View {
        Form {
            Section {
                Picker("Clipboard format", selection: $settings.clipboardFormat) {
                    ForEach(ClipboardFormat.allCases) { Text($0.title).tag($0) }
                }
            } footer: {
                Text("Every format is also available later from the menu bar → Recent captures.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section {
                stepper("Keep captures for", value: $settings.retentionDays, in: 1...365) { "\($0) days" }
                LabeledContent("Stored in") {
                    Button("~/.voice-annotate/captures") { NSWorkspace.shared.open(Paths.captures) }
                        .buttonStyle(.link)
                }
            }
            Section {
                Toggle("Play sounds", isOn: $settings.playSounds)
                Toggle("Launch at login", isOn: Binding(get: { settings.launchAtLogin }, set: { settings.launchAtLogin = $0 }))
            }
        }
        .formStyle(.grouped)
    }

    private var capture: some View {
        Form {
            Section("Images") {
                Picker("Image size (long edge)", selection: $settings.imageLongEdge) {
                    Text("1092 px (smallest)").tag(1092)
                    Text("1568 px (recommended for Claude)").tag(1568)
                    Text("2048 px").tag(2048)
                    Text("2560 px").tag(2560)
                }
                Toggle(isOn: $settings.allDisplays) {
                    Text("Capture all displays")
                    Text("Draw on any display; walkthroughs follow the cursor.")
                }
                Toggle("Add zoomed detail crops on large displays", isOn: $settings.detailCrops)
                Toggle("Also keep un-annotated screenshots", isOn: $settings.keepOriginals)
            }
            Section("Walkthrough") {
                stepper("Max keyframes", value: $settings.maxFrames, in: 4...80) { "\($0)" }
                stepper("Max length", value: $settings.maxWalkthroughMinutes, in: 1...60) { "\($0) min" }
                Picker("Draw mode", selection: $settings.drawModifier) {
                    ForEach(DrawModifier.allCases) { Text($0.title).tag($0) }
                }
                LabeledContent("Annotations fade after") {
                    HStack {
                        Slider(value: $settings.fadeSeconds, in: 1...10, step: 0.5).frame(width: 160)
                        Text(String(format: "%.1f s", settings.fadeSeconds)).monospacedDigit().frame(width: 40, alignment: .trailing)
                    }
                }
            }
        }
        .formStyle(.grouped)
    }

    private var transcription: some View {
        Form {
            Section {
                Picker("Language", selection: $settings.language) {
                    Text("English").tag(TranscriptionLanguage.english)
                    Text("Auto-detect").tag(TranscriptionLanguage.auto)
                    Divider()
                    ForEach(WhisperLanguage.all.filter { $0.code != "en" }) {
                        Text($0.menuTitle).tag(TranscriptionLanguage.language($0))
                    }
                }
                Picker("Whisper model", selection: $settings.model) {
                    ForEach(WhisperModel.allCases) { Text($0.title).tag($0) }
                }
                if let warning = LanguagePlan.resolve(selection: settings.language, model: settings.model).warning {
                    VStack(alignment: .leading, spacing: 6) {
                        Label(warning, systemImage: "exclamationmark.triangle.fill")
                            .font(.callout)
                            .foregroundStyle(.orange)
                        Button("Use Small (multilingual)") { settings.model = .small }
                    }
                }
                Picker("Run on", selection: $settings.compute) {
                    ForEach(WhisperCompute.allCases) { Text($0.title).tag($0) }
                }
                LabeledContent("Status") {
                    HStack(spacing: 6) {
                        if case .downloading = transcriber.status { ProgressView().controlSize(.small) }
                        if transcriber.status == .loading || transcriber.status == .ready(optimizing: true) { ProgressView().controlSize(.small) }
                        Text(transcriber.status.title)
                    }
                }
            } footer: {
                Text("Runs fully on-device with WhisperKit. Models live in ~/.voice-annotate/models. Smaller models are noticeably less accurate outside English.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section {
                TextEditor(text: $settings.vocabulary)
                    .font(.system(size: 12))
                    .frame(height: 60)
            } header: {
                Text("Custom vocabulary")
            } footer: {
                Text("Names, jargon and identifiers you say often (e.g. \"SwiftUI, PyTorch, SLURM, WhisperKit\"). Passed to Whisper as a prompt. When you speak another language, list the English labels you mention (e.g. \"Save, Settings\") so they keep their spelling.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }

    private var shortcuts: some View {
        Form {
            Section {
                ForEach(HotkeyAction.allCases, id: \.self) { action in
                    LabeledContent(action.title) {
                        HotkeyRecorder(action: action, settings: settings)
                    }
                }
            } footer: {
                HStack {
                    Spacer()
                    Button("Reset to defaults") { settings.resetHotkeys() }
                }
            }
            Section("Snapshot overlay keys") {
                Text("P pen · A arrow · R box · O ellipse · H highlighter · N pin · 1–6 colors · ⌘Z undo · ⇥ next shot · ↩ finish · ⎋ cancel. Pen strokes stay as drawn and get no number.")
                    .font(.callout).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }
}

struct HotkeyRecorder: View {
    let action: HotkeyAction
    @ObservedObject var settings: AppSettings
    @State private var recording = false
    @State private var monitor: Any?

    var body: some View {
        Button {
            recording ? stop() : start()
        } label: {
            Text(recording ? "Press shortcut…" : settings.hotkey(for: action).display).frame(minWidth: 110)
        }
        .onDisappear { stop() }
    }

    private func start() {
        recording = true
        HotkeyManager.shared.suspend()
        monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            if event.keyCode == 53 {
                stop()
                return nil
            }
            let mods = event.modifierFlags.intersection([.command, .option, .control, .shift])
            guard !mods.isEmpty, mods != .shift else {
                NSSound.beep()
                return nil
            }
            let hotkey = Hotkey(
                keyCode: UInt32(event.keyCode),
                carbonModifiers: Hotkey.carbonModifiers(from: mods),
                keyLabel: Hotkey.label(for: event.keyCode, characters: event.charactersIgnoringModifiers)
            )
            settings.setHotkey(hotkey, for: action)
            stop()
            return nil
        }
    }

    private func stop() {
        if let monitor { NSEvent.removeMonitor(monitor) }
        monitor = nil
        if recording {
            recording = false
            HotkeyManager.shared.resume()
        }
    }
}

// MARK: Onboarding / permissions

struct OnboardingView: View {
    @ObservedObject var settings: AppSettings
    @ObservedObject var transcriber: Transcriber
    @State private var screenGranted = Permissions.screenRecordingGranted
    @State private var micStatus = Permissions.microphoneStatus
    @State private var requestedScreen = false
    private let timer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 12) {
                Image(systemName: "viewfinder")
                    .font(.system(size: 34, weight: .medium))
                    .foregroundStyle(Color.accentColor)
                VStack(alignment: .leading) {
                    Text("voice-annotate").font(.title2.bold())
                    Text("Talk, point and draw on your screen. Paste the result into any agent.")
                        .foregroundStyle(.secondary)
                }
            }

            GroupBox("Permissions") {
                VStack(alignment: .leading, spacing: 10) {
                    permissionRow(
                        "Screen Recording", granted: screenGranted,
                        detail: requestedScreen && !screenGranted ? "After enabling it in System Settings, relaunch the app." : "Needed to take screenshots and record."
                    ) {
                        requestedScreen = true
                        Permissions.requestScreenRecording()
                    }
                    permissionRow(
                        "Microphone", granted: micStatus == .authorized,
                        detail: "Needed for the voice transcript."
                    ) {
                        Permissions.requestMicrophone { _ in micStatus = Permissions.microphoneStatus }
                    }
                    if requestedScreen && !screenGranted {
                        Button("Relaunch voice-annotate") { Permissions.relaunch() }
                    }
                }
                .padding(6)
            }

            GroupBox("Transcription") {
                HStack {
                    Text(transcriber.status.title)
                    Spacer()
                    if case .downloading = transcriber.status { ProgressView().controlSize(.small) }
                    if transcriber.status == .loading || transcriber.status == .ready(optimizing: true) { ProgressView().controlSize(.small) }
                }
                .padding(6)
            }

            GroupBox("Shortcuts") {
                VStack(alignment: .leading, spacing: 6) {
                    shortcutRow(settings.hotkey(for: .snapshot).display, "Snapshot: freeze the screen, draw and talk. Press again (or ↩) to finish, ⇥ for another shot.")
                    shortcutRow(settings.hotkey(for: .walkthrough).display, "Walkthrough: record while you use the computer. \(settings.drawModifier == .off ? "Toggle draw mode with \(settings.hotkey(for: .toggleDraw).display)." : "\(settings.drawModifier.title) to draw.") Press again to finish.")
                    shortcutRow(settings.hotkey(for: .finish).display, "Finish any capture and copy it to the clipboard.")
                }
                .padding(6)
            }
        }
        .padding(22)
        .frame(width: 560)
        .onReceive(timer) { _ in
            screenGranted = Permissions.screenRecordingGranted
            micStatus = Permissions.microphoneStatus
        }
    }

    private func permissionRow(_ title: String, granted: Bool, detail: String, action: @escaping () -> Void) -> some View {
        HStack {
            Image(systemName: granted ? "checkmark.circle.fill" : "exclamationmark.circle")
                .foregroundStyle(granted ? Color.green : Color.orange)
            VStack(alignment: .leading) {
                Text(title).font(.headline)
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if !granted { Button("Grant…", action: action) }
        }
    }

    private func shortcutRow(_ key: String, _ text: String) -> some View {
        HStack(alignment: .top) {
            Text(key).font(.system(.body, design: .monospaced)).frame(width: 70, alignment: .leading)
            Text(text).fixedSize(horizontal: false, vertical: true)
        }
    }
}
