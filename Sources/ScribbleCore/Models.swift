import CoreGraphics
import Foundation

public enum CaptureMode: String, Codable, Sendable {
    case snapshot
    case walkthrough
    /// Voice only: the transcript goes to the clipboard as plain text, no bundle markdown.
    case dictation

    public var title: String {
        switch self {
        case .snapshot: return "Snapshot"
        case .walkthrough: return "Walkthrough"
        case .dictation: return "Dictation"
        }
    }
}

public enum AnnotationTool: String, Codable, CaseIterable, Sendable {
    case pen, arrow, rectangle, ellipse, highlighter, pin

    public var title: String {
        switch self {
        case .pen: return "Pen"
        case .arrow: return "Arrow"
        case .rectangle: return "Box"
        case .ellipse: return "Ellipse"
        case .highlighter: return "Highlighter"
        case .pin: return "Pin"
        }
    }

    /// Single-key shortcut used in Snapshot mode.
    public var shortcut: Character {
        switch self {
        case .pen: return "p"
        case .arrow: return "a"
        case .rectangle: return "r"
        case .ellipse: return "o"
        case .highlighter: return "h"
        case .pin: return "n"
        }
    }

    public var defaultLineWidth: Double {
        switch self {
        case .pen, .arrow: return 4
        case .rectangle, .ellipse: return 3.5
        case .highlighter: return 20
        case .pin: return 0
        }
    }

    /// Tools whose geometry is defined by a start and an end point only.
    public var isTwoPoint: Bool {
        switch self {
        case .arrow, .rectangle, .ellipse: return true
        default: return false
        }
    }
}

/// Semantic shape of a finished annotation (what the agent is told it is).
public enum AnnotationShape: String, Codable, Sendable {
    case freehand, arrow, box, ellipse, highlight, pin
}

public struct RGBAColor: Codable, Equatable, Sendable {
    public var r: Double
    public var g: Double
    public var b: Double
    public var a: Double

    public init(r: Double, g: Double, b: Double, a: Double = 1) {
        self.r = r
        self.g = g
        self.b = b
        self.a = a
    }

    public static let red = RGBAColor(r: 1.0, g: 0.231, b: 0.188)
    public static let yellow = RGBAColor(r: 1.0, g: 0.8, b: 0.0)
    public static let green = RGBAColor(r: 0.204, g: 0.78, b: 0.349)
    public static let blue = RGBAColor(r: 0.039, g: 0.518, b: 1.0)
    public static let magenta = RGBAColor(r: 1.0, g: 0.176, b: 0.573)
    public static let white = RGBAColor(r: 1, g: 1, b: 1)

    public static let palette: [RGBAColor] = [.red, .yellow, .green, .blue, .magenta, .white]

    public var cgColor: CGColor { CGColor(srgbRed: r, green: g, blue: b, alpha: a) }

    public func withAlpha(_ alpha: Double) -> RGBAColor { RGBAColor(r: r, g: g, b: b, a: alpha) }

    /// Relative luminance, used to pick a readable badge text color.
    public var luminance: Double { 0.2126 * r + 0.7152 * g + 0.0722 * b }
}

/// One user annotation. Geometry is in canvas points (display points, origin top-left).
public struct Annotation: Codable, Identifiable, Equatable, Sendable {
    /// Unique within a capture (0 while in progress). Not shown; see `number`.
    public var id: Int
    /// Number shown as a badge and cited in the transcript (#1, #2, …). Pen strokes are
    /// drawings and stay unnumbered.
    public var number: Int?
    public var tool: AnnotationTool
    public var shape: AnnotationShape
    public var points: [CGPoint]
    public var color: RGBAColor
    public var lineWidth: Double
    /// Session time (seconds) when drawing started / finished.
    public var startTime: Double
    public var endTime: Double
    /// Snapshot shot index this annotation belongs to (always 0 in Walkthrough).
    public var shot: Int
    /// Index of the display (in the capture's display list) the annotation was drawn on.
    public var screen: Int

    public init(
        id: Int = 0,
        number: Int? = nil,
        tool: AnnotationTool,
        points: [CGPoint],
        color: RGBAColor,
        lineWidth: Double? = nil,
        startTime: Double,
        endTime: Double? = nil,
        shot: Int = 0,
        screen: Int = 0
    ) {
        self.id = id
        self.number = number
        self.tool = tool
        self.shape = ShapeClassifier.defaultShape(for: tool)
        self.points = points
        self.color = color
        self.lineWidth = lineWidth ?? tool.defaultLineWidth
        self.startTime = startTime
        self.endTime = endTime ?? startTime
        self.shot = shot
        self.screen = screen
    }

    public mutating func finalize(endTime: Double) {
        self.endTime = endTime
    }

    /// Pen strokes are freehand drawing: never reinterpreted as shapes, never numbered.
    public var isDrawing: Bool { tool == .pen }

    public static let pinRadius: Double = 13

    public var bounds: CGRect {
        guard let first = points.first else { return .zero }
        if tool == .pin {
            let r = Annotation.pinRadius
            return CGRect(x: first.x - r, y: first.y - r, width: 2 * r, height: 2 * r)
        }
        var minX = first.x, maxX = first.x, minY = first.y, maxY = first.y
        for p in points {
            minX = min(minX, p.x)
            maxX = max(maxX, p.x)
            minY = min(minY, p.y)
            maxY = max(maxY, p.y)
        }
        return CGRect(x: minX, y: minY, width: maxX - minX, height: maxY - minY)
    }
}

public struct ClickEvent: Codable, Equatable, Sendable {
    /// Session time in seconds.
    public var time: Double
    /// Canvas points, origin top-left of the captured display.
    public var location: CGPoint
    /// Index of the display (in the capture's display list) that was clicked.
    public var screen: Int

    public init(time: Double, location: CGPoint, screen: Int = 0) {
        self.time = time
        self.location = location
        self.screen = screen
    }
}

public struct Word: Codable, Equatable, Sendable {
    public var text: String
    /// Session time in seconds.
    public var start: Double
    public var end: Double

    public init(text: String, start: Double, end: Double) {
        self.text = text
        self.start = start
        self.end = end
    }
}

public struct SpeechSegment: Codable, Equatable, Sendable {
    public var text: String
    public var start: Double
    public var end: Double

    public init(text: String, start: Double, end: Double) {
        self.text = text
        self.start = start
        self.end = end
    }
}

/// Luminance thumbnail of one captured video frame, used for keyframe selection.
public struct FrameSample: Sendable {
    /// Session time in seconds.
    public var time: Double
    public var thumb: [UInt8]

    public init(time: Double, thumb: [UInt8]) {
        self.time = time
        self.thumb = thumb
    }
}
