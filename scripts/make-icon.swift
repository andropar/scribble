// Renders the app icon: a waveform with a circle drawn around it and a numbered badge, the
// way the app marks up a screenshot.
//
//   swift scripts/make-icon.swift <folder.iconset>          the macOS icon set
//   swift scripts/make-icon.swift --png <size> <file.png>   one image with the usual margin
//   swift scripts/make-icon.swift --png <size> <file.png> --bleed   … filling the canvas
//
// The PNGs checked in elsewhere (README, website, the Windows and Linux app, the video) are
// made with --png; `make icons` renders them all.
import AppKit

let ink = CGColor(srgbRed: 0.067, green: 0.067, blue: 0.075, alpha: 1)
let red = CGColor(srgbRed: 1.0, green: 0.231, blue: 0.188, alpha: 1)
let white = CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 1)

/// `bleed` fills the canvas; otherwise the tile keeps the margin macOS icons have.
func render(_ px: Int, bleed: Bool = false) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let ctx = NSGraphicsContext.current!.cgContext
    let s = CGFloat(px)
    let inset = bleed ? 0 : s * 0.1
    // The artwork is laid out on a 512 grid with the origin at the top left.
    let k = (s - 2 * inset) / 512
    ctx.translateBy(x: inset, y: s - inset)
    ctx.scaleBy(x: k, y: -k)

    ctx.addPath(CGPath(roundedRect: CGRect(x: 0, y: 0, width: 512, height: 512), cornerWidth: 116, cornerHeight: 116, transform: nil))
    ctx.setFillColor(ink)
    ctx.fillPath()

    // Below about 48 px the drawing is simpler: three bars, a heavier circle, no numeral.
    let tiny = Double(px) * (bleed ? 1 : 0.8) < 48
    let heights: [CGFloat] = tiny ? [112, 196, 96] : [84, 150, 206, 128, 70]
    let width: CGFloat = tiny ? 44 : 30, gap: CGFloat = tiny ? 34 : 24
    let center = CGPoint(x: tiny ? 256 : 262, y: tiny ? 256 : 262)
    let total = CGFloat(heights.count) * width + CGFloat(heights.count - 1) * gap
    ctx.setFillColor(white)
    for (i, h) in heights.enumerated() {
        let bar = CGRect(x: center.x - total / 2 + CGFloat(i) * (width + gap), y: center.y - h / 2, width: width, height: h)
        ctx.addPath(CGPath(roundedRect: bar, cornerWidth: width / 2, cornerHeight: width / 2, transform: nil))
    }
    ctx.fillPath()

    // The circle is drawn by hand: it overshoots where it started.
    let box = tiny ? CGRect(x: 56, y: 92, width: 400, height: 328) : CGRect(x: 74, y: 110, width: 376, height: 304)
    func at(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: box.minX + x * box.width / 100, y: box.minY + y * box.height / 100) }
    let loop = CGMutablePath()
    loop.move(to: at(50, 4))
    loop.addCurve(to: at(98, 50), control1: at(82, 2), control2: at(99, 22))
    loop.addCurve(to: at(48, 96), control1: at(97, 80), control2: at(74, 97))
    loop.addCurve(to: at(2, 50), control1: at(20, 95), control2: at(2, 78))
    loop.addCurve(to: at(60, 5), control1: at(2, 24), control2: at(22, 6))
    var turn = CGAffineTransform(translationX: box.midX, y: box.midY).rotated(by: -5 * .pi / 180).translatedBy(x: -box.midX, y: -box.midY)
    ctx.addPath(loop.copy(using: &turn)!)
    ctx.setStrokeColor(red)
    ctx.setLineWidth(tiny ? 46 : 30)
    ctx.setLineCap(.round)
    ctx.strokePath()

    // The badge that numbers the mark.
    if !tiny {
        let c = CGPoint(x: 112, y: 140), r: CGFloat = 46
        ctx.setFillColor(ink)
        ctx.fillEllipse(in: CGRect(x: c.x - r - 12, y: c.y - r - 12, width: 2 * (r + 12), height: 2 * (r + 12)))
        ctx.setFillColor(red)
        ctx.fillEllipse(in: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r))
        let one: [(CGFloat, CGFloat)] = [(9, -23), (9, 23), (-4, 23), (-4, -7), (-16, -2), (-16, -14), (-1, -23)]
        ctx.move(to: CGPoint(x: c.x + one[0].0, y: c.y + one[0].1))
        for p in one.dropFirst() { ctx.addLine(to: CGPoint(x: c.x + p.0, y: c.y + p.1)) }
        ctx.closePath()
        ctx.setFillColor(white)
        ctx.fillPath()
    }
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

let args = Array(CommandLine.arguments.dropFirst())
if args.first == "--png", args.count >= 3, let size = Int(args[1]) {
    try render(size, bleed: args.contains("--bleed")).write(to: URL(fileURLWithPath: args[2]))
} else if let folder = args.first {
    let out = URL(fileURLWithPath: folder, isDirectory: true)
    try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
    for base in [16, 32, 128, 256, 512] {
        try render(base).write(to: out.appendingPathComponent("icon_\(base)x\(base).png"))
        try render(base * 2).write(to: out.appendingPathComponent("icon_\(base)x\(base)@2x.png"))
    }
} else {
    FileHandle.standardError.write("usage: make-icon.swift <folder.iconset> | --png <size> <file.png> [--bleed]\n".data(using: .utf8)!)
    exit(2)
}
