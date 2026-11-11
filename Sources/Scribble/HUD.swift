import AppKit
import SwiftUI
import ScribbleCore

@MainActor
final class HUDModel: ObservableObject {
    @Published var mode: CaptureMode = .snapshot
    @Published var elapsed: Double = 0
    @Published var level: Float = 0
    @Published var drawing = false
    @Published var drawToggled = false
    @Published var tool: AnnotationTool = .pen
    @Published var colorIndex = 0
    @Published var navigating = false
    @Published var shotCount = 1
    @Published var annotationCount = 0
    @Published var micAvailable = true
    @Published var starting = true

    var onTool: (AnnotationTool) -> Void = { _ in }
    var onColor: (Int) -> Void = { _ in }
    var onUndo: () -> Void = {}
    var onNextShot: () -> Void = {}
    var onToggleDraw: () -> Void = {}
    var onFinish: () -> Void = {}
    var onCancel: () -> Void = {}
}

struct HUDView: View {
    @ObservedObject var model: HUDModel
    let snapshotKey: String
    let finishKey: String
    let drawModifier: String

    var body: some View {
        HStack(spacing: 10) {
            recordingIndicator
            LevelMeter(level: model.micAvailable ? model.level : 0)
                .opacity(model.micAvailable ? 1 : 0.3)
                .help(model.micAvailable ? "Microphone level" : "No microphone access")
            Divider().frame(height: 22)
            if model.mode == .dictation {
                Text("Dictating · \(finishKey) to finish")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(.secondary)
            } else if model.navigating {
                Text("Navigate, then \(snapshotKey) for shot \(model.shotCount + 1) · \(finishKey) to finish")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(.primary)
            } else {
                tools
                Divider().frame(height: 22)
                colors
                Divider().frame(height: 22)
                iconButton("arrow.uturn.backward", help: "Undo (⌘Z)", action: model.onUndo)
                if model.mode == .snapshot {
                    iconButton("camera.viewfinder", help: "Hide overlay and take another shot (⇥)", action: model.onNextShot)
                } else {
                    drawToggle
                }
            }
            Divider().frame(height: 22)
            iconButton("checkmark.circle.fill", help: "Finish and copy (\(model.mode == .snapshot ? "↩ / " : "")\(finishKey))", tint: .green, action: model.onFinish)
            iconButton("xmark.circle.fill", help: model.mode == .snapshot ? "Cancel (⎋)" : "Cancel", tint: .secondary, action: model.onCancel)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(.regularMaterial, in: Capsule())
        .overlay(Capsule().strokeBorder(Color.primary.opacity(0.12)))
        .fixedSize()
        .padding(10)
    }

    private var recordingIndicator: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(model.starting ? Color.orange : Color.red)
                .frame(width: 9, height: 9)
            Text(Timecode.clock(model.elapsed))
                .font(.system(size: 12, weight: .semibold).monospacedDigit())
            if model.mode == .snapshot && model.shotCount > 1 {
                Text("· \(model.shotCount) shots").font(.system(size: 11)).foregroundStyle(.secondary)
            }
            if model.annotationCount > 0 {
                Text("· #\(model.annotationCount)").font(.system(size: 11)).foregroundStyle(.secondary)
            }
        }
    }

    private var tools: some View {
        HStack(spacing: 2) {
            ForEach(AnnotationTool.allCases, id: \.self) { t in
                Button {
                    model.onTool(t)
                } label: {
                    Image(systemName: symbol(for: t))
                        .font(.system(size: 13, weight: .medium))
                        .frame(width: 26, height: 24)
                        .background(
                            RoundedRectangle(cornerRadius: 6)
                                .fill(model.tool == t ? Color.accentColor.opacity(0.25) : Color.clear)
                        )
                }
                .buttonStyle(.plain)
                .help(model.mode == .snapshot ? "\(t.title) (\(String(t.shortcut).uppercased()))" : t.title)
            }
        }
    }

    private var colors: some View {
        HStack(spacing: 5) {
            ForEach(Array(RGBAColor.palette.enumerated()), id: \.offset) { index, c in
                Button {
                    model.onColor(index)
                } label: {
                    Circle()
                        .fill(Color(.sRGB, red: c.r, green: c.g, blue: c.b))
                        .frame(width: 14, height: 14)
                        .overlay(Circle().strokeBorder(Color.primary.opacity(model.colorIndex == index ? 0.9 : 0.2), lineWidth: model.colorIndex == index ? 2 : 1))
                        .padding(2)
                }
                .buttonStyle(.plain)
                .help(model.mode == .snapshot ? "Color \(index + 1)" : "Color")
            }
        }
    }

    private var drawToggle: some View {
        Button(action: model.onToggleDraw) {
            HStack(spacing: 4) {
                Image(systemName: model.drawing ? "pencil.tip.crop.circle.fill" : "cursorarrow")
                Text(model.drawing ? "Drawing" : (drawModifier.isEmpty ? "Click-through" : "Hold \(drawModifier) to draw"))
                    .font(.system(size: 11, weight: .medium))
            }
            .padding(.horizontal, 8)
            .frame(height: 24)
            .background(RoundedRectangle(cornerRadius: 6).fill(model.drawing ? Color.accentColor.opacity(0.3) : Color.primary.opacity(0.06)))
        }
        .buttonStyle(.plain)
        .help("Toggle draw mode")
    }

    private func iconButton(_ name: String, help: String, tint: Color = .primary, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: name)
                .font(.system(size: 15, weight: .medium))
                .foregroundStyle(tint)
                .frame(width: 26, height: 24)
        }
        .buttonStyle(.plain)
        .help(help)
    }

    private func symbol(for t: AnnotationTool) -> String {
        switch t {
        case .pen: return "pencil.tip"
        case .arrow: return "arrow.up.right"
        case .rectangle: return "rectangle"
        case .ellipse: return "circle"
        case .highlighter: return "highlighter"
        case .pin: return "mappin"
        }
    }
}

struct LevelMeter: View {
    let level: Float

    var body: some View {
        HStack(alignment: .bottom, spacing: 2) {
            ForEach(0..<5, id: \.self) { i in
                RoundedRectangle(cornerRadius: 1)
                    .fill(Float(i) / 5 < level ? Color.green : Color.primary.opacity(0.15))
                    .frame(width: 3, height: CGFloat(6 + i * 3))
            }
        }
        .frame(height: 18)
        .animation(.linear(duration: 0.08), value: level)
    }
}

/// Hosting view that reacts to the first click even when the app isn't active.
final class FirstMouseHostingView<Content: View>: NSHostingView<Content> {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

/// Floating, non-activating panel that never becomes key and is excluded from capture.
class FloatingPanel: NSPanel {
    init() {
        super.init(contentRect: NSRect(x: 0, y: 0, width: 10, height: 10), styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.screenSaverWindow)) + 1)
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        isReleasedWhenClosed = false
        hidesOnDeactivate = false
        becomesKeyOnlyIfNeeded = true
        isMovableByWindowBackground = true
        sharingType = .none
        animationBehavior = .none
    }

    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }

    func setRoot<V: View>(_ view: V) {
        let host = FirstMouseHostingView(rootView: view)
        host.autoresizingMask = [.width, .height]
        contentView = host
    }

    /// Fits the panel to its content and places it at the top center of `screen`.
    func refit(on screen: NSScreen, keepPosition: Bool = false) {
        guard let content = contentView else { return }
        let size = content.fittingSize
        let x = keepPosition ? frame.midX - size.width / 2 : screen.frame.midX - size.width / 2
        let y = keepPosition ? frame.maxY - size.height : screen.visibleFrame.maxY - size.height - 6
        setFrame(NSRect(x: x, y: y, width: size.width, height: size.height), display: true)
    }
}

// MARK: Toast

@MainActor
final class ToastModel: ObservableObject {
    @Published var text = ""
    @Published var detail = ""
    @Published var busy = false
    @Published var success = false
}

struct ToastView: View {
    @ObservedObject var model: ToastModel

    var body: some View {
        HStack(spacing: 10) {
            if model.busy {
                ProgressView().controlSize(.small)
            } else {
                Image(systemName: model.success ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                    .foregroundStyle(model.success ? Color.green : Color.orange)
                    .font(.system(size: 16))
            }
            VStack(alignment: .leading, spacing: 1) {
                Text(model.text).font(.system(size: 13, weight: .semibold))
                if !model.detail.isEmpty {
                    Text(model.detail).font(.system(size: 11)).foregroundStyle(.secondary)
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.regularMaterial, in: Capsule())
        .overlay(Capsule().strokeBorder(Color.primary.opacity(0.12)))
        .fixedSize()
        .padding(10)
    }
}

@MainActor
final class Toast {
    static let shared = Toast()

    private let model = ToastModel()
    private var panel: FloatingPanel?
    private var hideWork: DispatchWorkItem?

    func show(_ text: String, detail: String = "", busy: Bool = false, success: Bool = true, duration: Double? = 2.8, on screen: NSScreen? = nil) {
        model.text = text
        model.detail = detail
        model.busy = busy
        model.success = success
        let panel = self.panel ?? {
            let p = FloatingPanel()
            p.setRoot(ToastView(model: model))
            self.panel = p
            return p
        }()
        panel.ignoresMouseEvents = true
        DispatchQueue.main.async {
            guard let target = screen ?? Screens.underMouse() else { return }
            panel.refit(on: target)
            panel.orderFrontRegardless()
        }
        hideWork?.cancel()
        if let duration {
            let work = DispatchWorkItem { [weak self] in self?.hide() }
            hideWork = work
            DispatchQueue.main.asyncAfter(deadline: .now() + duration, execute: work)
        }
    }

    func hide() {
        hideWork?.cancel()
        panel?.orderOut(nil)
    }
}
