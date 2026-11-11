import CoreGraphics
import Foundation

/// Maps canvas points (display points, origin top-left) to output image pixels.
public struct CoordinateMapper: Sendable, Equatable {
    public var canvasSize: CGSize
    public var imageSize: CGSize
    /// Canvas region that the image shows (full canvas unless it is a detail crop).
    public var canvasRegion: CGRect

    public init(canvasSize: CGSize, imageSize: CGSize, canvasRegion: CGRect? = nil) {
        self.canvasSize = canvasSize
        self.imageSize = imageSize
        self.canvasRegion = canvasRegion ?? CGRect(origin: .zero, size: canvasSize)
    }

    public var scaleX: Double { imageSize.width / max(canvasRegion.width, 1) }
    public var scaleY: Double { imageSize.height / max(canvasRegion.height, 1) }

    public func map(_ p: CGPoint) -> (x: Int, y: Int) {
        let x = ((p.x - canvasRegion.minX) * scaleX).rounded()
        let y = ((p.y - canvasRegion.minY) * scaleY).rounded()
        return (
            Int(min(max(x, 0), imageSize.width - 1)),
            Int(min(max(y, 0), imageSize.height - 1))
        )
    }

    public func map(_ r: CGRect) -> (x1: Int, y1: Int, x2: Int, y2: Int) {
        let a = map(CGPoint(x: r.minX, y: r.minY))
        let b = map(CGPoint(x: r.maxX, y: r.maxY))
        return (a.x, a.y, b.x, b.y)
    }

    /// `(x1,y1)-(x2,y2)` in image pixels.
    public func describe(_ r: CGRect) -> String {
        let m = map(r)
        return "(\(m.x1),\(m.y1))-(\(m.x2),\(m.y2))"
    }
}

public enum ImageSizing {
    /// Size that fits `size` into `longEdge` pixels on its longest side (never upscales).
    public static func fit(_ size: CGSize, longEdge: Int) -> CGSize {
        let longest = max(size.width, size.height)
        guard longest > CGFloat(longEdge), longest > 0 else {
            return CGSize(width: size.width.rounded(), height: size.height.rounded())
        }
        let s = CGFloat(longEdge) / longest
        return CGSize(width: max(1, (size.width * s).rounded()), height: max(1, (size.height * s).rounded()))
    }
}

/// Plans "detail" crops around annotations for very large displays, where a downscaled
/// full frame would make UI text illegible.
public enum CropPlanner {
    public struct Config: Sendable {
        /// Only crop when the full frame ends up with fewer output pixels per canvas point.
        public var minPixelsPerPoint: Double = 0.8
        public var padding: Double = 100
        public var minSize = CGSize(width: 800, height: 500)
        /// Skip the crop if it would cover this much of the canvas in both dimensions anyway.
        public var maxCoverage: Double = 0.7

        public init() {}
    }

    /// Returns the canvas rect to crop, or nil if no crop is worthwhile.
    public static func detailRect(
        around regions: [CGRect],
        canvasSize: CGSize,
        fullFramePixelWidth: Double,
        config: Config = Config()
    ) -> CGRect? {
        guard !regions.isEmpty, canvasSize.width > 0, canvasSize.height > 0 else { return nil }
        guard fullFramePixelWidth / canvasSize.width < config.minPixelsPerPoint else { return nil }

        var union = regions[0]
        for r in regions.dropFirst() { union = union.union(r) }
        var rect = union.insetBy(dx: -config.padding, dy: -config.padding)

        let w = min(max(rect.width, config.minSize.width), canvasSize.width)
        let h = min(max(rect.height, config.minSize.height), canvasSize.height)
        rect = CGRect(x: rect.midX - w / 2, y: rect.midY - h / 2, width: w, height: h)

        // Shift back inside the canvas.
        if rect.minX < 0 { rect.origin.x = 0 }
        if rect.minY < 0 { rect.origin.y = 0 }
        if rect.maxX > canvasSize.width { rect.origin.x = canvasSize.width - rect.width }
        if rect.maxY > canvasSize.height { rect.origin.y = canvasSize.height - rect.height }
        rect = rect.integral.intersection(CGRect(origin: .zero, size: canvasSize))

        if rect.width >= config.maxCoverage * canvasSize.width && rect.height >= config.maxCoverage * canvasSize.height {
            return nil
        }
        return rect
    }
}
