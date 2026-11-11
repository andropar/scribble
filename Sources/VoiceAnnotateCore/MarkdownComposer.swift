import CoreGraphics
import Foundation

public struct DetailRef: Codable, Equatable, Sendable {
    public var path: String
    public var pixelSize: CGSize
    /// Region of the parent frame this crop shows, in the parent frame's pixel coordinates.
    public var regionInFrame: String

    public init(path: String, pixelSize: CGSize, regionInFrame: String) {
        self.path = path
        self.pixelSize = pixelSize
        self.regionInFrame = regionInFrame
    }
}

public struct FrameRef: Codable, Equatable, Sendable {
    public var number: Int
    /// Session time in seconds.
    public var time: Double
    public var path: String
    public var pixelSize: CGSize
    public var detail: DetailRef?
    /// Display name, set when the capture spans several displays.
    public var display: String?

    public init(number: Int, time: Double, path: String, pixelSize: CGSize, detail: DetailRef? = nil, display: String? = nil) {
        self.number = number
        self.time = time
        self.path = path
        self.pixelSize = pixelSize
        self.detail = detail
        self.display = display
    }
}

/// A bracketed inline reference in the transcript, e.g. an annotation or a click.
public struct Marker: Equatable, Sendable {
    public var time: Double
    public var text: String

    public init(time: Double, text: String) {
        self.time = time
        self.text = text
    }
}

public struct CaptureDocument: Sendable {
    public var mode: CaptureMode
    public var startDate: Date
    public var duration: Double
    public var folder: String
    public var frames: [FrameRef]
    public var words: [Word]
    public var markers: [Marker]
    /// One line per annotation for the summary list, e.g. "#1 circled … in frame 2 · 00:06".
    public var annotationLines: [String]
    public var hasClicks: Bool

    public init(
        mode: CaptureMode, startDate: Date, duration: Double, folder: String, frames: [FrameRef],
        words: [Word], markers: [Marker], annotationLines: [String], hasClicks: Bool
    ) {
        self.mode = mode
        self.startDate = startDate
        self.duration = duration
        self.folder = folder
        self.frames = frames
        self.words = words
        self.markers = markers
        self.annotationLines = annotationLines
        self.hasClicks = hasClicks
    }
}

public struct TranscriptParagraph: Equatable, Sendable {
    public var time: Double
    /// Frames starting at this paragraph (several when displays were captured together).
    public var frames: [Int]
    public var text: String

    public var frame: Int? { frames.first }
}

public enum MarkdownComposer {
    /// Pause after which a new paragraph starts even without a new frame.
    public static let paragraphPause = 2.5

    public static func compose(_ doc: CaptureDocument) -> String {
        var out: [String] = []
        let dateFormatter = DateFormatter()
        dateFormatter.locale = Locale(identifier: "en_US_POSIX")
        dateFormatter.dateFormat = "yyyy-MM-dd HH:mm"

        out.append("# Screen capture · \(doc.mode.title) · \(Timecode.duration(doc.duration)) · \(dateFormatter.string(from: doc.startDate))")
        out.append("")
        switch doc.mode {
        case .snapshot:
            let shots = doc.frames.count == 1 ? "a screenshot" : "\(doc.frames.count) screenshots"
            out.append("The user took \(shots) and talked while drawing on it (voice-annotate).")
        case .walkthrough:
            out.append("The user recorded their screen while talking and using the computer (voice-annotate). The frames below are keyframes from that recording.")
        case .dictation:
            out.append("The user dictated this (voice-annotate).")
        }
        let displays = Set(doc.frames.compactMap(\.display))
        if displays.count > 1 {
            out.append("The user has several displays; each frame says which display it shows.")
        }
        var legend = "Marks in the images are the user's own: numbered ones (#1, #2, …) point at something, unnumbered pen strokes are freehand drawing. Each is referenced in the transcript at the moment it was drawn."
        if doc.hasClicks {
            legend += " Mouse clicks appear as [click at (x,y)]."
        }
        legend += " Coordinates are pixels in the referenced image, origin top-left."
        out.append(legend)
        out.append("")

        if doc.frames.isEmpty {
            out.append("_(no frames captured)_")
        } else {
            out.append(doc.mode == .snapshot ? "Screenshots:" : "Frames:")
            for f in doc.frames {
                let display = f.display.map { " · display \"\($0)\"" } ?? ""
                out.append("- frame \(f.number) · \(Timecode.clock(f.time))\(display) · \(Int(f.pixelSize.width))×\(Int(f.pixelSize.height)) · \(f.path)")
                if let d = f.detail {
                    out.append("  - detail of frame \(f.number), region \(d.regionInFrame), \(Int(d.pixelSize.width))×\(Int(d.pixelSize.height)): \(d.path)")
                }
            }
        }
        out.append("")
        out.append("## Transcript")
        out.append("")

        let paragraphs = transcriptParagraphs(frames: doc.frames, words: doc.words, markers: doc.markers)
        let hasSpeech = !doc.words.isEmpty
        if !hasSpeech {
            out.append("_(no speech detected)_")
            out.append("")
        }
        for p in paragraphs {
            var header = "**[\(Timecode.clock(p.time))"
            if p.frames.count == 1 {
                header += " · frame \(p.frames[0])"
            } else if !p.frames.isEmpty {
                header += " · frames " + p.frames.map(String.init).joined(separator: ", ")
            }
            header += "]**"
            out.append(p.text.isEmpty ? header : "\(header) \(p.text)")
            out.append("")
        }

        if !doc.annotationLines.isEmpty {
            out.append("## Annotations")
            out.append("")
            for line in doc.annotationLines {
                out.append("- \(line)")
            }
            out.append("")
        }
        out.append("Folder: \(doc.folder)")
        return out.joined(separator: "\n") + "\n"
    }

    /// Interleaves words, markers and frame boundaries by time into paragraphs.
    public static func transcriptParagraphs(frames: [FrameRef], words: [Word], markers: [Marker]) -> [TranscriptParagraph] {
        enum Event {
            case frame(FrameRef)
            case marker(Marker)
            case word(Word)
        }
        var events: [(time: Double, order: Int, event: Event)] = []
        let sortedFrames = frames.sorted { $0.time < $1.time }
        for (i, f) in sortedFrames.enumerated() {
            // Everything said before the first frame belongs to it.
            events.append((i == 0 ? -Double.infinity : f.time, 0, .frame(f)))
        }
        for m in markers { events.append((m.time, 1, .marker(m))) }
        for w in words { events.append((w.start, 2, .word(w))) }
        events.sort { $0.time != $1.time ? $0.time < $1.time : $0.order < $1.order }

        var paragraphs: [TranscriptParagraph] = []
        var current: TranscriptParagraph?
        var lastWordEnd: Double?

        func flush() {
            if var p = current {
                p.text = clean(p.text)
                paragraphs.append(p)
            }
            current = nil
        }

        for item in events {
            switch item.event {
            case let .frame(f):
                // Frames taken at the same moment (one per display) share a paragraph.
                if let c = current, !c.frames.isEmpty, c.text.isEmpty, abs(c.time - f.time) < 0.05 {
                    current?.frames.append(f.number)
                } else {
                    flush()
                    current = TranscriptParagraph(time: f.time, frames: [f.number], text: "")
                }
            case let .marker(m):
                if current == nil { current = TranscriptParagraph(time: m.time, frames: [], text: "") }
                current?.text += " [\(m.text)]"
            case let .word(w):
                let pause = lastWordEnd.map { w.start - $0 } ?? 0
                if current == nil || (pause > paragraphPause && !(current?.text.isEmpty ?? true)) {
                    flush()
                    current = TranscriptParagraph(time: w.start, frames: [], text: "")
                }
                let text = TranscriptText.spaced(w.text, after: current?.text ?? "")
                current?.text += text
                lastWordEnd = w.end
            }
        }
        flush()
        return paragraphs
    }

    static func clean(_ s: String) -> String {
        var t = s.trimmingCharacters(in: .whitespacesAndNewlines)
        while t.contains("  ") { t = t.replacingOccurrences(of: "  ", with: " ") }
        return t
    }
}

public enum Timecode {
    /// `mm:ss` (or `h:mm:ss`) for a position in the capture.
    public static func clock(_ seconds: Double) -> String {
        let s = max(0, Int(seconds.rounded(.down)))
        if s >= 3600 {
            return String(format: "%d:%02d:%02d", s / 3600, (s % 3600) / 60, s % 60)
        }
        return String(format: "%02d:%02d", s / 60, s % 60)
    }

    /// `m:ss` for a duration.
    public static func duration(_ seconds: Double) -> String {
        let s = max(0, Int(seconds.rounded()))
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}

/// Machine-readable timeline written next to `capture.md`.
public struct EventsLog: Codable, Sendable {
    public struct Frame: Codable, Sendable {
        public var number: Int
        public var time: Double
        public var file: String
        public var width: Int
        public var height: Int
        public var reasons: [String]
        public var annotationIDs: [Int]
        public var detailFile: String?
        public var display: Int

        public init(number: Int, time: Double, file: String, width: Int, height: Int, reasons: [String], annotationIDs: [Int], detailFile: String?, display: Int) {
            self.number = number
            self.time = time
            self.file = file
            self.width = width
            self.height = height
            self.reasons = reasons
            self.annotationIDs = annotationIDs
            self.detailFile = detailFile
            self.display = display
        }
    }

    public var version = 1
    public var mode: CaptureMode
    public var startDate: Date
    public var duration: Double
    /// Size of the captured display in points (the coordinate space of annotation geometry).
    public var canvasSize: CGSize
    public var displays: [Display]
    public var frames: [Frame]
    public var words: [Word]
    public var annotations: [Annotation]
    public var clicks: [ClickEvent]

    public struct Display: Codable, Sendable {
        public var index: Int
        public var name: String
        /// Display size in points: the coordinate space of annotation and click geometry.
        public var canvasSize: CGSize

        public init(index: Int, name: String, canvasSize: CGSize) {
            self.index = index
            self.name = name
            self.canvasSize = canvasSize
        }
    }

    public init(mode: CaptureMode, startDate: Date, duration: Double, canvasSize: CGSize, displays: [Display], frames: [Frame], words: [Word], annotations: [Annotation], clicks: [ClickEvent]) {
        self.mode = mode
        self.startDate = startDate
        self.duration = duration
        self.canvasSize = canvasSize
        self.displays = displays
        self.frames = frames
        self.words = words
        self.annotations = annotations
        self.clicks = clicks
    }
}
