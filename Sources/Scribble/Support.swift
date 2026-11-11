import AppKit
import Darwin
import Foundation
import QuartzCore
import os

enum Log {
    static let app = Logger(subsystem: "com.jroth.voiceannotate", category: "app")
    static let capture = Logger(subsystem: "com.jroth.voiceannotate", category: "capture")
    static let audio = Logger(subsystem: "com.jroth.voiceannotate", category: "audio")
    static let transcribe = Logger(subsystem: "com.jroth.voiceannotate", category: "transcribe")
}

/// Single clock for everything: host time in seconds (mach_absolute_time based), which is
/// the base of CACurrentMediaTime, SCStream display times and AVAudioTime host times.
enum HostClock {
    private static let timebase: mach_timebase_info_data_t = {
        var tb = mach_timebase_info_data_t()
        mach_timebase_info(&tb)
        return tb
    }()

    static func now() -> Double { CACurrentMediaTime() }

    static func seconds(fromMach ticks: UInt64) -> Double {
        Double(ticks) * Double(timebase.numer) / Double(timebase.denom) / 1_000_000_000
    }
}

enum Paths {
    /// `~/.scribble`. Before 0.5 the app was called voice-annotate and used
    /// `~/.voice-annotate`; that folder is taken over on the first start (see `adoptOldFolder`).
    static let root: URL = {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let new = home.appendingPathComponent(".scribble", isDirectory: true)
        let old = home.appendingPathComponent(".voice-annotate", isDirectory: true)
        return adoptOldFolder(old, as: new) ? new : old
    }()

    /// Moves the old folder (captures, models, the recent list) to `new` and leaves a link at
    /// the old place, so paths in earlier captures and in agents' transcripts still open.
    /// False only if the old folder exists and can't be moved: the app then keeps using it.
    static func adoptOldFolder(_ old: URL, as new: URL, defaults: UserDefaults = .standard) -> Bool {
        let fm = FileManager.default
        let type = (try? fm.attributesOfItem(atPath: old.path))?[.type] as? FileAttributeType
        guard type == .typeDirectory, !fm.fileExists(atPath: new.path) else { return true }
        do {
            try fm.moveItem(at: old, to: new)
        } catch {
            Log.app.error("Could not move \(old.path, privacy: .public) to \(new.path, privacy: .public): \(error.localizedDescription, privacy: .public)")
            return false
        }
        try? fm.createSymbolicLink(at: old, withDestinationURL: new)
        // Paths the app stored itself: the recent captures and where each model was found.
        let recents = new.appendingPathComponent("recent.json")
        if let text = try? String(contentsOf: recents, encoding: .utf8) {
            let moved = text
                .replacingOccurrences(of: old.path + "/", with: new.path + "/")
                .replacingOccurrences(of: old.path.replacingOccurrences(of: "/", with: "\\/") + "\\/", with: new.path.replacingOccurrences(of: "/", with: "\\/") + "\\/")
            try? moved.write(to: recents, atomically: true, encoding: .utf8)
        }
        for (key, value) in defaults.dictionaryRepresentation() where key.hasPrefix("modelFolder.") {
            if let path = value as? String, path.hasPrefix(old.path + "/") {
                defaults.set(new.path + path.dropFirst(old.path.count), forKey: key)
            }
        }
        Log.app.notice("Moved \(old.path, privacy: .public) to \(new.path, privacy: .public)")
        return true
    }

    static let captures = root.appendingPathComponent("captures", isDirectory: true)
    static let models = root.appendingPathComponent("models", isDirectory: true)
    static let recents = root.appendingPathComponent("recent.json")

    static func ensure() {
        for dir in [root, captures, models] {
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        }
    }

    static func newCaptureFolder(date: Date = Date()) throws -> URL {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd_HH-mm-ss"
        let base = f.string(from: date)
        var url = captures.appendingPathComponent(base, isDirectory: true)
        var n = 2
        while FileManager.default.fileExists(atPath: url.path) {
            url = captures.appendingPathComponent("\(base)-\(n)", isDirectory: true)
            n += 1
        }
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    /// Deletes capture folders older than `days`.
    static func prune(olderThanDays days: Int) {
        guard days > 0 else { return }
        let cutoff = Date().addingTimeInterval(-Double(days) * 86_400)
        let fm = FileManager.default
        guard let items = try? fm.contentsOfDirectory(
            at: captures, includingPropertiesForKeys: [.creationDateKey], options: [.skipsHiddenFiles]
        ) else { return }
        for item in items {
            let created = (try? item.resourceValues(forKeys: [.creationDateKey]))?.creationDate ?? Date()
            if created < cutoff {
                do {
                    try fm.removeItem(at: item)
                    Log.app.info("Pruned old capture \(item.lastPathComponent, privacy: .public)")
                } catch {
                    Log.app.error("Failed to prune \(item.path, privacy: .public): \(error.localizedDescription, privacy: .public)")
                }
            }
        }
    }
}

enum Sounds {
    @MainActor static func play(_ name: String) {
        guard AppSettings.shared.playSounds, let sound = NSSound(named: NSSound.Name(name)) else { return }
        sound.volume = 0.35
        sound.play()
    }

    @MainActor static func start() { play("Pop") }
    @MainActor static func stop() { play("Bottle") }
    @MainActor static func done() { play("Glass") }
    @MainActor static func error() { play("Basso") }
}

enum Screens {
    /// nil only while no display is attached (e.g. mid-reconfiguration).
    static func underMouse() -> NSScreen? {
        let p = NSEvent.mouseLocation
        return NSScreen.screens.first { NSMouseInRect(p, $0.frame, false) } ?? NSScreen.main ?? NSScreen.screens.first
    }

    static func displayID(of screen: NSScreen) -> CGDirectDisplayID {
        (screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value ?? CGMainDisplayID()
    }

    /// Converts a global AppKit point (origin bottom-left of the primary screen) into the
    /// screen's canvas space (points, origin top-left of that screen).
    static func canvasPoint(fromGlobal p: NSPoint, on screen: NSScreen) -> CGPoint {
        CGPoint(x: p.x - screen.frame.minX, y: screen.frame.maxY - p.y)
    }
}

struct AppError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}
