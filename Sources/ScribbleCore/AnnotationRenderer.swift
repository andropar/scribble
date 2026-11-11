import CoreGraphics
import CoreText
import Foundation
import ImageIO
import UniformTypeIdentifiers

/// Draws annotations into a CGContext whose coordinate system is top-left origin (y down),
/// e.g. a flipped NSView or a bitmap context with a flip transform applied.
/// Shared by the live overlay and offline rendering so both look identical.
public enum AnnotationRenderer {
    public static let badgeRadius: CGFloat = 11

    /// - Parameters:
    ///   - scale: canvas points → context units.
    ///   - markScale: size of strokes and badges in context units per point; defaults to
    ///     `scale`. A larger value keeps marks legible in strongly downscaled images.
    ///   - canvasSize: if given, badges are kept inside this canvas (in canvas points).
    public static func draw(
        _ a: Annotation,
        in ctx: CGContext,
        scale s: CGFloat = 1,
        markScale: CGFloat? = nil,
        alpha: CGFloat = 1,
        showBadge: Bool = true,
        canvasSize: CGSize? = nil
    ) {
        guard alpha > 0, !a.points.isEmpty else { return }
        ctx.saveGState()
        defer { ctx.restoreGState() }
        ctx.setAlpha(alpha)
        ctx.setLineCap(.round)
        ctx.setLineJoin(.round)

        let m = markScale ?? s
        let pts = a.points.map { CGPoint(x: $0.x * s, y: $0.y * s) }
        let lw = CGFloat(a.lineWidth) * m
        let color = a.color.cgColor
        let shadow = CGColor(gray: 0, alpha: 0.45)

        switch a.tool {
        case .pen:
            ctx.setShadow(offset: CGSize(width: 0, height: 1 * m), blur: 3 * m, color: shadow)
            strokeSmooth(pts, in: ctx, width: lw, color: color)
        case .highlighter:
            ctx.setLineCap(.butt)
            strokeSmooth(pts, in: ctx, width: lw, color: a.color.withAlpha(0.38).cgColor)
        case .arrow:
            ctx.setShadow(offset: CGSize(width: 0, height: 1 * m), blur: 3 * m, color: shadow)
            drawArrow(from: pts[0], to: pts[pts.count - 1], in: ctx, width: lw, color: color)
        case .rectangle:
            ctx.setShadow(offset: CGSize(width: 0, height: 1 * m), blur: 3 * m, color: shadow)
            let r = rect(pts[0], pts[pts.count - 1])
            ctx.addPath(CGPath(roundedRect: r, cornerWidth: min(4 * m, r.width / 2), cornerHeight: min(4 * m, r.height / 2), transform: nil))
            ctx.setStrokeColor(color)
            ctx.setLineWidth(lw)
            ctx.strokePath()
        case .ellipse:
            ctx.setShadow(offset: CGSize(width: 0, height: 1 * m), blur: 3 * m, color: shadow)
            ctx.addEllipse(in: rect(pts[0], pts[pts.count - 1]))
            ctx.setStrokeColor(color)
            ctx.setLineWidth(lw)
            ctx.strokePath()
        case .pin:
            drawBadge(number: a.number ?? 0, center: pts[0], radius: CGFloat(Annotation.pinRadius) * m, color: a.color, in: ctx, scale: m)
            return
        }

        guard showBadge, let number = a.number else { return }
        var center = badgeCenter(for: a, markFactor: m / s)
        if let canvasSize {
            let r = (Double(badgeRadius) + 2) * Double(m / s)
            center.x = min(max(center.x, r), canvasSize.width - r)
            center.y = min(max(center.y, r), canvasSize.height - r)
        }
        ctx.setShadow(offset: .zero, blur: 0, color: nil)
        drawBadge(number: number, center: CGPoint(x: center.x * s, y: center.y * s), radius: badgeRadius * m, color: a.color, in: ctx, scale: m)
    }

    /// Badge position in canvas points: at the arrow's tail, else outside the top-left corner.
    /// `markFactor` = mark size relative to geometry (see `draw(markScale:)`).
    public static func badgeCenter(for a: Annotation, markFactor: CGFloat = 1) -> CGPoint {
        let offset = (Double(badgeRadius) + 4) * Double(markFactor)
        if a.tool == .arrow, let tail = a.points.first, let tip = a.points.last {
            let dx = tail.x - tip.x, dy = tail.y - tip.y
            let len = max(hypot(dx, dy), 0.001)
            return CGPoint(x: tail.x + dx / len * offset, y: tail.y + dy / len * offset)
        }
        let b = a.bounds
        let pad = a.tool == .highlighter ? a.lineWidth / 2 * Double(markFactor) : 0
        return CGPoint(x: b.minX - pad - offset * 0.7, y: b.minY - pad - offset * 0.7)
    }

    static func rect(_ a: CGPoint, _ b: CGPoint) -> CGRect {
        CGRect(x: min(a.x, b.x), y: min(a.y, b.y), width: abs(a.x - b.x), height: abs(a.y - b.y))
    }

    static func strokeSmooth(_ pts: [CGPoint], in ctx: CGContext, width: CGFloat, color: CGColor) {
        if pts.count == 1 {
            let p = pts[0]
            ctx.setFillColor(color)
            ctx.fillEllipse(in: CGRect(x: p.x - width / 2, y: p.y - width / 2, width: width, height: width))
            return
        }
        let path = CGMutablePath()
        path.move(to: pts[0])
        if pts.count == 2 {
            path.addLine(to: pts[1])
        } else {
            for i in 1..<(pts.count - 1) {
                let mid = CGPoint(x: (pts[i].x + pts[i + 1].x) / 2, y: (pts[i].y + pts[i + 1].y) / 2)
                path.addQuadCurve(to: mid, control: pts[i])
            }
            path.addLine(to: pts[pts.count - 1])
        }
        ctx.addPath(path)
        ctx.setStrokeColor(color)
        ctx.setLineWidth(width)
        ctx.strokePath()
    }

    static func drawArrow(from tail: CGPoint, to tip: CGPoint, in ctx: CGContext, width: CGFloat, color: CGColor) {
        let dx = tip.x - tail.x, dy = tip.y - tail.y
        let len = hypot(dx, dy)
        guard len > 0.5 else { return }
        let ux = dx / len, uy = dy / len
        let headLen = min(max(width * 4, 14), len * 0.6)
        let headHalf = headLen * 0.55
        let base = CGPoint(x: tip.x - ux * headLen, y: tip.y - uy * headLen)
        let lineEnd = CGPoint(x: tip.x - ux * headLen * 0.6, y: tip.y - uy * headLen * 0.6)

        ctx.setStrokeColor(color)
        ctx.setLineWidth(width)
        ctx.move(to: tail)
        ctx.addLine(to: lineEnd)
        ctx.strokePath()

        ctx.setFillColor(color)
        ctx.move(to: tip)
        ctx.addLine(to: CGPoint(x: base.x - uy * headHalf, y: base.y + ux * headHalf))
        ctx.addLine(to: CGPoint(x: base.x + uy * headHalf, y: base.y - ux * headHalf))
        ctx.closePath()
        ctx.fillPath()
    }

    static func drawBadge(number: Int, center: CGPoint, radius: CGFloat, color: RGBAColor, in ctx: CGContext, scale s: CGFloat) {
        ctx.saveGState()
        defer { ctx.restoreGState() }
        ctx.setShadow(offset: CGSize(width: 0, height: 1 * s), blur: 3 * s, color: CGColor(gray: 0, alpha: 0.5))
        let r = CGRect(x: center.x - radius, y: center.y - radius, width: 2 * radius, height: 2 * radius)
        ctx.setFillColor(color.cgColor)
        ctx.fillEllipse(in: r)
        ctx.setShadow(offset: .zero, blur: 0, color: nil)
        ctx.setStrokeColor(CGColor(gray: 1, alpha: 1))
        ctx.setLineWidth(2 * s)
        ctx.strokeEllipse(in: r.insetBy(dx: 1 * s, dy: 1 * s))
        guard number > 0 else { return }
        let textColor = color.luminance > 0.6 ? CGColor(gray: 0, alpha: 1) : CGColor(gray: 1, alpha: 1)
        let text = "\(number)"
        let fontSize = radius * (text.count > 1 ? 1.0 : 1.2)
        drawCenteredText(text, at: center, fontSize: fontSize, color: textColor, in: ctx)
    }

    /// Draws text centered at `c` in a y-down context.
    public static func drawCenteredText(_ text: String, at c: CGPoint, fontSize: CGFloat, color: CGColor, in ctx: CGContext) {
        let font = CTFontCreateUIFontForLanguage(.emphasizedSystem, fontSize, nil)
            ?? CTFontCreateWithName("Helvetica-Bold" as CFString, fontSize, nil)
        let attrs: [NSAttributedString.Key: Any] = [
            NSAttributedString.Key(kCTFontAttributeName as String): font,
            NSAttributedString.Key(kCTForegroundColorAttributeName as String): color,
        ]
        let line = CTLineCreateWithAttributedString(NSAttributedString(string: text, attributes: attrs))
        var ascent: CGFloat = 0, descent: CGFloat = 0, leading: CGFloat = 0
        let width = CGFloat(CTLineGetTypographicBounds(line, &ascent, &descent, &leading))
        ctx.saveGState()
        ctx.textMatrix = CGAffineTransform(scaleX: 1, y: -1)
        ctx.textPosition = CGPoint(x: c.x - width / 2, y: c.y + (ascent - descent) / 2)
        CTLineDraw(line, ctx)
        ctx.restoreGState()
    }
}

/// Offline image rendering: annotated screenshots, downscaling, crops, PNG output.
public enum ImageRendering {
    static let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!
    /// Minimum output pixels per point for strokes and badges.
    public static let minMarkScale: CGFloat = 1

    public static func makeContext(width: Int, height: Int) -> CGContext? {
        CGContext(
            data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
            space: colorSpace, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        )
    }

    /// Renders `image` (showing the whole canvas) plus annotations into an image of
    /// `outputSize`, optionally showing only `crop` (canvas points).
    public static func renderAnnotated(
        image: CGImage,
        canvasSize: CGSize,
        annotations: [Annotation],
        outputSize: CGSize,
        crop: CGRect? = nil
    ) -> CGImage? {
        let region = crop ?? CGRect(origin: .zero, size: canvasSize)
        let w = Int(outputSize.width), h = Int(outputSize.height)
        guard w > 0, h > 0, region.width > 0, let ctx = makeContext(width: w, height: h) else { return nil }
        let s = CGFloat(w) / region.width
        ctx.interpolationQuality = .high
        ctx.setFillColor(CGColor(gray: 0, alpha: 1))
        ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
        // Image drawn in the unflipped bitmap space.
        ctx.draw(image, in: CGRect(
            x: -region.minX * s,
            y: CGFloat(h) - (canvasSize.height - region.minY) * s,
            width: canvasSize.width * s,
            height: canvasSize.height * s
        ))
        // Annotations in a y-down space.
        ctx.translateBy(x: 0, y: CGFloat(h))
        ctx.scaleBy(x: 1, y: -1)
        ctx.translateBy(x: -region.minX * s, y: -region.minY * s)
        // Marks never shrink below their on-screen size, so badges stay readable when a
        // large display is downscaled.
        let markScale = max(s, minMarkScale)
        for a in annotations {
            AnnotationRenderer.draw(a, in: ctx, scale: s, markScale: markScale, canvasSize: canvasSize)
        }
        return ctx.makeImage()
    }

    /// High-quality resize of (a pixel region of) an image.
    public static func resize(_ image: CGImage, to size: CGSize, sourceRect: CGRect? = nil) -> CGImage? {
        let source: CGImage
        if let sourceRect {
            guard let cropped = image.cropping(to: sourceRect.integral) else { return nil }
            source = cropped
        } else {
            source = image
        }
        let w = Int(size.width), h = Int(size.height)
        if sourceRect == nil, source.width == w, source.height == h { return source }
        guard w > 0, h > 0, let ctx = makeContext(width: w, height: h) else { return nil }
        ctx.interpolationQuality = .high
        ctx.draw(source, in: CGRect(x: 0, y: 0, width: w, height: h))
        return ctx.makeImage()
    }

    public static func pngData(_ image: CGImage) -> Data? {
        let data = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(dest, image, nil)
        guard CGImageDestinationFinalize(dest) else { return nil }
        return data as Data
    }

    public static func writePNG(_ image: CGImage, to url: URL) throws {
        guard let data = pngData(image) else {
            throw NSError(domain: "Scribble", code: 1, userInfo: [NSLocalizedDescriptionKey: "PNG encoding failed"])
        }
        try data.write(to: url, options: .atomic)
    }
}
