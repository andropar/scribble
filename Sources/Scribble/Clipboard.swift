import AppKit
import ImageIO
import ScribbleCore

@MainActor
enum ClipboardWriter {
    static func writeText(_ text: String) throws {
        let pb = NSPasteboard.general
        pb.clearContents()
        guard pb.setString(text, forType: .string) else { throw AppError("Writing to the clipboard failed") }
    }

    static func write(_ capture: RecentCapture, format: ClipboardFormat) throws {
        let markdown = try String(contentsOf: capture.markdownURL, encoding: .utf8)
        let fm = FileManager.default
        let images = capture.imageURLs.filter { fm.fileExists(atPath: $0.path) }

        func textItem() -> NSPasteboardItem {
            let item = NSPasteboardItem()
            item.setString(markdown, forType: .string)
            return item
        }

        func fileItem(_ url: URL, png: Bool) -> NSPasteboardItem {
            let item = NSPasteboardItem()
            item.setString(url.absoluteString, forType: .fileURL)
            if png, let data = try? Data(contentsOf: url) {
                item.setData(data, forType: .png)
            }
            return item
        }

        var items: [NSPasteboardItem]
        switch format {
        case .universal:
            // Terminals paste the text; chat apps (Electron/web) attach files and drop the
            // text when images are present, so the transcript also travels as capture.md.
            let first = textItem()
            first.setString(capture.markdownURL.absoluteString, forType: .fileURL)
            items = [first] + images.map { fileItem($0, png: true) }
        case .textAndPaths:
            items = [textItem()]
        case .imagesAndText:
            items = images.map { fileItem($0, png: true) } + [textItem()]
        case .imagesOnly:
            items = images.map { fileItem($0, png: true) }
        case .composite:
            let url = try CompositeRenderer.render(capture, markdown: markdown)
            items = [fileItem(url, png: true)]
        case .markdownFile:
            let item = fileItem(capture.markdownURL, png: false)
            item.setString(markdown, forType: .string)
            items = [item]
        }
        if items.isEmpty { items = [textItem()] }

        let pb = NSPasteboard.general
        pb.clearContents()
        guard pb.writeObjects(items) else { throw AppError("Writing to the clipboard failed") }
    }
}

/// Renders a single tall image: all frames stacked, transcript text underneath.
enum CompositeRenderer {
    static let width: CGFloat = 1400
    static let maxHeight: CGFloat = 7800
    static let margin: CGFloat = 24

    static func render(_ capture: RecentCapture, markdown: String) throws -> URL {
        let frames: [CGImage] = capture.frameURLs.compactMap { url in
            guard let src = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
            return CGImageSourceCreateImageAtIndex(src, 0, nil)
        }
        let transcript = transcriptText(from: markdown)
        let textAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 17),
            .foregroundColor: NSColor.black,
        ]
        let text = NSAttributedString(string: transcript, attributes: textAttrs)
        let textWidth = width - 2 * margin
        var textHeight = ceil(text.boundingRect(with: NSSize(width: textWidth, height: .greatestFiniteMagnitude), options: [.usesLineFragmentOrigin, .usesFontLeading]).height)
        textHeight = min(textHeight, maxHeight * 0.5)

        let labelHeight: CGFloat = 30
        let imageWidth = width - 2 * margin
        var imageHeights = frames.map { imageWidth * CGFloat($0.height) / CGFloat(max($0.width, 1)) }
        let fixed = CGFloat(frames.count) * (labelHeight + margin) + textHeight + 3 * margin
        let available = maxHeight - fixed
        let total = imageHeights.reduce(0, +)
        var scale: CGFloat = 1
        if total > available, total > 0 {
            scale = max(0.2, available / total)
            imageHeights = imageHeights.map { $0 * scale }
        }
        let height = Int(ceil(fixed + imageHeights.reduce(0, +)))

        guard let ctx = ImageRendering.makeContext(width: Int(width), height: height) else {
            throw AppError("Could not create composite image")
        }
        ctx.setFillColor(CGColor(gray: 1, alpha: 1))
        ctx.fill(CGRect(x: 0, y: 0, width: Int(width), height: height))
        ctx.translateBy(x: 0, y: CGFloat(height))
        ctx.scaleBy(x: 1, y: -1)
        let gctx = NSGraphicsContext(cgContext: ctx, flipped: true)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = gctx

        let labelAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.boldSystemFont(ofSize: 18),
            .foregroundColor: NSColor.darkGray,
        ]
        var y = margin
        for (i, image) in frames.enumerated() {
            NSAttributedString(string: "frame \(i + 1)", attributes: labelAttrs).draw(at: NSPoint(x: margin, y: y))
            y += labelHeight
            let w = imageWidth * scale
            let rect = NSRect(x: (width - w) / 2, y: y, width: w, height: imageHeights[i])
            NSImage(cgImage: image, size: rect.size).draw(in: rect, from: .zero, operation: .sourceOver, fraction: 1, respectFlipped: true, hints: [.interpolation: NSImageInterpolation.high.rawValue])
            y += imageHeights[i] + margin
        }
        text.draw(with: NSRect(x: margin, y: y + margin, width: textWidth, height: textHeight), options: [.usesLineFragmentOrigin, .usesFontLeading, .truncatesLastVisibleLine])
        NSGraphicsContext.restoreGraphicsState()

        guard let image = ctx.makeImage() else { throw AppError("Could not create composite image") }
        let url = capture.folderURL.appendingPathComponent("composite.png")
        try ImageRendering.writePNG(image, to: url)
        return url
    }

    /// Transcript section of capture.md without markdown emphasis.
    static func transcriptText(from markdown: String) -> String {
        var lines = markdown.components(separatedBy: "\n")
        if let start = lines.firstIndex(of: "## Transcript") {
            lines = Array(lines[(start + 1)...])
        }
        if let end = lines.firstIndex(where: { $0.hasPrefix("Folder: ") }) {
            lines = Array(lines[..<end])
        }
        return lines.joined(separator: "\n")
            .replacingOccurrences(of: "**", with: "")
            .replacingOccurrences(of: "## ", with: "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

@MainActor
final class RecentsStore: ObservableObject {
    @Published private(set) var items: [RecentCapture] = []
    private let maxItems = 10

    init() {
        load()
    }

    func add(_ capture: RecentCapture) {
        items.removeAll { $0.folder == capture.folder }
        items.insert(capture, at: 0)
        if items.count > maxItems { items = Array(items.prefix(maxItems)) }
        save()
    }

    func pruneMissing() {
        let before = items.count
        items.removeAll { !FileManager.default.fileExists(atPath: $0.markdownPath) }
        if items.count != before { save() }
    }

    private func load() {
        guard let data = try? Data(contentsOf: Paths.recents) else { return }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        items = (try? decoder.decode([RecentCapture].self, from: data)) ?? []
        pruneMissing()
    }

    private func save() {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        do {
            try encoder.encode(items).write(to: Paths.recents, options: .atomic)
        } catch {
            Log.app.error("Saving recents failed: \(error.localizedDescription, privacy: .public)")
        }
    }
}
