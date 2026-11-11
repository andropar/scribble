import Carbon.HIToolbox
import Foundation

/// Global hotkeys via Carbon `RegisterEventHotKey` (no Accessibility permission needed).
@MainActor
final class HotkeyManager {
    static let shared = HotkeyManager()

    private var refs: [EventHotKeyRef] = []
    private var handlers: [UInt32: () -> Void] = [:]
    private var handlerInstalled = false
    private var suspended = false
    private var registrations: [(Hotkey, () -> Void)] = []
    private static let signature: OSType = 0x5641_4E54 // "VANT"

    func setRegistrations(_ regs: [(Hotkey, () -> Void)]) {
        registrations = regs
        if !suspended { apply() }
    }

    /// Temporarily releases all hotkeys (e.g. while recording a new shortcut).
    func suspend() {
        suspended = true
        unregisterAll()
    }

    func resume() {
        suspended = false
        apply()
    }

    private func apply() {
        installHandlerIfNeeded()
        unregisterAll()
        for (index, (hotkey, handler)) in registrations.enumerated() {
            let id = UInt32(index + 1)
            var ref: EventHotKeyRef?
            let status = RegisterEventHotKey(
                hotkey.keyCode, hotkey.carbonModifiers,
                EventHotKeyID(signature: Self.signature, id: id),
                GetApplicationEventTarget(), 0, &ref
            )
            if status == noErr, let ref {
                refs.append(ref)
                handlers[id] = handler
            } else {
                Log.app.error("Failed to register hotkey \(hotkey.display, privacy: .public) (status \(status))")
            }
        }
    }

    private func unregisterAll() {
        for ref in refs { UnregisterEventHotKey(ref) }
        refs.removeAll()
        handlers.removeAll()
    }

    fileprivate func fire(id: UInt32) {
        handlers[id]?()
    }

    private func installHandlerIfNeeded() {
        guard !handlerInstalled else { return }
        handlerInstalled = true
        var eventType = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, event, _ -> OSStatus in
            var hkID = EventHotKeyID()
            let status = GetEventParameter(
                event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                nil, MemoryLayout<EventHotKeyID>.size, nil, &hkID
            )
            guard status == noErr else { return status }
            let id = hkID.id
            DispatchQueue.main.async {
                MainActor.assumeIsolated { HotkeyManager.shared.fire(id: id) }
            }
            return noErr
        }, 1, &eventType, nil, nil)
    }
}
