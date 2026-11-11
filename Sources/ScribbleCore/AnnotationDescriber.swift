import CoreGraphics
import Foundation

/// Produces the textual reference for an annotation, e.g.
/// `#3 circled (412,230)-(640,310) in frame 2`, or for a burst of pen strokes
/// `drawing (4 strokes) over (100,80)-(420,300) in frame 1`.
public enum AnnotationDescriber {
    public static func describe(_ a: Annotation, mapper: CoordinateMapper, frame: Int?) -> String {
        let body: String
        switch a.shape {
        case .pin:
            body = "pin at \(pt(mapper.map(a.points.first ?? .zero)))"
        case .arrow:
            let from = a.points.first ?? .zero
            let to = a.points.last ?? from
            body = "arrow from \(pt(mapper.map(from))) pointing at \(pt(mapper.map(to)))"
        case .ellipse:
            body = "circled \(rect(mapper.map(a.bounds)))"
        case .box:
            body = "box around \(rect(mapper.map(a.bounds)))"
        case .highlight:
            body = "highlighted \(rect(mapper.map(a.bounds)))"
        case .freehand:
            body = "drawing over \(rect(mapper.map(a.bounds)))"
        }
        return label(body, number: a.number, frame: frame)
    }

    /// One reference for a burst of pen strokes (see `drawingBursts`); no number.
    public static func describeDrawing(_ strokes: [Annotation], mapper: CoordinateMapper, frame: Int?) -> String {
        let bounds = strokes.map(\.bounds).reduce(CGRect.null) { $0.union($1) }
        let count = strokes.count > 1 ? " (\(strokes.count) strokes)" : ""
        return label("drawing\(count) over \(rect(mapper.map(bounds)))", number: nil, frame: frame)
    }

    /// Groups pen strokes into bursts: consecutive strokes on the same frame that start
    /// within `maxGap` seconds of the previous one ending. Strokes without a frame are dropped.
    public static func drawingBursts(_ strokes: [Annotation], frameOf: (Annotation) -> Int?, maxGap: Double = 1.5) -> [(frame: Int, strokes: [Annotation])] {
        var bursts: [(frame: Int, strokes: [Annotation])] = []
        for a in strokes.sorted(by: { $0.startTime < $1.startTime }) {
            guard let frame = frameOf(a) else { continue }
            if let last = bursts.last, last.frame == frame, let prev = last.strokes.last, a.startTime - prev.endTime <= maxGap {
                bursts[bursts.count - 1].strokes.append(a)
            } else {
                bursts.append((frame, [a]))
            }
        }
        return bursts
    }

    static func label(_ body: String, number: Int?, frame: Int?) -> String {
        var text = number.map { "#\($0) \(body)" } ?? body
        if let frame { text += " in frame \(frame)" }
        return text
    }

    public static func describeClick(_ c: ClickEvent, mapper: CoordinateMapper) -> String {
        "click at \(pt(mapper.map(c.location)))"
    }

    static func pt(_ p: (x: Int, y: Int)) -> String { "(\(p.x),\(p.y))" }

    static func rect(_ r: (x1: Int, y1: Int, x2: Int, y2: Int)) -> String {
        "(\(r.x1),\(r.y1))-(\(r.x2),\(r.y2))"
    }
}
