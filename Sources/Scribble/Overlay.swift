import AppKit
import QuartzCore
import ScribbleCore

/// Borderless full-screen panel above everything (including full-screen apps).
final class OverlayPanel: NSPanel {
    var allowsKey = false

    init(screen: NSScreen) {
        super.init(contentRect: screen.frame, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        setFrame(screen.frame, display: false)
        level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.screenSaverWindow)))
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        isReleasedWhenClosed = false
        hidesOnDeactivate = false
        isMovable = false
        animationBehavior = .none
        acceptsMouseMovedEvents = true
    }

    override var canBecomeKey: Bool { allowsKey }
    override var canBecomeMain: Bool { false }
}

@MainActor
protocol CanvasDelegate: AnyObject {
    func canvas(_ canvas: AnnotationCanvasView, didFinish annotation: Annotation)
    func canvasRequestsFinish()
    func canvasRequestsCancel()
    func canvasRequestsNextShot()
    func canvasRequestsUndo()
    func canvas(_ canvas: AnnotationCanvasView, didSelect tool: AnnotationTool)
    func canvas(_ canvas: AnnotationCanvasView, didSelectColor index: Int)
}

/// Draws annotations and handles mouse/keyboard input. Coordinates are canvas points with
/// origin top-left (the view is flipped).
final class AnnotationCanvasView: NSView {
    weak var delegate: CanvasDelegate?
    var tool: AnnotationTool = .pen
    var colorIndex = 0
    /// Session clock (seconds since capture start).
    var clock: () -> Double = { 0 }
    var shot = 0
    /// Display index (in the capture's display list) this canvas covers.
    var screen = 0
    /// Walkthrough: seconds an annotation stays fully visible before fading. nil = never fade.
    var fadeAfter: Double?
    static let fadeOutDuration = 0.6
    static let pinExtraHold = 2.0

    private(set) var annotations: [Annotation] = []
    private var current: Annotation?
    private var fadeTimer: Timer?

    var isTracking: Bool { current != nil }

    /// Show a crosshair cursor (only effective while the overlay is key, i.e. Snapshot).
    var crosshair = false {
        didSet { window?.invalidateCursorRects(for: self) }
    }

    override func resetCursorRects() {
        if crosshair { addCursorRect(bounds, cursor: .crosshair) }
    }

    override var isFlipped: Bool { true }
    override var acceptsFirstResponder: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.backgroundColor = .clear
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func add(_ a: Annotation) {
        annotations.append(a)
        needsDisplay = true
        startFadeTimerIfNeeded()
    }

    func remove(id: Int) {
        annotations.removeAll { $0.id == id }
        needsDisplay = true
    }

    func removeAll() {
        annotations.removeAll()
        current = nil
        needsDisplay = true
    }

    func stop() {
        fadeTimer?.invalidate()
        fadeTimer = nil
    }

    private var color: RGBAColor { RGBAColor.palette[min(max(colorIndex, 0), RGBAColor.palette.count - 1)] }

    // MARK: Mouse

    private func point(_ event: NSEvent) -> CGPoint {
        let p = convert(event.locationInWindow, from: nil)
        return CGPoint(x: min(max(p.x, 0), bounds.width), y: min(max(p.y, 0), bounds.height))
    }

    override func mouseDown(with event: NSEvent) {
        // Keyboard shortcuts follow the display being drawn on.
        if let window, !window.isKeyWindow, window.canBecomeKey {
            window.makeKey()
            window.makeFirstResponder(self)
        }
        let p = point(event)
        let t = clock()
        if tool == .pin {
            var a = Annotation(tool: .pin, points: [p], color: color, startTime: t, shot: shot, screen: screen)
            a.finalize(endTime: t)
            delegate?.canvas(self, didFinish: a)
            return
        }
        current = Annotation(tool: tool, points: [p], color: color, startTime: t, shot: shot, screen: screen)
        needsDisplay = true
    }

    override func mouseDragged(with event: NSEvent) {
        guard var a = current else { return }
        let p = point(event)
        if a.tool.isTwoPoint {
            a.points = [a.points[0], p]
        } else if let last = a.points.last, hypot(p.x - last.x, p.y - last.y) >= 1.5 {
            a.points.append(p)
        }
        current = a
        needsDisplay = true
    }

    override func mouseUp(with event: NSEvent) {
        guard var a = current else { return }
        current = nil
        needsDisplay = true
        let t = clock()
        let extent = ShapeClassifier.pathLength(a.points)
        if a.tool.isTwoPoint {
            guard a.points.count == 2, extent >= 6 else { return }
        } else if extent < 3 {
            // A pen tap stays a dot; a highlighter tap highlights nothing.
            guard a.tool == .pen else { return }
            a.points = [a.points[0]]
        }
        a.finalize(endTime: t)
        delegate?.canvas(self, didFinish: a)
    }

    override func rightMouseDown(with event: NSEvent) {
        delegate?.canvasRequestsUndo()
    }

    // MARK: Keyboard (Snapshot mode)

    override func keyDown(with event: NSEvent) {
        switch Int(event.keyCode) {
        case 36, 76: delegate?.canvasRequestsFinish(); return
        case 53: delegate?.canvasRequestsCancel(); return
        case 48: delegate?.canvasRequestsNextShot(); return
        case 51, 117: delegate?.canvasRequestsUndo(); return
        default: break
        }
        guard let chars = event.charactersIgnoringModifiers?.lowercased(), let ch = chars.first else { return }
        if event.modifierFlags.contains(.command) {
            if ch == "z" { delegate?.canvasRequestsUndo() }
            return
        }
        if let t = AnnotationTool.allCases.first(where: { $0.shortcut == ch }) {
            delegate?.canvas(self, didSelect: t)
        } else if let digit = Int(String(ch)), (1...RGBAColor.palette.count).contains(digit) {
            delegate?.canvas(self, didSelectColor: digit - 1)
        }
    }

    // MARK: Drawing

    func alpha(for a: Annotation, now: Double) -> CGFloat {
        guard let fadeAfter else { return 1 }
        let hold = fadeAfter + (a.tool == .pin ? Self.pinExtraHold : 0)
        let age = now - a.endTime - hold
        if age <= 0 { return 1 }
        return CGFloat(max(0, 1 - age / Self.fadeOutDuration))
    }

    override func draw(_ dirtyRect: NSRect) {
        guard let ctx = NSGraphicsContext.current?.cgContext else { return }
        let now = clock()
        for a in annotations {
            AnnotationRenderer.draw(a, in: ctx, alpha: alpha(for: a, now: now), canvasSize: bounds.size)
        }
        if let current {
            AnnotationRenderer.draw(current, in: ctx, showBadge: false)
        }
    }

    private func startFadeTimerIfNeeded() {
        guard fadeAfter != nil, fadeTimer == nil else { return }
        let timer = Timer(timeInterval: 1.0 / 30, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.fadeTick() }
        }
        RunLoop.main.add(timer, forMode: .common)
        fadeTimer = timer
    }

    private func fadeTick() {
        let now = clock()
        var changed = false
        let before = annotations.count
        annotations.removeAll { alpha(for: $0, now: now) <= 0 }
        if annotations.count != before { changed = true }
        if annotations.contains(where: { let al = alpha(for: $0, now: now); return al > 0 && al < 1 }) { changed = true }
        if changed { needsDisplay = true }
        if annotations.isEmpty {
            fadeTimer?.invalidate()
            fadeTimer = nil
        }
    }
}

/// Root view of a Snapshot overlay: frozen screenshot underneath, canvas on top.
final class SnapshotBackdropView: NSView {
    init(frame: NSRect, image: CGImage) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.contents = image
        layer?.contentsGravity = .resize
        layer?.magnificationFilter = .linear
        layer?.minificationFilter = .trilinear
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
}
