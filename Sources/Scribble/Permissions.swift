import AVFoundation
import AppKit
import CoreGraphics

enum Permissions {
    static var screenRecordingGranted: Bool { CGPreflightScreenCaptureAccess() }

    static var microphoneStatus: AVAuthorizationStatus { AVCaptureDevice.authorizationStatus(for: .audio) }

    static var microphoneGranted: Bool { microphoneStatus == .authorized }

    /// The first request shows the system prompt; later ones (macOS prompts only once) open
    /// System Settings instead.
    static func requestScreenRecording() {
        let key = "requestedScreenRecording"
        let askedBefore = UserDefaults.standard.bool(forKey: key)
        UserDefaults.standard.set(true, forKey: key)
        if !CGRequestScreenCaptureAccess(), askedBefore {
            openSettings("Privacy_ScreenCapture")
        }
    }

    static func requestMicrophone(_ completion: @escaping @MainActor (Bool) -> Void) {
        switch microphoneStatus {
        case .authorized:
            Task { @MainActor in completion(true) }
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                Task { @MainActor in completion(granted) }
            }
        default:
            openSettings("Privacy_Microphone")
            Task { @MainActor in completion(false) }
        }
    }

    static func openSettings(_ pane: String) {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)") {
            NSWorkspace.shared.open(url)
        }
    }

    /// Relaunches the app (needed after granting Screen Recording).
    @MainActor static func relaunch() {
        let path = Bundle.main.bundlePath
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/sh")
        task.arguments = ["-c", "sleep 0.7; /usr/bin/open \"\(path)\""]
        do {
            try task.run()
            NSApp.terminate(nil)
        } catch {
            Log.app.error("Relaunch failed: \(error.localizedDescription, privacy: .public)")
        }
    }
}
