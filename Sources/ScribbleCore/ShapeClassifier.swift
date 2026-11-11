import CoreGraphics
import Foundation

/// Maps tools to the shape the agent is told about, plus stroke geometry helpers. Pen
/// strokes are never reinterpreted: they stay freehand drawings.
public enum ShapeClassifier {
    public static func defaultShape(for tool: AnnotationTool) -> AnnotationShape {
        switch tool {
        case .pen: return .freehand
        case .arrow: return .arrow
        case .rectangle: return .box
        case .ellipse: return .ellipse
        case .highlighter: return .highlight
        case .pin: return .pin
        }
    }

    public static func pathLength(_ pts: [CGPoint]) -> Double {
        guard pts.count > 1 else { return 0 }
        var total = 0.0
        for i in 1..<pts.count {
            total += hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y)
        }
        return total
    }
}
