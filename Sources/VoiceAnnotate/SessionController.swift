import AppKit
import Combine
import VoiceAnnotateCore

/// A display taking part in the current capture.
struct CaptureDisplay {
    let index: Int
    let screen: NSScreen
    let displayID: CGDirectDisplayID
    let name: String

    var canvasSize: CGSize { screen.frame.size }
    var info: DisplayInfo { DisplayInfo(index: index, name: name, canvasSize: canvasSize) }
}

final class CaptureSession {
    let mode: CaptureMode
    let folder: URL
    let startDate = Date()
    let t0 = HostClock.now()
    /// All displays connected when the capture started.
    let displays: [CaptureDisplay]
    var annotations: [Annotation] = []
    var clicks: [ClickEvent] = []
    var shots: [ShotData] = []
    var round = -1
    var cursorTimeline: [(time: Double, display: Int)] = []

    init(mode: CaptureMode, folder: URL) {
        self.mode = mode
        self.folder = folder
        displays = NSScreen.screens.enumerated().map { i, screen in
            CaptureDisplay(index: i, screen: screen, displayID: Screens.displayID(of: screen), name: screen.localizedName)
        }
    }

    func now() -> Double { HostClock.now() - t0 }

    var nextID: Int { (annotations.map(\.id).max() ?? 0) + 1 }
    var nextMarkNumber: Int { (annotations.compactMap(\.number).max() ?? 0) + 1 }
    /// Marks with a number badge (pen drawings don't count).
    var markCount: Int { annotations.filter { $0.number != nil }.count }

    func display(atGlobal p: NSPoint) -> CaptureDisplay? {
        displays.first { NSMouseInRect(p, $0.screen.frame, false) }
    }

    /// Display under the mouse (falls back to the first one).
    var cursorDisplay: CaptureDisplay {
        display(atGlobal: NSEvent.mouseLocation) ?? displays[0]
    }
}

/// Owns the lifecycle of a capture: start → record/annotate → finish → process → clipboard.
/// Works across all connected displays: Snapshot freezes every display, Walkthrough records
/// every display, and annotations can be drawn on any of them.
// Task-group results are structs, not `(Int, …)` tuples: with tuples the display index
// came back as 0 for every child task in release builds (Swift 6.3).
private struct ShotResult {
    let index: Int
    let image: CGImage?
    let error: Error?
}

private struct StoppedRecording {
    let index: Int
    let result: RecordingResult
}

@MainActor
final class SessionController: NSObject, CanvasDelegate {
    enum Phase: Equatable {
        case idle, snapshot, snapshotNavigating, walkthrough, dictation, processing

        var isCapturing: Bool { self == .snapshot || self == .snapshotNavigating || self == .walkthrough || self == .dictation }
    }

    private(set) var phase: Phase = .idle {
        didSet { onPhaseChange?(phase) }
    }

    var onPhaseChange: ((Phase) -> Void)?
    var onNeedsPermissions: (() -> Void)?

    private let settings = AppSettings.shared
    private let recents: RecentsStore

    private var session: CaptureSession?
    /// One overlay + canvas per display taking part (keyed by display index).
    private var overlays: [Int: OverlayPanel] = [:]
    private var canvases: [Int: AnnotationCanvasView] = [:]
    private var hud: FloatingPanel?
    private var hudScreen: NSScreen?
    private let hudModel = HUDModel()
    private var audio: AudioRecorder?
    private var recorders: [Int: WalkthroughRecorder] = [:]
    private var clickMonitor: Any?
    private var ticker: Timer?
    private var previousApp: NSRunningApplication?
    private var lastCursorDisplay: Int?
    /// Busy toast shown while processing; kept current with the model's download/load status.
    private var processingToast: (title: String, screen: NSScreen?)?
    private var modelStatusWatch: AnyCancellable?

    init(recents: RecentsStore) {
        self.recents = recents
        super.init()
        hudModel.onTool = { [weak self] in self?.select(tool: $0) }
        hudModel.onColor = { [weak self] in self?.select(colorIndex: $0) }
        hudModel.onUndo = { [weak self] in self?.undo() }
        hudModel.onNextShot = { [weak self] in self?.hideForNextShot() }
        hudModel.onToggleDraw = { [weak self] in self?.hudModel.drawToggled.toggle() }
        hudModel.onFinish = { [weak self] in self?.finish() }
        hudModel.onCancel = { [weak self] in self?.cancel() }
    }

    // MARK: Hotkeys

    func handle(_ action: HotkeyAction) {
        switch (action, phase) {
        case (.snapshot, .idle): startSnapshot()
        case (.snapshot, .snapshot): finish()
        case (.snapshot, .snapshotNavigating): captureRound()
        case (.walkthrough, .idle): startWalkthrough()
        case (.walkthrough, .walkthrough): finish()
        case (.dictation, .idle): startDictation()
        case (.dictation, .dictation): finish()
        case (.finish, _) where phase.isCapturing: finish()
        case (.toggleDraw, .walkthrough): hudModel.drawToggled.toggle()
        case (_, .processing): showProcessingToast()
        default: break
        }
    }

    // MARK: Snapshot

    func startSnapshot() {
        guard phase == .idle else { return }
        guard Permissions.screenRecordingGranted else {
            onNeedsPermissions?()
            return
        }
        // The system prompt would sit under the frozen-screen overlays, so ask first.
        if Permissions.microphoneStatus == .notDetermined {
            Permissions.requestMicrophone { [weak self] _ in self?.startSnapshot() }
            return
        }
        guard let session = makeSession(.snapshot) else { return }
        phase = .snapshot
        rememberFrontmostApp()
        resetHUD(mode: .snapshot)
        startAudio(for: session)
        captureRound()
    }

    /// Freezes the displays (all of them, or the one under the cursor) as a new round of shots.
    private func captureRound() {
        guard let session else { return }
        let primary = session.cursorDisplay
        let targets = settings.allDisplays ? session.displays : [primary]
        Task {
            var images: [Int: CGImage] = [:]
            var primaryError: Error?
            await withTaskGroup(of: ShotResult.self) { group in
                for d in targets {
                    let index = d.index, screen = d.screen
                    group.addTask { @MainActor in
                        do { return ShotResult(index: index, image: try await Screenshotter.capture(screen: screen), error: nil) }
                        catch { return ShotResult(index: index, image: nil, error: error) }
                    }
                }
                for await shot in group {
                    if let image = shot.image {
                        images[shot.index] = image
                    } else if let error = shot.error {
                        Log.capture.error("Screenshot of display \(shot.index) failed: \(error.localizedDescription, privacy: .public)")
                        if shot.index == primary.index { primaryError = error }
                    }
                }
            }
            guard self.session === session, self.phase == .snapshot || self.phase == .snapshotNavigating else { return }
            if let primaryError {
                self.abort("Screenshot failed", detail: primaryError.localizedDescription)
                return
            }
            session.round += 1
            let time = session.now()
            var shotIndices: [Int: Int] = [:]
            for d in targets {
                guard let image = images[d.index] else { continue }
                shotIndices[d.index] = session.shots.count
                session.shots.append(ShotData(
                    image: image, time: time, canvasSize: d.canvasSize, display: d.index,
                    round: session.round, primary: d.index == primary.index
                ))
            }
            self.presentRound(session: session, shotIndices: shotIndices, primary: primary)
            if session.round == 0 { Sounds.start() }
        }
    }

    private func presentRound(session: CaptureSession, shotIndices: [Int: Int], primary: CaptureDisplay) {
        closeOverlays()
        for (displayIndex, shotIndex) in shotIndices.sorted(by: { $0.key < $1.key }) {
            let display = session.displays[displayIndex]
            let shot = session.shots[shotIndex]
            let panel = OverlayPanel(screen: display.screen)
            panel.allowsKey = true
            panel.ignoresMouseEvents = false
            let bounds = NSRect(origin: .zero, size: display.canvasSize)
            let backdrop = SnapshotBackdropView(frame: bounds, image: shot.image)
            let canvas = AnnotationCanvasView(frame: bounds)
            canvas.autoresizingMask = [.width, .height]
            configure(canvas, shot: shotIndex, screen: displayIndex, fade: nil)
            canvas.crosshair = true
            backdrop.addSubview(canvas)
            panel.contentView = backdrop
            panel.initialFirstResponder = canvas
            overlays[displayIndex] = panel
            canvases[displayIndex] = canvas
            panel.orderFrontRegardless()
        }
        // Nonactivating panels take key focus without activating the app; asking for
        // activation instead is refused by macOS when the request isn't user-initiated,
        // leaving keystrokes going to the previously frontmost app.
        if let key = overlays[primary.index] ?? overlays.values.first {
            key.makeKeyAndOrderFront(nil)
            if let canvas = canvases[primary.index] { key.makeFirstResponder(canvas) }
        }

        phase = .snapshot
        hudModel.navigating = false
        hudModel.shotCount = session.round + 1
        hudModel.starting = false
        showHUD(on: primary.screen)
    }

    private func hideForNextShot() {
        guard phase == .snapshot else { return }
        phase = .snapshotNavigating
        closeOverlays()
        hudModel.navigating = true
        refitHUD()
        previousApp?.activate()
    }

    // MARK: Walkthrough

    func startWalkthrough() {
        guard phase == .idle else { return }
        guard Permissions.screenRecordingGranted else {
            onNeedsPermissions?()
            return
        }
        guard let session = makeSession(.walkthrough) else { return }
        phase = .walkthrough
        let primary = session.cursorDisplay
        let targets = settings.allDisplays ? session.displays : [primary]
        session.cursorTimeline = [(0, primary.index)]
        lastCursorDisplay = primary.index

        for d in targets {
            let panel = OverlayPanel(screen: d.screen)
            panel.allowsKey = false
            panel.ignoresMouseEvents = true
            let canvas = AnnotationCanvasView(frame: NSRect(origin: .zero, size: d.canvasSize))
            configure(canvas, shot: 0, screen: d.index, fade: settings.fadeSeconds)
            panel.contentView = canvas
            panel.orderFrontRegardless()
            overlays[d.index] = panel
            canvases[d.index] = canvas
        }

        resetHUD(mode: .walkthrough)
        showHUD(on: primary.screen)
        startAudio(for: session)

        let excluded: Set<CGWindowID> = hud.map { [CGWindowID($0.windowNumber)] } ?? []
        var started: [Int: WalkthroughRecorder] = [:]
        for d in targets {
            let recorder = WalkthroughRecorder()
            recorder.onError = { [weak self] error in
                guard let self, self.recorders[d.index] === recorder else { return }
                Toast.shared.show("Recording of \(d.name) stopped", detail: error.localizedDescription, success: false, duration: 4)
                self.finish()
            }
            started[d.index] = recorder
        }
        recorders = started
        Task {
            do {
                try await withThrowingTaskGroup(of: Void.self) { group in
                    for d in targets {
                        guard let recorder = started[d.index] else { continue }
                        let output = session.folder.appendingPathComponent("screen_display\(d.index + 1).mp4")
                        let id = d.displayID
                        group.addTask { try await recorder.start(displayID: id, excludingWindowIDs: excluded, outputURL: output) }
                    }
                    try await group.waitForAll()
                }
                guard self.session === session, self.phase == .walkthrough else {
                    for r in started.values { _ = await r.stop() }
                    return
                }
                self.hudModel.starting = false
                Sounds.start()
            } catch {
                Log.capture.error("Recording failed to start: \(error.localizedDescription, privacy: .public)")
                if self.session === session, self.phase == .walkthrough {
                    self.abort("Screen recording failed", detail: error.localizedDescription)
                } else {
                    for r in started.values { _ = await r.stop() }
                }
            }
        }

        clickMonitor = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown]) { [weak self] _ in
            MainActor.assumeIsolated { self?.recordClick() }
        }
        startTicker()
    }

    // MARK: Dictation

    func startDictation() {
        guard phase == .idle else { return }
        guard Permissions.microphoneStatus != .denied, Permissions.microphoneStatus != .restricted else {
            Toast.shared.show("Microphone access needed", detail: "System Settings → Privacy & Security → Microphone", success: false, duration: 4)
            return
        }
        guard let session = makeSession(.dictation) else { return }
        phase = .dictation
        resetHUD(mode: .dictation)
        showHUD(on: session.cursorDisplay.screen)
        startAudio(for: session)
    }

    private func finishDictation(_ session: CaptureSession) {
        phase = .processing
        let audioResult = audio?.stop()
        audio = nil
        let screen = hudScreen
        teardownUI()
        Sounds.stop()
        beginProcessingToast("Transcribing…", on: screen)

        Task {
            let samples = audioResult?.samples ?? []
            var transcript: TranscriptionOutput?
            var failure: Error?
            if !samples.isEmpty {
                do {
                    transcript = try await Transcriber.shared.transcribe(samples, model: self.settings.model.rawValue, language: self.settings.language, vocabulary: self.settings.vocabulary)
                } catch {
                    failure = error
                }
            }
            let text = transcript?.plainText ?? ""
            Self.saveDictation(samples, text: text, in: session.folder)
            self.endProcessingToast()
            if let failure {
                Log.transcribe.error("Dictation failed: \(failure.localizedDescription, privacy: .public)")
                Toast.shared.show("Transcription failed", detail: failure.localizedDescription, success: false, duration: 5, on: screen)
                Sounds.error()
            } else if text.isEmpty {
                Toast.shared.show("No speech detected", success: false, duration: 2.5, on: screen)
            } else {
                do {
                    try ClipboardWriter.writeText(text)
                    let words = text.split(whereSeparator: \.isWhitespace).count
                    if let warning = transcript?.warning {
                        Toast.shared.show("Copied to clipboard", detail: warning, success: false, duration: 5, on: screen)
                    } else {
                        Toast.shared.show("Copied to clipboard", detail: "\(words) words", duration: 2.5, on: screen)
                    }
                    Sounds.done()
                } catch {
                    Toast.shared.show("Copy failed", detail: error.localizedDescription, success: false, duration: 4, on: screen)
                    Sounds.error()
                }
            }
            self.session = nil
            self.phase = .idle
        }
    }

    /// Keeps the audio and text in the capture folder (pruned like captures) in case the
    /// clipboard gets overwritten.
    private static func saveDictation(_ samples: [Float], text: String, in folder: URL) {
        do {
            guard !samples.isEmpty else {
                try FileManager.default.removeItem(at: folder)
                return
            }
            if !text.isEmpty {
                try text.write(to: folder.appendingPathComponent("transcript.txt"), atomically: true, encoding: .utf8)
            }
        } catch {
            Log.app.error("Saving dictation failed: \(error.localizedDescription, privacy: .public)")
        }
        guard !samples.isEmpty else { return }
        Task.detached(priority: .utility) {
            do {
                try AudioRecorder.writeM4A(samples, to: folder.appendingPathComponent("audio.m4a"))
            } catch {
                Log.audio.error("Writing audio failed: \(error.localizedDescription, privacy: .public)")
            }
        }
    }

    private func recordClick() {
        guard phase == .walkthrough, let session else { return }
        let p = NSEvent.mouseLocation
        guard let d = session.display(atGlobal: p), recorders[d.index] != nil else { return }
        session.clicks.append(ClickEvent(time: session.now(), location: Screens.canvasPoint(fromGlobal: p, on: d.screen), screen: d.index))
    }

    private func startTicker() {
        ticker?.invalidate()
        let timer = Timer(timeInterval: 1.0 / 20, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
        RunLoop.main.add(timer, forMode: .common)
        ticker = timer
    }

    private func tick() {
        guard let session else { return }
        hudModel.elapsed = session.now()
        guard phase == .walkthrough else { return }

        // Follow the cursor across displays: timeline for keyframe focus, HUD placement.
        if let d = session.display(atGlobal: NSEvent.mouseLocation), d.index != lastCursorDisplay {
            lastCursorDisplay = d.index
            session.cursorTimeline.append((session.now(), d.index))
            if recorders[d.index] != nil { showHUD(on: d.screen) }
        }

        let held = settings.drawModifier.matches(CGEventSource.flagsState(.combinedSessionState))
        let tracking = canvases.values.contains { $0.isTracking }
        let drawing = hudModel.drawToggled || held || tracking
        if drawing != hudModel.drawing {
            hudModel.drawing = drawing
            for panel in overlays.values { panel.ignoresMouseEvents = !drawing }
        }
        if session.now() >= Double(settings.maxWalkthroughMinutes * 60) {
            Toast.shared.show("Maximum recording length reached", busy: false, duration: 2)
            finish()
        }
    }

    // MARK: Finish / cancel

    func finish() {
        guard let session, phase.isCapturing else { return }
        if session.mode == .dictation {
            finishDictation(session)
            return
        }
        // Nothing captured yet (recording starting, first screenshot pending).
        if (phase == .walkthrough || phase == .snapshot) && hudModel.starting { return }
        phase = .processing
        let mode = session.mode
        let duration = session.now()
        let audioResult = audio?.stop()
        audio = nil
        let recorders = self.recorders
        self.recorders = [:]
        let screen = hudScreen
        teardownUI()
        if mode == .snapshot { previousApp?.activate() }
        Sounds.stop()

        beginProcessingToast("Processing capture…", on: screen)

        Task {
            var recordings: [Int: RecordingResult] = [:]
            await withTaskGroup(of: StoppedRecording.self) { group in
                for (index, recorder) in recorders {
                    group.addTask { StoppedRecording(index: index, result: await recorder.stop()) }
                }
                for await stopped in group { recordings[stopped.index] = stopped.result }
            }
            let input = CaptureInput(
                mode: mode, folder: session.folder, startDate: session.startDate, t0: session.t0,
                duration: duration, annotations: session.annotations, clicks: session.clicks,
                shots: session.shots, displays: session.displays.map(\.info), audio: audioResult,
                recordings: recordings, cursorTimeline: session.cursorTimeline,
                model: self.settings.model.rawValue, language: self.settings.language, vocabulary: self.settings.vocabulary,
                maxFrames: self.settings.maxFrames, longEdge: self.settings.imageLongEdge,
                detailCrops: self.settings.detailCrops, keepOriginals: self.settings.keepOriginals,
                fadeSeconds: self.settings.fadeSeconds
            )
            do {
                let capture = try await CaptureProcessor.process(input)
                // Listed first, so a failed clipboard write can be redone from the menu.
                self.recents.add(capture)
                try ClipboardWriter.write(capture, format: self.settings.clipboardFormat)
                self.endProcessingToast()
                var detail = capture.note ?? capture.summary
                if audioResult == nil { detail += " · no microphone access" }
                Toast.shared.show(
                    "Copied to clipboard", detail: detail,
                    success: capture.note == nil, duration: capture.note == nil ? 2.8 : 5, on: screen
                )
                Sounds.done()
            } catch {
                Log.app.error("Processing failed: \(error.localizedDescription, privacy: .public)")
                self.endProcessingToast()
                Toast.shared.show("Capture failed", detail: error.localizedDescription, success: false, duration: 5, on: screen)
                Sounds.error()
            }
            self.session = nil
            self.phase = .idle
        }
    }

    func cancel() {
        guard let session, phase.isCapturing else { return }
        let mode = session.mode
        _ = audio?.stop()
        audio = nil
        let recorders = Array(self.recorders.values)
        self.recorders = [:]
        teardownUI()
        if mode == .snapshot { previousApp?.activate() }
        self.session = nil
        phase = .idle
        let folder = session.folder
        Task {
            for r in recorders { _ = await r.stop() }
            do {
                try FileManager.default.removeItem(at: folder)
            } catch {
                Log.app.error("Removing cancelled capture failed: \(error.localizedDescription, privacy: .public)")
            }
        }
        Toast.shared.show("Capture cancelled", success: false, duration: 1.5)
    }

    private func abort(_ message: String, detail: String) {
        cancel()
        Toast.shared.show(message, detail: detail, success: false, duration: 5)
        Sounds.error()
    }

    private func closeOverlays() {
        for canvas in canvases.values { canvas.stop() }
        for panel in overlays.values { panel.orderOut(nil) }
        overlays = [:]
        canvases = [:]
    }

    private func teardownUI() {
        ticker?.invalidate()
        ticker = nil
        if let clickMonitor { NSEvent.removeMonitor(clickMonitor) }
        clickMonitor = nil
        closeOverlays()
        hud?.orderOut(nil)
        lastCursorDisplay = nil
    }

    // MARK: Canvas delegate

    func canvas(_ canvas: AnnotationCanvasView, didFinish annotation: Annotation) {
        guard let session else { return }
        var a = annotation
        a.id = session.nextID
        if !a.isDrawing { a.number = session.nextMarkNumber }
        session.annotations.append(a)
        canvas.add(a)
        hudModel.annotationCount = session.markCount
    }

    func canvasRequestsFinish() { finish() }
    func canvasRequestsCancel() { cancel() }
    func canvasRequestsNextShot() { hideForNextShot() }
    func canvasRequestsUndo() { undo() }
    func canvas(_ canvas: AnnotationCanvasView, didSelect tool: AnnotationTool) { select(tool: tool) }
    func canvas(_ canvas: AnnotationCanvasView, didSelectColor index: Int) { select(colorIndex: index) }

    /// Removes the most recent annotation that is still visible on any display.
    private func undo() {
        guard let session else { return }
        var visible: [Int: AnnotationCanvasView] = [:]
        for canvas in canvases.values {
            for a in canvas.annotations { visible[a.id] = canvas }
        }
        guard let last = session.annotations.filter({ visible[$0.id] != nil }).max(by: { $0.id < $1.id }) else { return }
        session.annotations.removeAll { $0.id == last.id }
        visible[last.id]?.remove(id: last.id)
        hudModel.annotationCount = session.markCount
    }

    private func select(tool: AnnotationTool) {
        hudModel.tool = tool
        for canvas in canvases.values { canvas.tool = tool }
    }

    private func select(colorIndex: Int) {
        hudModel.colorIndex = colorIndex
        for canvas in canvases.values { canvas.colorIndex = colorIndex }
    }

    // MARK: Helpers

    private func makeSession(_ mode: CaptureMode) -> CaptureSession? {
        guard !NSScreen.screens.isEmpty else { return nil }
        do {
            let s = CaptureSession(mode: mode, folder: try Paths.newCaptureFolder())
            session = s
            return s
        } catch {
            Toast.shared.show("Cannot create capture folder", detail: error.localizedDescription, success: false, duration: 4)
            return nil
        }
    }

    /// Shows a busy toast that follows the model status, so a first capture waiting for
    /// the model download shows progress instead of looking stuck.
    private func beginProcessingToast(_ title: String, on screen: NSScreen?) {
        processingToast = (title, screen)
        showProcessingToast()
        modelStatusWatch = Transcriber.shared.$status
            .removeDuplicates()
            .throttle(for: .milliseconds(500), scheduler: RunLoop.main, latest: true)
            .sink { [weak self] _ in MainActor.assumeIsolated { self?.showProcessingToast() } }
    }

    private func showProcessingToast() {
        guard phase == .processing, let toast = processingToast else { return }
        let status = Transcriber.shared.status
        let detail = status.isReady ? "Transcribing and composing" : "Waiting for the model: \(status.title)"
        Toast.shared.show(toast.title, detail: detail, busy: true, duration: nil, on: toast.screen)
    }

    private func endProcessingToast() {
        modelStatusWatch = nil
        processingToast = nil
    }

    private func configure(_ canvas: AnnotationCanvasView, shot: Int, screen: Int, fade: Double?) {
        canvas.delegate = self
        canvas.tool = hudModel.tool
        canvas.colorIndex = hudModel.colorIndex
        canvas.shot = shot
        canvas.screen = screen
        canvas.fadeAfter = fade
        let session = self.session
        canvas.clock = { session?.now() ?? 0 }
    }

    private func rememberFrontmostApp() {
        let front = NSWorkspace.shared.frontmostApplication
        previousApp = front?.processIdentifier == ProcessInfo.processInfo.processIdentifier ? nil : front
    }

    private func resetHUD(mode: CaptureMode) {
        hudModel.mode = mode
        hudModel.elapsed = 0
        hudModel.level = 0
        hudModel.drawing = false
        hudModel.drawToggled = false
        hudModel.navigating = false
        hudModel.shotCount = mode == .snapshot ? 0 : 1
        hudModel.annotationCount = 0
        hudModel.starting = true
        hudModel.micAvailable = Permissions.microphoneStatus != .denied && Permissions.microphoneStatus != .restricted
        startTicker()
    }

    private func showHUD(on screen: NSScreen) {
        if hud == nil {
            let panel = FloatingPanel()
            panel.setRoot(HUDView(
                model: hudModel,
                snapshotKey: settings.hotkey(for: .snapshot).display,
                finishKey: settings.hotkey(for: .finish).display,
                drawModifier: settings.drawModifier.symbol
            ))
            hud = panel
        }
        hudScreen = screen
        hud?.refit(on: screen)
        hud?.orderFrontRegardless()
        refitHUD()
    }

    private func refitHUD() {
        DispatchQueue.main.async { [weak self] in
            guard let self, let hud = self.hud, let screen = self.hudScreen else { return }
            hud.refit(on: screen)
        }
    }

    private func startAudio(for session: CaptureSession) {
        func begin() {
            guard self.session === session, phase.isCapturing else { return }
            let recorder = AudioRecorder()
            recorder.onLevel = { [weak self] level in self?.hudModel.level = level }
            do {
                try recorder.start()
                audio = recorder
                hudModel.micAvailable = true
                if session.mode == .dictation {
                    hudModel.starting = false
                    Sounds.start()
                }
            } catch {
                Log.audio.error("Microphone start failed: \(error.localizedDescription, privacy: .public)")
                hudModel.micAvailable = false
            }
        }
        switch Permissions.microphoneStatus {
        case .authorized:
            begin()
        case .notDetermined:
            Permissions.requestMicrophone { granted in
                if granted { begin() } else { self.hudModel.micAvailable = false }
            }
        default:
            hudModel.micAvailable = false
        }
    }
}
