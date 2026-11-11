import AppKit
import Foundation
import VoiceAnnotateCore

/// A display taking part in a capture (index = position in the capture's display list).
struct DisplayInfo {
    var index: Int
    var name: String
    var canvasSize: CGSize
}

struct ShotData {
    var image: CGImage
    /// Session time of the screenshot.
    var time: Double
    var canvasSize: CGSize
    var display: Int
    /// Snapshot round (0 = first shot, 1 = after the first "next shot", …).
    var round: Int
    /// The display the cursor was on when this round was captured.
    var primary: Bool
}

/// Everything needed to turn a finished capture into a bundle.
struct CaptureInput {
    var mode: CaptureMode
    var folder: URL
    var startDate: Date
    var t0: Double
    var duration: Double
    var annotations: [Annotation]
    var clicks: [ClickEvent]
    var shots: [ShotData]
    var displays: [DisplayInfo]
    var audio: AudioResult?
    /// Walkthrough recordings by display index.
    var recordings: [Int: RecordingResult]
    /// (session time, display index) whenever the cursor moved to another display.
    var cursorTimeline: [(time: Double, display: Int)]

    var model: String
    var language: TranscriptionLanguage
    var vocabulary: String
    var maxFrames: Int
    var longEdge: Int
    var detailCrops: Bool
    var keepOriginals: Bool
    var fadeSeconds: Double
}

struct RecentCapture: Codable, Identifiable, Equatable {
    var folder: String
    var date: Date
    var mode: CaptureMode
    var duration: Double
    var markdownPath: String
    /// All images in paste order (frames with their detail crops).
    var imagePaths: [String]
    /// Main frames only (no detail crops).
    var framePaths: [String]
    var wordCount: Int
    var annotationCount: Int
    var note: String?

    var id: String { folder }
    var folderURL: URL { URL(fileURLWithPath: folder, isDirectory: true) }
    var markdownURL: URL { URL(fileURLWithPath: markdownPath) }
    var imageURLs: [URL] { imagePaths.map { URL(fileURLWithPath: $0) } }
    var frameURLs: [URL] { framePaths.map { URL(fileURLWithPath: $0) } }

    var summary: String {
        var parts = ["\(framePaths.count) \(framePaths.count == 1 ? "frame" : "frames")"]
        if annotationCount > 0 { parts.append("\(annotationCount) \(annotationCount == 1 ? "annotation" : "annotations")") }
        parts.append("\(wordCount) words")
        return parts.joined(separator: " · ")
    }
}

private struct BuiltFrame {
    var ref: FrameRef
    var mapper: CoordinateMapper
    var reasons: [String]
    var annotationIDs: [Int]
    var imagePaths: [String]
    var display: Int
    /// Snapshot: index into `CaptureInput.shots`.
    var shotIndex: Int?
}

enum CaptureProcessor {
    static func process(_ input: CaptureInput) async throws -> RecentCapture {
        let folder = input.folder
        var note: String?

        // 1. Audio → transcript (session times).
        var words: [Word] = []
        var segments: [SpeechSegment] = []
        var audioStart = 0.0
        var audioURL: URL?
        if let audio = input.audio, !audio.samples.isEmpty {
            audioStart = (audio.firstSampleHostTime ?? input.t0) - input.t0
            let samples = audio.samples
            let url = folder.appendingPathComponent("audio.m4a")
            let writeTask = Task.detached(priority: .utility) { () -> Bool in
                do {
                    try AudioRecorder.writeM4A(samples, to: url)
                    return true
                } catch {
                    Log.audio.error("Writing audio failed: \(error.localizedDescription, privacy: .public)")
                    return false
                }
            }
            do {
                let out = try await Transcriber.shared.transcribe(samples, model: input.model, language: input.language, vocabulary: input.vocabulary)
                note = out.warning
                words = out.words.map { Word(text: $0.text, start: $0.start + audioStart, end: $0.end + audioStart) }
                segments = out.segments.map { SpeechSegment(text: $0.text, start: $0.start + audioStart, end: $0.end + audioStart) }
            } catch {
                Log.transcribe.error("Transcription failed: \(error.localizedDescription, privacy: .public)")
                note = "Transcription failed: \(error.localizedDescription)"
            }
            if await writeTask.value { audioURL = url }
        }

        // 2. Frames.
        var frames: [BuiltFrame] = []
        do {
            switch input.mode {
            case .snapshot:
                frames = try await Task.detached(priority: .userInitiated) { try buildSnapshotFrames(input) }.value
            case .walkthrough:
                frames = try await buildWalkthroughFrames(input, speechStarts: segments.map(\.start))
            case .dictation:
                break
            }
        } catch {
            // Keep the transcript: capture.md is still written, without frames.
            Log.capture.error("Building frames failed: \(error.localizedDescription, privacy: .public)")
            note = [note, "Frames failed: \(error.localizedDescription)"].compactMap { $0 }.joined(separator: " · ")
        }
        let multiDisplay = Set(frames.map(\.display)).count > 1
        if multiDisplay {
            for i in frames.indices {
                frames[i].ref.display = input.displays.first { $0.index == frames[i].display }?.name
            }
        }

        // 3. Annotation / click markers.
        var markers: [Marker] = []
        var annotationLines: [(time: Double, text: String)] = []
        for a in input.annotations where !a.isDrawing {
            guard let frameIndex = frameIndex(for: a, mode: input.mode, frames: frames) else { continue }
            let f = frames[frameIndex]
            let text = AnnotationDescriber.describe(a, mapper: f.mapper, frame: f.ref.number)
            markers.append(Marker(time: a.startTime, text: text))
            annotationLines.append((a.startTime, "\(text) · \(Timecode.clock(a.startTime))"))
        }
        // Pen strokes are referenced per burst (one reference for a sketch drawn in one go).
        let bursts = AnnotationDescriber.drawingBursts(input.annotations.filter(\.isDrawing)) {
            frameIndex(for: $0, mode: input.mode, frames: frames)
        }
        for burst in bursts {
            let f = frames[burst.frame]
            let text = AnnotationDescriber.describeDrawing(burst.strokes, mapper: f.mapper, frame: f.ref.number)
            let start = burst.strokes[0].startTime, end = burst.strokes[burst.strokes.count - 1].endTime
            markers.append(Marker(time: start, text: text))
            let span = Timecode.clock(start) == Timecode.clock(end) ? Timecode.clock(start) : "\(Timecode.clock(start))–\(Timecode.clock(end))"
            annotationLines.append((start, "\(text) · \(span)"))
        }
        var hasClickMarkers = false
        if input.mode == .walkthrough {
            for c in input.clicks {
                let onDisplay = frames.filter { $0.display == c.screen }
                guard let f = onDisplay.last(where: { $0.ref.time <= c.time + 0.6 }) ?? onDisplay.first else { continue }
                var text = AnnotationDescriber.describeClick(c, mapper: f.mapper)
                if multiDisplay { text += " in frame \(f.ref.number)" }
                markers.append(Marker(time: c.time, text: text))
                hasClickMarkers = true
            }
        }

        // 4. Markdown + events.
        let doc = CaptureDocument(
            mode: input.mode, startDate: input.startDate, duration: input.duration, folder: folder.path,
            frames: frames.map(\.ref), words: words, markers: markers,
            annotationLines: annotationLines.sorted { $0.time < $1.time }.map(\.text),
            hasClicks: hasClickMarkers
        )
        var markdown = MarkdownComposer.compose(doc)
        if let note { markdown += "\n_\(note)_\n" }
        let markdownURL = folder.appendingPathComponent("capture.md")
        try markdown.write(to: markdownURL, atomically: true, encoding: .utf8)

        let log = EventsLog(
            mode: input.mode, startDate: input.startDate, duration: input.duration,
            canvasSize: input.displays.first { $0.index == frames.first?.display }?.canvasSize ?? input.displays.first?.canvasSize ?? .zero,
            displays: input.displays.map { EventsLog.Display(index: $0.index, name: $0.name, canvasSize: $0.canvasSize) },
            frames: frames.map {
                EventsLog.Frame(
                    number: $0.ref.number, time: $0.ref.time, file: ($0.ref.path as NSString).lastPathComponent,
                    width: Int($0.ref.pixelSize.width), height: Int($0.ref.pixelSize.height),
                    reasons: $0.reasons, annotationIDs: $0.annotationIDs,
                    detailFile: $0.ref.detail.map { ($0.path as NSString).lastPathComponent },
                    display: $0.display
                )
            },
            words: words, annotations: input.annotations, clicks: input.clicks
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        try encoder.encode(log).write(to: folder.appendingPathComponent("events.json"), options: .atomic)

        // 5. Walkthrough: mux each display's video with the audio in the background.
        if input.mode == .walkthrough {
            let recorded = input.recordings.filter { $0.value.videoURL != nil && $0.value.firstFrameHostTime != nil }
            for (display, rec) in recorded {
                guard let video = rec.videoURL, let firstHost = rec.firstFrameHostTime else { continue }
                let videoStart = firstHost - input.t0
                let name = recorded.count == 1 ? "recording.mp4" : "recording_display\(display + 1).mp4"
                let output = folder.appendingPathComponent(name)
                let audio = audioURL
                Task.detached(priority: .utility) {
                    do {
                        if let audio {
                            try await VideoTools.mux(video: video, audio: audio, audioOffset: audioStart - videoStart, output: output)
                            try FileManager.default.removeItem(at: video)
                        } else {
                            try FileManager.default.moveItem(at: video, to: output)
                        }
                    } catch {
                        Log.capture.error("Muxing failed: \(error.localizedDescription, privacy: .public)")
                    }
                }
            }
        }

        return RecentCapture(
            folder: folder.path, date: input.startDate, mode: input.mode, duration: input.duration,
            markdownPath: markdownURL.path, imagePaths: frames.flatMap(\.imagePaths), framePaths: frames.map(\.ref.path),
            wordCount: words.count, annotationCount: annotationLines.count, note: note
        )
    }

    private static func frameIndex(for a: Annotation, mode: CaptureMode, frames: [BuiltFrame]) -> Int? {
        if mode == .snapshot {
            return frames.firstIndex { $0.shotIndex == a.shot }
        }
        if let i = frames.firstIndex(where: { $0.annotationIDs.contains(a.id) }) { return i }
        let onDisplay = frames.indices.filter { frames[$0].display == a.screen }
        return onDisplay.first { frames[$0].ref.time >= a.endTime } ?? onDisplay.last
    }

    /// Canvas region an annotation occupies including its badge.
    private static func extent(of a: Annotation) -> CGRect {
        var r = a.bounds.insetBy(dx: -a.lineWidth / 2, dy: -a.lineWidth / 2)
        if a.tool != .pin, a.number != nil {
            let c = AnnotationRenderer.badgeCenter(for: a)
            let br = Double(AnnotationRenderer.badgeRadius)
            r = r.union(CGRect(x: c.x - br, y: c.y - br, width: 2 * br, height: 2 * br))
        }
        return r
    }

    private static func frameName(_ n: Int, suffix: String = "") -> String {
        String(format: "frame_%02d%@.png", n, suffix)
    }

    /// Shots that become frames: the cursor's display of every round, plus any other display
    /// that was drawn on. Ordered by round, cursor display first.
    static func snapshotFrameOrder(_ shots: [ShotData], annotations: [Annotation]) -> [Int] {
        let annotated = Set(annotations.map(\.shot))
        return shots.indices
            .filter { shots[$0].primary || annotated.contains($0) }
            .sorted {
                let a = shots[$0], b = shots[$1]
                if a.round != b.round { return a.round < b.round }
                if a.primary != b.primary { return a.primary }
                return a.display < b.display
            }
    }

    private static func buildSnapshotFrames(_ input: CaptureInput) throws -> [BuiltFrame] {
        var frames: [BuiltFrame] = []
        for (n, shotIndex) in snapshotFrameOrder(input.shots, annotations: input.annotations).enumerated() {
            let shot = input.shots[shotIndex]
            let number = n + 1
            let anns = input.annotations.filter { $0.shot == shotIndex }
            let native = CGSize(width: shot.image.width, height: shot.image.height)
            let out = ImageSizing.fit(native, longEdge: input.longEdge)
            guard let image = ImageRendering.renderAnnotated(image: shot.image, canvasSize: shot.canvasSize, annotations: anns, outputSize: out) else {
                throw AppError("Rendering screenshot \(number) failed")
            }
            let url = input.folder.appendingPathComponent(frameName(number))
            try ImageRendering.writePNG(image, to: url)
            var paths = [url.path]

            if input.keepOriginals, let clean = ImageRendering.resize(shot.image, to: out) {
                try ImageRendering.writePNG(clean, to: input.folder.appendingPathComponent(frameName(number, suffix: "_clean")))
            }

            let mapper = CoordinateMapper(canvasSize: shot.canvasSize, imageSize: out)
            var detail: DetailRef?
            if input.detailCrops,
               let crop = CropPlanner.detailRect(around: anns.map(extent(of:)), canvasSize: shot.canvasSize, fullFramePixelWidth: out.width) {
                let nativeScale = native.width / shot.canvasSize.width
                let cropOut = ImageSizing.fit(CGSize(width: crop.width * nativeScale, height: crop.height * nativeScale), longEdge: input.longEdge)
                if let d = ImageRendering.renderAnnotated(image: shot.image, canvasSize: shot.canvasSize, annotations: anns, outputSize: cropOut, crop: crop) {
                    let durl = input.folder.appendingPathComponent(frameName(number, suffix: "_detail"))
                    try ImageRendering.writePNG(d, to: durl)
                    detail = DetailRef(path: durl.path, pixelSize: cropOut, regionInFrame: mapper.describe(crop))
                    paths.append(durl.path)
                }
            }
            frames.append(BuiltFrame(
                ref: FrameRef(number: number, time: shot.time, path: url.path, pixelSize: out, detail: detail),
                mapper: mapper, reasons: ["screenshot"], annotationIDs: anns.map(\.id), imagePaths: paths,
                display: shot.display, shotIndex: shotIndex
            ))
        }
        return frames
    }

    private static func buildWalkthroughFrames(_ input: CaptureInput, speechStarts: [Double]) async throws -> [BuiltFrame] {
        let recorded = input.recordings
            .filter { $0.value.videoURL != nil && $0.value.firstFrameHostTime != nil }
            .sorted { $0.key < $1.key }
        guard !recorded.isEmpty else { return [] }
        let multi = recorded.count > 1

        var config = KeyframeSelector.Config()
        config.maxFrames = input.maxFrames
        config.fadeDuration = input.fadeSeconds
        let intervals = KeyframeSelector.activeIntervals(
            timeline: input.cursorTimeline, displayCount: input.displays.count, duration: input.duration
        )
        let startDisplay = input.cursorTimeline.first?.display ?? recorded[0].key
        let endDisplay = input.cursorTimeline.last?.display ?? startDisplay

        var lists: [[KeyframeSelection]] = []
        for (display, rec) in recorded {
            let samples = rec.samples.map { FrameSample(time: $0.time - input.t0, thumb: $0.thumb) }
            let sel = KeyframeSelector.select(
                samples: samples,
                annotations: input.annotations.filter { $0.screen == display },
                clicks: input.clicks.filter { $0.screen == display }.map(\.time),
                speechStarts: speechStarts,
                duration: input.duration,
                activeIntervals: multi ? intervals[display] : nil,
                includeStart: !multi || display == startDisplay,
                includeEnd: !multi || display == endDisplay,
                config: config
            )
            lists.append(sel)
        }
        let budget = min(input.maxFrames, max(config.minFrames, Int((input.duration / config.secondsPerFrame).rounded(.up))))
        let merged = KeyframeSelector.merge(lists, maxFrames: budget)
        Log.capture.info("Selected \(merged.count) keyframes across \(recorded.count) display(s)")

        let annotationsByID = Dictionary(input.annotations.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        let displayIDs = recorded.map(\.key)

        return try await Task.detached(priority: .userInitiated) { () throws -> [BuiltFrame] in
            var frames: [BuiltFrame] = []
            for (listIndex, display) in displayIDs.enumerated() {
                guard let rec = input.recordings[display], let video = rec.videoURL, let firstHost = rec.firstFrameHostTime,
                      let info = input.displays.first(where: { $0.index == display }) else { continue }
                let videoStart = firstHost - input.t0
                // Frame numbers are chronological across displays.
                let items = merged.enumerated().filter { $0.element.display == listIndex }
                try await VideoTools.forEachFrame(in: video, at: items.map { $0.element.selection.time - videoStart }) { i, image in
                    let number = items[i].offset + 1
                    let sel = items[i].element.selection
                    let native = CGSize(width: image.width, height: image.height)
                    let out = ImageSizing.fit(native, longEdge: input.longEdge)
                    guard let full = ImageRendering.resize(image, to: out) else { throw AppError("Resizing frame \(number) failed") }
                    let url = input.folder.appendingPathComponent(frameName(number))
                    try ImageRendering.writePNG(full, to: url)
                    var paths = [url.path]

                    let mapper = CoordinateMapper(canvasSize: info.canvasSize, imageSize: out)
                    var detail: DetailRef?
                    let anns = sel.annotationIDs.compactMap { annotationsByID[$0] }
                    if input.detailCrops,
                       let crop = CropPlanner.detailRect(around: anns.map(extent(of:)), canvasSize: info.canvasSize, fullFramePixelWidth: out.width) {
                        let s = native.width / info.canvasSize.width
                        let pixelRect = CGRect(x: crop.minX * s, y: crop.minY * s, width: crop.width * s, height: crop.height * s)
                        let cropOut = ImageSizing.fit(pixelRect.size, longEdge: input.longEdge)
                        if let d = ImageRendering.resize(image, to: cropOut, sourceRect: pixelRect) {
                            let durl = input.folder.appendingPathComponent(frameName(number, suffix: "_detail"))
                            try ImageRendering.writePNG(d, to: durl)
                            detail = DetailRef(path: durl.path, pixelSize: cropOut, regionInFrame: mapper.describe(crop))
                            paths.append(durl.path)
                        }
                    }
                    frames.append(BuiltFrame(
                        ref: FrameRef(number: number, time: sel.time, path: url.path, pixelSize: out, detail: detail),
                        mapper: mapper, reasons: sel.reasons, annotationIDs: sel.annotationIDs, imagePaths: paths,
                        display: display, shotIndex: nil
                    ))
                }
            }
            return frames.sorted { $0.ref.number < $1.ref.number }
        }.value
    }
}
