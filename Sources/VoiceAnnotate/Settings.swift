import AppKit
import Carbon.HIToolbox
import Combine
import CoreGraphics
import Foundation
import ServiceManagement
import VoiceAnnotateCore

struct Hotkey: Codable, Equatable {
    var keyCode: UInt32
    var carbonModifiers: UInt32
    var keyLabel: String

    var display: String {
        var s = ""
        if carbonModifiers & UInt32(controlKey) != 0 { s += "⌃" }
        if carbonModifiers & UInt32(optionKey) != 0 { s += "⌥" }
        if carbonModifiers & UInt32(shiftKey) != 0 { s += "⇧" }
        if carbonModifiers & UInt32(cmdKey) != 0 { s += "⌘" }
        return s + keyLabel
    }

    static func carbonModifiers(from flags: NSEvent.ModifierFlags) -> UInt32 {
        var m: UInt32 = 0
        if flags.contains(.control) { m |= UInt32(controlKey) }
        if flags.contains(.option) { m |= UInt32(optionKey) }
        if flags.contains(.shift) { m |= UInt32(shiftKey) }
        if flags.contains(.command) { m |= UInt32(cmdKey) }
        return m
    }

    static let hyper = UInt32(controlKey | optionKey | cmdKey)

    static func label(for keyCode: UInt16, characters: String?) -> String {
        let special: [Int: String] = [
            kVK_Space: "Space", kVK_Return: "↩", kVK_Tab: "⇥", kVK_Escape: "⎋", kVK_Delete: "⌫",
            kVK_LeftArrow: "←", kVK_RightArrow: "→", kVK_UpArrow: "↑", kVK_DownArrow: "↓",
            kVK_F1: "F1", kVK_F2: "F2", kVK_F3: "F3", kVK_F4: "F4", kVK_F5: "F5", kVK_F6: "F6",
            kVK_F7: "F7", kVK_F8: "F8", kVK_F9: "F9", kVK_F10: "F10", kVK_F11: "F11", kVK_F12: "F12",
        ]
        if let s = special[Int(keyCode)] { return s }
        return (characters ?? "?").uppercased()
    }
}

enum HotkeyAction: String, CaseIterable {
    case snapshot, walkthrough, dictation, finish, toggleDraw

    var title: String {
        switch self {
        case .snapshot: return "Snapshot (start / next shot)"
        case .walkthrough: return "Walkthrough (start / stop)"
        case .dictation: return "Dictation, voice only (start / stop)"
        case .finish: return "Finish capture"
        case .toggleDraw: return "Toggle draw mode (Walkthrough)"
        }
    }

    var defaultHotkey: Hotkey {
        switch self {
        case .snapshot: return Hotkey(keyCode: UInt32(kVK_ANSI_S), carbonModifiers: Hotkey.hyper, keyLabel: "S")
        case .walkthrough: return Hotkey(keyCode: UInt32(kVK_ANSI_R), carbonModifiers: Hotkey.hyper, keyLabel: "R")
        case .dictation: return Hotkey(keyCode: UInt32(kVK_ANSI_T), carbonModifiers: Hotkey.hyper, keyLabel: "T")
        case .finish: return Hotkey(keyCode: UInt32(kVK_ANSI_F), carbonModifiers: Hotkey.hyper, keyLabel: "F")
        case .toggleDraw: return Hotkey(keyCode: UInt32(kVK_ANSI_D), carbonModifiers: Hotkey.hyper, keyLabel: "D")
        }
    }
}

enum DrawModifier: String, CaseIterable, Identifiable {
    case controlOption, optionCommand, function, off

    var id: String { rawValue }

    var title: String {
        switch self {
        case .controlOption: return "Hold ⌃⌥"
        case .optionCommand: return "Hold ⌥⌘"
        case .function: return "Hold fn"
        case .off: return "Off (toggle hotkey only)"
        }
    }

    var symbol: String {
        switch self {
        case .controlOption: return "⌃⌥"
        case .optionCommand: return "⌥⌘"
        case .function: return "fn"
        case .off: return ""
        }
    }

    func matches(_ flags: CGEventFlags) -> Bool {
        let relevant: CGEventFlags = [.maskControl, .maskAlternate, .maskCommand, .maskShift, .maskSecondaryFn]
        let held = flags.intersection(relevant)
        switch self {
        case .controlOption: return held == [.maskControl, .maskAlternate]
        case .optionCommand: return held == [.maskAlternate, .maskCommand]
        case .function: return held == [.maskSecondaryFn]
        case .off: return false
        }
    }
}

enum WhisperCompute: String, CaseIterable, Identifiable {
    case auto, gpu, neuralEngine

    var id: String { rawValue }

    var title: String {
        switch self {
        case .auto: return "Auto (GPU right away, Neural Engine once compiled)"
        case .gpu: return "GPU encoder (loads fast, ~3× slower)"
        case .neuralEngine: return "Neural Engine (fastest; first load can take ~10 min)"
        }
    }
}

enum ClipboardFormat: String, CaseIterable, Identifiable {
    case markdownFile, universal, textAndPaths, imagesAndText, imagesOnly, composite

    var id: String { rawValue }

    var title: String {
        switch self {
        case .markdownFile: return "capture.md only (agents that read files)"
        case .universal: return "capture.md + images (web chats)"
        case .textAndPaths: return "Text with image paths"
        case .imagesAndText: return "Images first, then text"
        case .imagesOnly: return "Images only"
        case .composite: return "Single composite image"
        }
    }
}

/// User settings, persisted in UserDefaults.
@MainActor
final class AppSettings: ObservableObject {
    static let shared = AppSettings()

    private let defaults = UserDefaults.standard

    @Published var hotkeys: [String: Hotkey] { didSet { save(hotkeys, "hotkeys") } }
    @Published var model: WhisperModel { didSet { defaults.set(model.rawValue, forKey: "model") } }
    @Published var language: TranscriptionLanguage { didSet { defaults.set(language.storageValue, forKey: "transcriptionLanguage") } }
    @Published var compute: WhisperCompute { didSet { defaults.set(compute.rawValue, forKey: "compute") } }
    @Published var vocabulary: String { didSet { defaults.set(vocabulary, forKey: "vocabulary") } }
    @Published var maxFrames: Int { didSet { defaults.set(maxFrames, forKey: "maxFrames") } }
    @Published var imageLongEdge: Int { didSet { defaults.set(imageLongEdge, forKey: "imageLongEdge") } }
    @Published var detailCrops: Bool { didSet { defaults.set(detailCrops, forKey: "detailCrops") } }
    @Published var maxWalkthroughMinutes: Int { didSet { defaults.set(maxWalkthroughMinutes, forKey: "maxWalkthroughMinutes") } }
    @Published var fadeSeconds: Double { didSet { defaults.set(fadeSeconds, forKey: "fadeSeconds") } }
    @Published var drawModifier: DrawModifier { didSet { defaults.set(drawModifier.rawValue, forKey: "drawModifier") } }
    @Published var allDisplays: Bool { didSet { defaults.set(allDisplays, forKey: "allDisplays") } }
    @Published var keepOriginals: Bool { didSet { defaults.set(keepOriginals, forKey: "keepOriginals") } }
    @Published var clipboardFormat: ClipboardFormat { didSet { defaults.set(clipboardFormat.rawValue, forKey: "clipboardFormat") } }
    @Published var retentionDays: Int { didSet { defaults.set(retentionDays, forKey: "retentionDays") } }
    @Published var playSounds: Bool { didSet { defaults.set(playSounds, forKey: "playSounds") } }
    @Published var onboardingShown: Bool { didSet { defaults.set(onboardingShown, forKey: "onboardingShown") } }

    private init() {
        let d = UserDefaults.standard
        if let data = d.data(forKey: "hotkeys"), let hk = try? JSONDecoder().decode([String: Hotkey].self, from: data) {
            hotkeys = hk
        } else {
            hotkeys = [:]
        }
        model = WhisperModel(rawValue: d.string(forKey: "model") ?? "") ?? .largeTurbo
        language = TranscriptionLanguage(storage: d.string(forKey: "transcriptionLanguage"))
        compute = WhisperCompute(rawValue: d.string(forKey: "compute") ?? "") ?? .auto
        vocabulary = d.string(forKey: "vocabulary") ?? ""
        maxFrames = d.object(forKey: "maxFrames") as? Int ?? 30
        imageLongEdge = d.object(forKey: "imageLongEdge") as? Int ?? 1568
        detailCrops = d.object(forKey: "detailCrops") as? Bool ?? true
        maxWalkthroughMinutes = d.object(forKey: "maxWalkthroughMinutes") as? Int ?? 10
        fadeSeconds = d.object(forKey: "fadeSeconds") as? Double ?? 3.0
        drawModifier = DrawModifier(rawValue: d.string(forKey: "drawModifier") ?? "") ?? .controlOption
        allDisplays = d.object(forKey: "allDisplays") as? Bool ?? true
        keepOriginals = d.object(forKey: "keepOriginals") as? Bool ?? false
        clipboardFormat = ClipboardFormat(rawValue: d.string(forKey: "clipboardFormat") ?? "") ?? .markdownFile
        retentionDays = d.object(forKey: "retentionDays") as? Int ?? 14
        playSounds = d.object(forKey: "playSounds") as? Bool ?? true
        onboardingShown = d.object(forKey: "onboardingShown") as? Bool ?? false
    }

    func hotkey(for action: HotkeyAction) -> Hotkey {
        hotkeys[action.rawValue] ?? action.defaultHotkey
    }

    func setHotkey(_ hotkey: Hotkey, for action: HotkeyAction) {
        hotkeys[action.rawValue] = hotkey
    }

    func resetHotkeys() {
        hotkeys = [:]
    }

    var launchAtLogin: Bool {
        get { SMAppService.mainApp.status == .enabled }
        set {
            objectWillChange.send()
            do {
                if newValue {
                    try SMAppService.mainApp.register()
                } else {
                    try SMAppService.mainApp.unregister()
                }
            } catch {
                Log.app.error("Launch at login change failed: \(error.localizedDescription, privacy: .public)")
            }
        }
    }

    private func save<T: Encodable>(_ value: T, _ key: String) {
        if let data = try? JSONEncoder().encode(value) {
            defaults.set(data, forKey: key)
        }
    }
}
