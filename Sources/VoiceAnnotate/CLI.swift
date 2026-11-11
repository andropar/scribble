import AppKit
import Foundation
import VoiceAnnotateCore

/// Developer commands for exercising the pipeline without the UI.
enum CLI {
    static let usage = """
    voice-annotate developer commands:
      --transcribe <audio-file> [model]      Transcribe a file, print language and word timings
      --render-demo <out.png>                Render every annotation type onto a test image
      --demo-snapshot <audio-file> [screenshot.png annotations.json]
                                             Full Snapshot pipeline on a synthetic (or given @2x) screenshot
      --test-walkthrough <seconds> [screen|all]  Record display(s) (index into NSScreen.screens), run the pipeline
      --test-screenshot [screen]             Screenshot a display, run the pipeline

    Commands use the app's settings. These options override them for one run:
      --language <code|auto>                 Transcription language, e.g. ru
      --model <variant>                      Whisper model, e.g. openai_whisper-base
      --vocabulary <text>                    Custom vocabulary
    """

    /// Bundle identifier of the app, whose settings a bare binary doesn't see on its own.
    private static let appDomain = "com.jroth.voiceannotate"

    private static var languageOverride: TranscriptionLanguage?
    private static var modelOverride: String?
    private static var vocabularyOverride: String?

    @MainActor private static var language: TranscriptionLanguage { languageOverride ?? AppSettings.shared.language }
    @MainActor private static var model: String { modelOverride ?? AppSettings.shared.model.rawValue }
    @MainActor private static var vocabulary: String { vocabularyOverride ?? AppSettings.shared.vocabulary }

    private static func failUsage() -> Never {
        FileHandle.standardError.write(Data((usage + "\n").utf8))
        exit(2)
    }

    /// Removes `name` and its value from `args`.
    private static func takeOption(_ name: String, from args: inout [String]) -> String? {
        guard let index = args.firstIndex(of: name) else { return nil }
        guard index + 1 < args.count else { failUsage() }
        let value = args[index + 1]
        args.removeSubrange(index...(index + 1))
        return value
    }

    static func run(_ arguments: [String]) -> Never {
        if Bundle.main.bundleIdentifier == nil {
            UserDefaults.standard.addSuite(named: appDomain)
        }
        var args = arguments
        if let value = takeOption("--language", from: &args) {
            guard let language = TranscriptionLanguage(argument: value) else { failUsage() }
            languageOverride = language
        }
        modelOverride = takeOption("--model", from: &args)
        vocabularyOverride = takeOption("--vocabulary", from: &args)
        guard !args.isEmpty else { failUsage() }

        Task { @MainActor in
            do {
                switch args[0] {
                case "--transcribe" where args.count >= 2:
                    try await transcribe(path: args[1], model: args.count >= 3 ? args[2] : model)
                case "--render-demo" where args.count >= 2:
                    try renderDemo(to: URL(fileURLWithPath: args[1]))
                case "--demo-snapshot" where args.count >= 2:
                    try await demoSnapshot(audioPath: args[1], imagePath: args.count >= 4 ? args[2] : nil, annotationsPath: args.count >= 4 ? args[3] : nil)
                case "--test-walkthrough" where args.count >= 2:
                    let which = args.count >= 3 ? args[2] : nil
                    try await testWalkthrough(seconds: Double(args[1]) ?? 5, screens: which == "all" ? NSScreen.screens : [try screen(at: which)])
                case "--test-screenshot":
                    try await testScreenshot(screen: screen(at: args.count >= 2 ? args[1] : nil))
                default:
                    failUsage()
                }
                exit(0)
            } catch {
                FileHandle.standardError.write(Data("error: \(error.localizedDescription)\n".utf8))
                exit(1)
            }
        }
        // A real main run loop (dispatchMain() would exit the main thread, breaking AppKit).
        MainActor.assumeIsolated {
            NSApplication.shared.setActivationPolicy(.accessory)
            NSApplication.shared.run()
        }
        exit(0)
    }

    @MainActor
    private static func screen(at index: String?) throws -> NSScreen {
        guard let index else { return NSScreen.main ?? NSScreen.screens[0] }
        guard let i = Int(index), NSScreen.screens.indices.contains(i) else { throw AppError("No screen \(index)") }
        return NSScreen.screens[i]
    }

    /// Borderless colored window at a canvas position (points, top-left origin) of `screen`.
    @MainActor
    private static func markerWindow(on screen: NSScreen, canvasRect r: CGRect, color: NSColor) -> NSWindow {
        let f = screen.frame
        let window = NSWindow(contentRect: NSRect(x: f.minX + r.minX, y: f.maxY - r.maxY, width: r.width, height: r.height), styleMask: [.borderless], backing: .buffered, defer: false)
        window.level = .screenSaver
        window.backgroundColor = color
        window.isReleasedWhenClosed = false
        return window
    }

    private static func out(_ s: String) {
        FileHandle.standardOutput.write(Data((s + "\n").utf8))
    }

    @MainActor
    private static func loadModel(_ model: String) async throws {
        let started = Date()
        let task = Transcriber.shared.ensureLoaded(model)
        var lastStatus = ""
        let poll = Task { @MainActor in
            while !Task.isCancelled {
                let s = Transcriber.shared.status.title
                if s != lastStatus {
                    out("[model] \(s)")
                    lastStatus = s
                }
                try? await Task.sleep(nanoseconds: 500_000_000)
            }
        }
        _ = try await task.value
        poll.cancel()
        out(String(format: "[model] ready after %.1fs", Date().timeIntervalSince(started)))
    }

    @MainActor
    private static func transcribe(path: String, model: String) async throws {
        let samples = try AudioRecorder.loadSamples(from: URL(fileURLWithPath: path))
        out(String(format: "audio: %.2fs, rms %.4f", Double(samples.count) / AudioResult.sampleRate, AudioRecorder.rms(samples)))
        try await loadModel(model)
        let started = Date()
        let result = try await Transcriber.shared.transcribe(samples, model: model, language: language, vocabulary: vocabulary)
        out(String(format: "transcribed in %.2fs", Date().timeIntervalSince(started)))
        let languages = result.languages.joined(separator: ", ")
        out("language: \(languages.isEmpty ? "none, no speech" : "\(languages) (\(language == .auto ? "detected" : "selected"))")")
        if let warning = result.warning {
            FileHandle.standardError.write(Data("warning: \(warning)\n".utf8))
        }
        for s in result.segments {
            out(String(format: "[segment %6.2f–%6.2f] %@", s.start, s.end, s.text))
        }
        for w in result.words {
            out(String(format: "%6.2f–%6.2f %@", w.start, w.end, w.text))
        }
    }

    static func demoImage(size: CGSize, scale: CGFloat) -> CGImage {
        let w = Int(size.width * scale), h = Int(size.height * scale)
        let ctx = ImageRendering.makeContext(width: w, height: h)!
        let colors = [CGColor(srgbRed: 0.93, green: 0.94, blue: 0.97, alpha: 1), CGColor(srgbRed: 0.75, green: 0.8, blue: 0.9, alpha: 1)] as CFArray
        let gradient = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB), colors: colors, locations: [0, 1])!
        ctx.drawLinearGradient(gradient, start: .zero, end: CGPoint(x: w, y: h), options: [])
        ctx.setFillColor(CGColor(gray: 1, alpha: 1))
        for i in 0..<6 {
            ctx.fill(CGRect(x: CGFloat(60 + i * 220) * scale, y: CGFloat(120 + (i % 3) * 200) * scale, width: 180 * scale, height: 120 * scale))
        }
        return ctx.makeImage()!
    }

    static func demoAnnotations(shot: Int = 0, startTime: Double = 1) -> [Annotation] {
        var anns: [Annotation] = []
        func add(_ tool: AnnotationTool, _ points: [CGPoint], _ color: RGBAColor, _ t: Double) {
            let number = tool == .pen ? nil : anns.filter { $0.number != nil }.count + 1
            var a = Annotation(id: anns.count + 1, number: number, tool: tool, points: points, color: color, startTime: t, shot: shot)
            a.finalize(endTime: t + 0.8)
            anns.append(a)
        }
        let circle = (0...40).map { i -> CGPoint in
            let angle = Double(i) / 40 * 2 * .pi * 1.05
            return CGPoint(x: 400 + 120 * cos(angle), y: 300 + 70 * sin(angle))
        }
        add(.pen, circle, .red, startTime)
        add(.arrow, [CGPoint(x: 900, y: 650), CGPoint(x: 700, y: 420)], .blue, startTime + 2)
        add(.rectangle, [CGPoint(x: 1000, y: 120), CGPoint(x: 1280, y: 260)], .green, startTime + 4)
        add(.highlighter, (0...20).map { CGPoint(x: 120 + Double($0) * 15, y: 720) }, .yellow, startTime + 6)
        add(.pin, [CGPoint(x: 1300, y: 700)], .magenta, startTime + 8)
        add(.pen, (0...20).map { CGPoint(x: 600 + Double($0) * 12, y: 780) }, .red, startTime + 9)
        add(.ellipse, [CGPoint(x: 200, y: 450), CGPoint(x: 330, y: 520)], .white, startTime + 10)
        return anns
    }

    private static func renderDemo(to url: URL) throws {
        let canvas = CGSize(width: 1512, height: 982)
        let image = demoImage(size: canvas, scale: 2)
        let outSize = ImageSizing.fit(CGSize(width: image.width, height: image.height), longEdge: 1568)
        guard let rendered = ImageRendering.renderAnnotated(image: image, canvasSize: canvas, annotations: demoAnnotations(), outputSize: outSize) else {
            throw AppError("render failed")
        }
        try ImageRendering.writePNG(rendered, to: url)
        let mapper = CoordinateMapper(canvasSize: canvas, imageSize: outSize)
        for a in demoAnnotations() {
            out(AnnotationDescriber.describe(a, mapper: mapper, frame: 1))
        }
        out("wrote \(url.path)")
    }

    @MainActor
    private static func settingsInput(mode: CaptureMode, folder: URL, t0: Double, duration: Double) -> CaptureInput {
        let s = AppSettings.shared
        let displays = NSScreen.screens.enumerated().map { i, screen in
            DisplayInfo(index: i, name: screen.localizedName, canvasSize: screen.frame.size)
        }
        return CaptureInput(
            mode: mode, folder: folder, startDate: Date(), t0: t0, duration: duration, annotations: [], clicks: [],
            shots: [], displays: displays, audio: nil, recordings: [:], cursorTimeline: [],
            model: model, language: language, vocabulary: vocabulary,
            maxFrames: s.maxFrames, longEdge: s.imageLongEdge, detailCrops: s.detailCrops, keepOriginals: s.keepOriginals,
            fadeSeconds: s.fadeSeconds
        )
    }

    @MainActor
    private static func finishDemo(_ input: CaptureInput, clipboard: Bool = true) async throws {
        let started = Date()
        let capture = try await CaptureProcessor.process(input)
        out(String(format: "processed in %.2fs → %@", Date().timeIntervalSince(started), capture.folder))
        if clipboard {
            try ClipboardWriter.write(capture, format: .universal)
            out("clipboard: \(NSPasteboard.general.pasteboardItems?.count ?? 0) items")
        }
        out("----- capture.md -----")
        out(try String(contentsOf: capture.markdownURL, encoding: .utf8))
        try? await Task.sleep(nanoseconds: 1_500_000_000) // let background muxing finish
    }

    /// One mark of an `--demo-snapshot` annotations file: canvas points (top-left origin), session seconds.
    private struct DemoMark: Decodable {
        let tool: AnnotationTool
        let points: [[Double]]
        let color: String
        let start: Double
        let end: Double
    }

    private static func loadDemoMarks(from url: URL) throws -> [Annotation] {
        let colors: [String: RGBAColor] = ["red": .red, "yellow": .yellow, "green": .green, "blue": .blue, "magenta": .magenta, "white": .white]
        let marks = try JSONDecoder().decode([DemoMark].self, from: Data(contentsOf: url))
        var numbered = 0
        return try marks.enumerated().map { i, m in
            guard let color = colors[m.color] else { throw AppError("Unknown color \(m.color)") }
            if m.tool != .pen { numbered += 1 }
            var a = Annotation(id: i + 1, number: m.tool == .pen ? nil : numbered, tool: m.tool, points: m.points.map { CGPoint(x: $0[0], y: $0[1]) }, color: color, startTime: m.start)
            a.finalize(endTime: m.end)
            return a
        }
    }

    @MainActor
    private static func demoSnapshot(audioPath: String, imagePath: String?, annotationsPath: String?) async throws {
        Paths.ensure()
        let samples = try AudioRecorder.loadSamples(from: URL(fileURLWithPath: audioPath))
        try await loadModel(model)
        var canvas = CGSize(width: 3008, height: 1692) // 6K display at 2x: exercises detail crops
        var image = demoImage(size: canvas, scale: 2)
        var annotations = demoAnnotations()
        if let imagePath, let annotationsPath {
            guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: imagePath) as CFURL, nil),
                  let loaded = CGImageSourceCreateImageAtIndex(src, 0, nil) else { throw AppError("Could not read \(imagePath)") }
            image = loaded
            canvas = CGSize(width: loaded.width / 2, height: loaded.height / 2)
            annotations = try loadDemoMarks(from: URL(fileURLWithPath: annotationsPath))
        }
        let folder = try Paths.newCaptureFolder()
        let t0 = HostClock.now()
        var input = settingsInput(mode: .snapshot, folder: folder, t0: t0, duration: Double(samples.count) / AudioResult.sampleRate)
        input.shots = [ShotData(image: image, time: 0.1, canvasSize: canvas, display: 0, round: 0, primary: true)]
        input.displays = [DisplayInfo(index: 0, name: "Demo", canvasSize: canvas)]
        input.annotations = annotations
        input.audio = AudioResult(samples: samples, firstSampleHostTime: t0)
        // A given screenshot means generating docs assets: leave the user's clipboard alone.
        try await finishDemo(input, clipboard: imagePath == nil)
    }

    @MainActor
    private static func testScreenshot(screen: NSScreen) async throws {
        Paths.ensure()
        guard Permissions.screenRecordingGranted else { throw AppError("No screen recording permission for this process") }
        // Our own windows are excluded from screenshots (that keeps the HUD out), so a test
        // marker must come from another process (see the scratch marker helper).
        let image = try await Screenshotter.capture(screen: screen)
        out("screen \(screen.localizedName) frame \(screen.frame): screenshot \(image.width)x\(image.height)")
        let folder = try Paths.newCaptureFolder()
        var input = settingsInput(mode: .snapshot, folder: folder, t0: HostClock.now(), duration: 1)
        let index = NSScreen.screens.firstIndex(of: screen) ?? 0
        input.shots = [ShotData(image: image, time: 0, canvasSize: screen.frame.size, display: index, round: 0, primary: true)]
        var box = Annotation(id: 1, number: 1, tool: .rectangle, points: [CGPoint(x: 290, y: 190), CGPoint(x: 510, y: 330)], color: .red, startTime: 0.2, screen: index)
        box.finalize(endTime: 0.5)
        input.annotations = [box]
        try await finishDemo(input)
    }

    /// Records `screens` at once. Teal marker appears on the first screen at 1/4, the cursor
    /// "moves" to the last screen at 1/2, where the orange marker appears at 5/8 and is
    /// pinned and clicked. With one screen both happen there.
    @MainActor
    private static func testWalkthrough(seconds: Double, screens: [NSScreen]) async throws {
        Paths.ensure()
        guard Permissions.screenRecordingGranted else { throw AppError("No screen recording permission for this process") }
        let folder = try Paths.newCaptureFolder()
        let indices = screens.map { NSScreen.screens.firstIndex(of: $0) ?? 0 }
        var recorders: [Int: WalkthroughRecorder] = [:]
        let t0 = HostClock.now()
        for (screen, index) in zip(screens, indices) {
            let recorder = WalkthroughRecorder()
            try await recorder.start(
                displayID: Screens.displayID(of: screen), excludingWindowIDs: [],
                outputURL: folder.appendingPathComponent("screen_display\(index + 1).mp4")
            )
            recorders[index] = recorder
            out("screen \(index) \(screen.localizedName) frame \(screen.frame): recording \(recorder.pixelSize)")
        }
        let first = screens[0], last = screens[screens.count - 1]
        func sleep(_ fraction: Double) async { try? await Task.sleep(nanoseconds: UInt64(seconds * fraction * 1e9)) }
        await sleep(0.25)
        let teal = markerWindow(on: first, canvasRect: CGRect(x: 200, y: 200, width: 400, height: 300), color: .systemTeal)
        teal.orderFrontRegardless()
        await sleep(0.375)
        let orange = markerWindow(on: last, canvasRect: CGRect(x: 600, y: 300, width: 600, height: 400), color: .systemOrange)
        orange.orderFrontRegardless()
        await sleep(0.375)
        let duration = HostClock.now() - t0
        var input = settingsInput(mode: .walkthrough, folder: folder, t0: t0, duration: duration)
        for (index, recorder) in recorders {
            let r = await recorder.stop()
            out("display \(index): frames sampled \(r.samples.count), video \(r.videoURL?.lastPathComponent ?? "none")")
            input.recordings[index] = r
        }
        teal.orderOut(nil)
        orange.orderOut(nil)
        input.cursorTimeline = [(0, indices[0]), (seconds * 0.5, indices[indices.count - 1])]
        var pin = Annotation(id: 1, number: 1, tool: .pin, points: [CGPoint(x: 700, y: 350)], color: .red, startTime: seconds * 0.72, screen: indices[indices.count - 1])
        pin.finalize(endTime: seconds * 0.72)
        input.annotations = [pin]
        // Click given in global AppKit coordinates, as the real click monitor sees it: center of the orange window.
        let globalClick = NSPoint(x: last.frame.minX + 900, y: last.frame.maxY - 500)
        input.clicks = [ClickEvent(time: seconds * 0.8, location: Screens.canvasPoint(fromGlobal: globalClick, on: last), screen: indices[indices.count - 1])]
        try await finishDemo(input)
    }
}
