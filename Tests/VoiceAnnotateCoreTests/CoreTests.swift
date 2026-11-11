import CoreGraphics
import XCTest
@testable import VoiceAnnotateCore
import WhisperKit

final class ShapeClassifierTests: XCTestCase {
    func circle(turns: Double = 1.0, n: Int = 60, cx: Double = 200, cy: Double = 150, rx: Double = 80, ry: Double = 50) -> [CGPoint] {
        (0...n).map { i in
            let a = Double(i) / Double(n) * 2 * .pi * turns
            return CGPoint(x: cx + rx * cos(a), y: cy + ry * sin(a))
        }
    }

    func testPenStrokesStayFreehandDrawings() {
        var loop = Annotation(tool: .pen, points: circle(), color: .red, startTime: 0)
        loop.finalize(endTime: 1)
        XCTAssertEqual(loop.shape, .freehand)
        XCTAssertTrue(loop.isDrawing)
        XCTAssertNil(loop.number)
    }

    func testToolShapes() {
        XCTAssertEqual(ShapeClassifier.defaultShape(for: .arrow), .arrow)
        XCTAssertEqual(ShapeClassifier.defaultShape(for: .rectangle), .box)
        XCTAssertEqual(ShapeClassifier.defaultShape(for: .pin), .pin)
    }
}

final class GeometryTests: XCTestCase {
    func testMapperScalesAndClamps() {
        let m = CoordinateMapper(canvasSize: CGSize(width: 1000, height: 500), imageSize: CGSize(width: 2000, height: 1000))
        XCTAssertEqual(m.map(CGPoint(x: 100, y: 50)).x, 200)
        XCTAssertEqual(m.map(CGPoint(x: 100, y: 50)).y, 100)
        XCTAssertEqual(m.map(CGPoint(x: 5000, y: -10)).x, 1999)
        XCTAssertEqual(m.map(CGPoint(x: 5000, y: -10)).y, 0)
        XCTAssertEqual(m.describe(CGRect(x: 10, y: 10, width: 20, height: 30)), "(20,20)-(60,80)")
    }

    func testMapperWithCropRegion() {
        let m = CoordinateMapper(
            canvasSize: CGSize(width: 1000, height: 500), imageSize: CGSize(width: 400, height: 200),
            canvasRegion: CGRect(x: 100, y: 100, width: 200, height: 100)
        )
        XCTAssertEqual(m.map(CGPoint(x: 150, y: 150)).x, 100)
        XCTAssertEqual(m.map(CGPoint(x: 150, y: 150)).y, 100)
    }

    func testFitNeverUpscales() {
        XCTAssertEqual(ImageSizing.fit(CGSize(width: 800, height: 600), longEdge: 1568), CGSize(width: 800, height: 600))
        XCTAssertEqual(ImageSizing.fit(CGSize(width: 3024, height: 1964), longEdge: 1568), CGSize(width: 1568, height: 1018))
        XCTAssertEqual(ImageSizing.fit(CGSize(width: 1000, height: 4000), longEdge: 2000), CGSize(width: 500, height: 2000))
    }

    func testDetailCropOnlyOnLargeDisplays() {
        let region = [CGRect(x: 100, y: 100, width: 50, height: 50)]
        // Laptop: 1512 pt shown at 1568 px → legible, no crop.
        XCTAssertNil(CropPlanner.detailRect(around: region, canvasSize: CGSize(width: 1512, height: 982), fullFramePixelWidth: 1568))
        // 6K display: 3008 pt shown at 1568 px → crop.
        let canvas = CGSize(width: 3008, height: 1692)
        let crop = CropPlanner.detailRect(around: region, canvasSize: canvas, fullFramePixelWidth: 1568)
        XCTAssertNotNil(crop)
        if let crop {
            XCTAssertGreaterThanOrEqual(crop.minX, 0)
            XCTAssertGreaterThanOrEqual(crop.minY, 0)
            XCTAssertGreaterThanOrEqual(crop.width, 800)
            XCTAssertGreaterThanOrEqual(crop.height, 500)
            XCTAssertTrue(crop.contains(region[0]))
        }
    }

    func testDetailCropSkippedWhenAnnotationsCoverEverything() {
        let canvas = CGSize(width: 3008, height: 1692)
        let regions = [CGRect(x: 10, y: 10, width: 20, height: 20), CGRect(x: 2900, y: 1600, width: 50, height: 50)]
        XCTAssertNil(CropPlanner.detailRect(around: regions, canvasSize: canvas, fullFramePixelWidth: 1568))
    }
}

final class DescriberTests: XCTestCase {
    let mapper = CoordinateMapper(canvasSize: CGSize(width: 1000, height: 1000), imageSize: CGSize(width: 1000, height: 1000))

    func testArrowDescription() {
        let a = Annotation(id: 2, number: 2, tool: .arrow, points: [CGPoint(x: 10, y: 20), CGPoint(x: 300, y: 400)], color: .red, startTime: 0)
        XCTAssertEqual(AnnotationDescriber.describe(a, mapper: mapper, frame: 3), "#2 arrow from (10,20) pointing at (300,400) in frame 3")
    }

    func testCircledDescription() {
        let a = Annotation(id: 7, number: 1, tool: .ellipse, points: [CGPoint(x: 120, y: 100), CGPoint(x: 280, y: 200)], color: .red, startTime: 0)
        XCTAssertEqual(AnnotationDescriber.describe(a, mapper: mapper, frame: nil), "#1 circled (120,100)-(280,200)")
    }

    func testPinDescription() {
        let pin = Annotation(id: 9, number: 4, tool: .pin, points: [CGPoint(x: 50, y: 60)], color: .red, startTime: 0)
        XCTAssertEqual(AnnotationDescriber.describe(pin, mapper: mapper, frame: 1), "#4 pin at (50,60) in frame 1")
    }

    func stroke(_ x: Double, start: Double, end: Double, shot: Int = 0) -> Annotation {
        var a = Annotation(tool: .pen, points: [CGPoint(x: x, y: 100), CGPoint(x: x + 50, y: 140)], color: .red, startTime: start, shot: shot)
        a.finalize(endTime: end)
        return a
    }

    func testDrawingBurstsSplitOnPausesAndFrames() {
        let strokes = [
            stroke(100, start: 0, end: 0.5), stroke(200, start: 1.2, end: 1.8), stroke(300, start: 2.5, end: 3),
            // A pause to think, then more drawing.
            stroke(400, start: 8, end: 8.6),
            // Same moment, another shot.
            stroke(500, start: 9, end: 9.5, shot: 1),
        ]
        let bursts = AnnotationDescriber.drawingBursts(strokes) { $0.shot }
        XCTAssertEqual(bursts.map(\.strokes.count), [3, 1, 1])
        XCTAssertEqual(bursts.map(\.frame), [0, 0, 1])
    }

    func testDrawingDescriptionHasNoNumber() {
        let text = AnnotationDescriber.describeDrawing([stroke(100, start: 0, end: 1), stroke(300, start: 1.5, end: 2)], mapper: mapper, frame: 2)
        XCTAssertEqual(text, "drawing (2 strokes) over (100,100)-(350,140) in frame 2")
        XCTAssertEqual(AnnotationDescriber.describeDrawing([stroke(100, start: 0, end: 1)], mapper: mapper, frame: nil), "drawing over (100,100)-(150,140)")
    }
}

final class KeyframeSelectorTests: XCTestCase {
    func thumb(_ v: UInt8) -> [UInt8] { [UInt8](repeating: v, count: 64 * 40) }

    /// Screen shows view A until t=5, animates to view B until t=5.5, then stays.
    func twoViewSamples(duration: Double = 12) -> [FrameSample] {
        var samples: [FrameSample] = []
        var t = 0.0
        while t <= duration {
            let v: UInt8
            if t < 5 { v = 40 } else if t < 5.5 { v = UInt8(40 + (t - 5) / 0.5 * 160) } else { v = 200 }
            samples.append(FrameSample(time: t, thumb: thumb(v)))
            t += 0.1
        }
        return samples
    }

    func testDiff() {
        XCTAssertEqual(KeyframeSelector.diff(thumb(0), thumb(255)), 1, accuracy: 1e-9)
        XCTAssertEqual(KeyframeSelector.diff(thumb(10), thumb(10)), 0)
    }

    func testPicksSettledFrameAfterChangeAndDropsDuplicateEnd() {
        let sel = KeyframeSelector.select(samples: twoViewSamples(), annotations: [], clicks: [], speechStarts: [], duration: 12)
        XCTAssertEqual(sel.count, 2)
        XCTAssertEqual(sel.first?.reasons, ["start"])
        let change = sel.last
        XCTAssertEqual(change?.reasons, ["screen change"])
        if let change {
            // Settled: after the animation ended, not mid-animation.
            XCTAssertGreaterThanOrEqual(change.time, 5.5)
            XCTAssertLessThan(change.time, 6.5)
        }
    }

    func testEndFrameKeptWhenItShowsSomethingNew() {
        var samples = twoViewSamples(duration: 12)
        samples.append(FrameSample(time: 11.9, thumb: thumb(120)))
        let sel = KeyframeSelector.select(samples: samples, annotations: [], clicks: [], speechStarts: [], duration: 12)
        XCTAssertTrue(sel.last?.reasons.contains("end") ?? false)
    }

    func testReturningToEarlierViewIsKept() {
        // A → B → A: the second A is chronologically meaningful and must not be deduped away.
        var samples: [FrameSample] = []
        for i in 0..<200 {
            let t = Double(i) * 0.1
            samples.append(FrameSample(time: t, thumb: thumb(t < 6 || t >= 13 ? 40 : 200)))
        }
        let sel = KeyframeSelector.select(samples: samples, annotations: [], clicks: [], speechStarts: [], duration: 20)
        XCTAssertEqual(sel.filter { $0.reasons.contains("screen change") }.count, 2)
    }

    func testAnnotationFramesAlwaysKeptAndGrouped() {
        var a1 = Annotation(id: 1, tool: .pin, points: [.zero], color: .red, startTime: 2)
        a1.finalize(endTime: 2)
        var a2 = Annotation(id: 2, tool: .arrow, points: [.zero, CGPoint(x: 10, y: 10)], color: .red, startTime: 2.5)
        a2.finalize(endTime: 3)
        var a3 = Annotation(id: 3, tool: .rectangle, points: [.zero, CGPoint(x: 10, y: 10)], color: .red, startTime: 8)
        a3.finalize(endTime: 8.5)
        let sel = KeyframeSelector.select(samples: twoViewSamples(), annotations: [a1, a2, a3], clicks: [], speechStarts: [], duration: 12)
        let annotated = sel.filter { $0.reasons.contains("annotation") }
        XCTAssertEqual(annotated.count, 2)
        XCTAssertEqual(annotated.first?.annotationIDs, [1, 2])
        XCTAssertEqual(annotated.last?.annotationIDs, [3])
        XCTAssertEqual(annotated.first?.time ?? 0, 3.25, accuracy: 0.01)
    }

    func testScreenChangeSplitsAnnotationGroups() {
        var a1 = Annotation(id: 1, tool: .pin, points: [.zero], color: .red, startTime: 4.6)
        a1.finalize(endTime: 4.6)
        var a2 = Annotation(id: 2, tool: .pin, points: [.zero], color: .red, startTime: 5.9)
        a2.finalize(endTime: 5.9)
        let sel = KeyframeSelector.select(samples: twoViewSamples(), annotations: [a1, a2], clicks: [], speechStarts: [], duration: 12)
        XCTAssertEqual(sel.filter { $0.reasons.contains("annotation") }.count, 2)
    }

    func testBudgetRespectedForOptionalFrames() {
        // Screen alternates every 2 s for 60 s → many change candidates.
        var samples: [FrameSample] = []
        for i in 0..<600 {
            let t = Double(i) * 0.1
            samples.append(FrameSample(time: t, thumb: thumb(Int(t / 2) % 2 == 0 ? 30 : 220)))
        }
        var config = KeyframeSelector.Config()
        config.maxFrames = 8
        let sel = KeyframeSelector.select(samples: samples, annotations: [], clicks: [], speechStarts: [], duration: 60, config: config)
        XCTAssertLessThanOrEqual(sel.count, 8)
        XCTAssertGreaterThanOrEqual(sel.count, 6)
        // Sorted and spaced.
        for i in 1..<sel.count {
            XCTAssertGreaterThan(sel[i].time, sel[i - 1].time)
        }
    }

    func testPeriodicFallbackOnStaticScreen() {
        let samples = (0..<600).map { FrameSample(time: Double($0) * 0.1, thumb: thumb(UInt8(100 + $0 % 2))) }
        let sel = KeyframeSelector.select(samples: samples, annotations: [], clicks: [], speechStarts: [], duration: 60)
        // Nothing ever changes: periodic and end frames are all duplicates of the start frame.
        XCTAssertEqual(sel.map(\.reasons), [["start"]])
    }

    func testClickWithSmallChangeAddsFrame() {
        // A checkbox-sized change right after the click: too small for a "screen change"
        // candidate, but the click still gets its own frame.
        let samples = (0..<100).map { FrameSample(time: Double($0) * 0.1, thumb: thumb($0 < 62 ? 50 : 52)) }
        let sel = KeyframeSelector.select(samples: samples, annotations: [], clicks: [6.0], speechStarts: [], duration: 10)
        XCTAssertTrue(sel.contains { $0.reasons.contains("click") })
    }

    func testClickWithoutVisibleChangeIsDropped() {
        let samples = (0..<100).map { FrameSample(time: Double($0) * 0.1, thumb: thumb(50)) }
        let sel = KeyframeSelector.select(samples: samples, annotations: [], clicks: [6.0], speechStarts: [], duration: 10)
        XCTAssertEqual(sel.map(\.reasons), [["start"]])
    }

    func testEmptyInputs() {
        XCTAssertEqual(KeyframeSelector.select(samples: [], annotations: [], clicks: [], speechStarts: [], duration: 0).count, 0)
        let sel = KeyframeSelector.select(samples: [], annotations: [], clicks: [], speechStarts: [], duration: 10)
        XCTAssertEqual(sel.map(\.reasons), [["start"], ["end"]])
    }
}

final class MarkdownComposerTests: XCTestCase {
    func words(_ text: String, start: Double, step: Double = 0.3) -> [Word] {
        text.split(separator: " ").enumerated().map { i, w in
            Word(text: " " + w, start: start + Double(i) * step, end: start + Double(i) * step + 0.25)
        }
    }

    func testInterleavesFramesMarkersAndWords() {
        let frames = [
            FrameRef(number: 1, time: 0.3, path: "/a/frame_01.png", pixelSize: CGSize(width: 100, height: 50)),
            FrameRef(number: 2, time: 4.0, path: "/a/frame_02.png", pixelSize: CGSize(width: 100, height: 50)),
        ]
        let w = words("so this button", start: 0.1) + words("is broken here", start: 4.2)
        let markers = [Marker(time: 0.55, text: "#1 pin at (1,2) in frame 1")]
        let paras = MarkdownComposer.transcriptParagraphs(frames: frames, words: w, markers: markers)
        XCTAssertEqual(paras.count, 2)
        XCTAssertEqual(paras[0].frame, 1)
        XCTAssertEqual(paras[0].text, "so this [#1 pin at (1,2) in frame 1] button")
        XCTAssertEqual(paras[1].frame, 2)
        XCTAssertEqual(paras[1].text, "is broken here")
    }

    func testSimultaneousFramesShareParagraph() {
        let size = CGSize(width: 10, height: 10)
        let frames = [
            FrameRef(number: 1, time: 0.2, path: "/f1.png", pixelSize: size),
            FrameRef(number: 2, time: 0.2, path: "/f2.png", pixelSize: size),
            FrameRef(number: 3, time: 0.2, path: "/f3.png", pixelSize: size),
            FrameRef(number: 4, time: 6, path: "/f4.png", pixelSize: size),
        ]
        let w = words("look here", start: 1) + words("and there", start: 6.5)
        let paras = MarkdownComposer.transcriptParagraphs(frames: frames, words: w, markers: [])
        XCTAssertEqual(paras.map(\.frames), [[1, 2, 3], [4]])
        XCTAssertEqual(paras.map(\.text), ["look here", "and there"])
    }

    func testLongPauseStartsNewParagraph() {
        let frames = [FrameRef(number: 1, time: 0, path: "/f.png", pixelSize: CGSize(width: 1, height: 1))]
        let w = words("first part", start: 0) + words("second part", start: 10)
        let paras = MarkdownComposer.transcriptParagraphs(frames: frames, words: w, markers: [])
        XCTAssertEqual(paras.map(\.text), ["first part", "second part"])
        XCTAssertEqual(paras[1].frame, nil)
        XCTAssertEqual(paras[1].time, 10, accuracy: 1e-9)
    }

    func testComposeContainsPathsAndSections() {
        let frames = [FrameRef(
            number: 1, time: 0, path: "/caps/x/frame_01.png", pixelSize: CGSize(width: 1568, height: 1018),
            detail: DetailRef(path: "/caps/x/frame_01_detail.png", pixelSize: CGSize(width: 1200, height: 750), regionInFrame: "(1,2)-(3,4)")
        )]
        let doc = CaptureDocument(
            mode: .snapshot, startDate: Date(timeIntervalSince1970: 0), duration: 12.4, folder: "/caps/x", frames: frames,
            words: words("hello there", start: 0.5), markers: [], annotationLines: ["#1 pin at (1,1) in frame 1 · 00:00"], hasClicks: false
        )
        let md = MarkdownComposer.compose(doc)
        XCTAssertTrue(md.contains("/caps/x/frame_01.png"))
        XCTAssertTrue(md.contains("detail of frame 1, region (1,2)-(3,4), 1200×750: /caps/x/frame_01_detail.png"))
        XCTAssertTrue(md.contains("## Transcript"))
        XCTAssertTrue(md.contains("**[00:00 · frame 1]** hello there"))
        XCTAssertTrue(md.contains("## Annotations"))
        XCTAssertTrue(md.contains("0:12"))
    }

    func testNoSpeech() {
        let doc = CaptureDocument(
            mode: .walkthrough, startDate: Date(), duration: 3, folder: "/x", frames: [],
            words: [], markers: [], annotationLines: [], hasClicks: true
        )
        let md = MarkdownComposer.compose(doc)
        XCTAssertTrue(md.contains("_(no speech detected)_"))
        XCTAssertTrue(md.contains("[click at (x,y)]"))
    }

    func testTimecode() {
        XCTAssertEqual(Timecode.clock(65.9), "01:05")
        XCTAssertEqual(Timecode.clock(3725), "1:02:05")
        XCTAssertEqual(Timecode.duration(47.4), "0:47")
    }
}

final class RenderingTests: XCTestCase {
    func testRenderAnnotatedProducesRequestedSizeAndDrawsStroke() throws {
        let canvas = CGSize(width: 200, height: 100)
        let ctx = try XCTUnwrap(ImageRendering.makeContext(width: 400, height: 200))
        ctx.setFillColor(CGColor(gray: 1, alpha: 1))
        ctx.fill(CGRect(x: 0, y: 0, width: 400, height: 200))
        let base = try XCTUnwrap(ctx.makeImage())
        var box = Annotation(id: 1, tool: .rectangle, points: [CGPoint(x: 50, y: 25), CGPoint(x: 150, y: 75)], color: .red, startTime: 0)
        box.finalize(endTime: 0)
        let out = try XCTUnwrap(ImageRendering.renderAnnotated(image: base, canvasSize: canvas, annotations: [box], outputSize: CGSize(width: 200, height: 100)))
        XCTAssertEqual(out.width, 200)
        XCTAssertEqual(out.height, 100)
        // Pixel on the box's top edge (canvas y=25 → image row 25 from top) should be reddish.
        let px = pixel(out, x: 100, y: 25)
        XCTAssertGreaterThan(px.r, 200)
        XCTAssertLessThan(px.g, 150)
        // Box interior stays white.
        let inner = pixel(out, x: 100, y: 50)
        XCTAssertGreaterThan(inner.g, 240)
        XCTAssertNotNil(ImageRendering.pngData(out))
    }

    func testCropRendering() throws {
        let canvas = CGSize(width: 200, height: 100)
        let ctx = try XCTUnwrap(ImageRendering.makeContext(width: 200, height: 100))
        // Left half black, right half white (bitmap coords: x from left).
        ctx.setFillColor(CGColor(gray: 0, alpha: 1))
        ctx.fill(CGRect(x: 0, y: 0, width: 100, height: 100))
        ctx.setFillColor(CGColor(gray: 1, alpha: 1))
        ctx.fill(CGRect(x: 100, y: 0, width: 100, height: 100))
        let base = try XCTUnwrap(ctx.makeImage())
        let out = try XCTUnwrap(ImageRendering.renderAnnotated(
            image: base, canvasSize: canvas, annotations: [], outputSize: CGSize(width: 100, height: 100),
            crop: CGRect(x: 100, y: 0, width: 100, height: 100)
        ))
        XCTAssertGreaterThan(pixel(out, x: 10, y: 50).r, 240)
    }

    func pixel(_ image: CGImage, x: Int, y: Int) -> (r: Int, g: Int, b: Int) {
        let ctx = ImageRendering.makeContext(width: image.width, height: image.height)!
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
        let data = ctx.data!.assumingMemoryBound(to: UInt8.self)
        // Bitmap memory is top-row first.
        let o = y * ctx.bytesPerRow + x * 4
        return (Int(data[o]), Int(data[o + 1]), Int(data[o + 2]))
    }
}

final class MultiDisplayTests: XCTestCase {
    func thumb(_ v: UInt8) -> [UInt8] { [UInt8](repeating: v, count: 64 * 40) }

    func testActiveIntervalsFromTimeline() {
        let iv = KeyframeSelector.activeIntervals(timeline: [(0, 0), (4, 2), (9, 0)], displayCount: 3, duration: 12)
        XCTAssertEqual(iv[0], [0...4, 9...12])
        XCTAssertEqual(iv[1], [])
        XCTAssertEqual(iv[2], [4...9])
    }

    func testInactiveDisplayChangesAreIgnored() {
        // Screen keeps changing (e.g. a video) but the cursor was never on this display.
        let samples = (0..<200).map { FrameSample(time: Double($0) * 0.1, thumb: thumb(Int(Double($0) * 0.1 / 2) % 2 == 0 ? 30 : 220)) }
        let sel = KeyframeSelector.select(
            samples: samples, annotations: [], clicks: [], speechStarts: [], duration: 20,
            activeIntervals: [], includeStart: false, includeEnd: false
        )
        XCTAssertEqual(sel.count, 0)
    }

    func testDisplaySwitchIsMandatoryWhenUserStays() {
        let samples = (0..<200).map { FrameSample(time: Double($0) * 0.1, thumb: thumb(80)) }
        let sel = KeyframeSelector.select(
            samples: samples, annotations: [], clicks: [], speechStarts: [], duration: 20,
            activeIntervals: [5...12], includeStart: false, includeEnd: false
        )
        XCTAssertEqual(sel.map(\.reasons), [["display switch"]])
        XCTAssertTrue(sel[0].mandatory)
        XCTAssertEqual(sel[0].time, 5.4, accuracy: 1e-9)
        // Just passing through (< 2 s) is optional only.
        let brief = KeyframeSelector.select(
            samples: samples, annotations: [], clicks: [], speechStarts: [], duration: 20,
            activeIntervals: [5...6], includeStart: false, includeEnd: false
        )
        XCTAssertFalse(brief.first?.mandatory ?? false)
    }

    func testAnnotationsKeptEvenOutsideActiveIntervals() {
        var a = Annotation(id: 1, tool: .pin, points: [.zero], color: .red, startTime: 3, screen: 1)
        a.finalize(endTime: 3)
        let sel = KeyframeSelector.select(
            samples: [], annotations: [a], clicks: [], speechStarts: [], duration: 10,
            activeIntervals: [], includeStart: false, includeEnd: false
        )
        XCTAssertEqual(sel.map(\.annotationIDs), [[1]])
    }

    func testMergeKeepsMandatoryAndFillsByScore() {
        let a = [
            KeyframeSelection(time: 0.3, reasons: ["start"], annotationIDs: [], score: 50, mandatory: true),
            KeyframeSelection(time: 5, reasons: ["screen change"], annotationIDs: [], score: 2),
            KeyframeSelection(time: 8, reasons: ["speech"], annotationIDs: [], score: 0.6),
        ]
        let b = [
            KeyframeSelection(time: 4, reasons: ["display switch"], annotationIDs: [], score: 40, mandatory: true),
            KeyframeSelection(time: 6, reasons: ["screen change"], annotationIDs: [], score: 3),
        ]
        let merged = KeyframeSelector.merge([a, b], maxFrames: 4)
        XCTAssertEqual(merged.map(\.selection.time), [0.3, 4, 5, 6])
        XCTAssertEqual(merged.map(\.display), [0, 1, 0, 1])
        // Mandatory frames survive even a tiny budget.
        XCTAssertEqual(KeyframeSelector.merge([a, b], maxFrames: 1).count, 2)
    }

    func testDisplayLabelInMarkdown() {
        let frames = [
            FrameRef(number: 1, time: 0, path: "/a.png", pixelSize: CGSize(width: 10, height: 10), display: "Built-in"),
            FrameRef(number: 2, time: 3, path: "/b.png", pixelSize: CGSize(width: 10, height: 10), display: "DELL (2)"),
        ]
        let doc = CaptureDocument(mode: .walkthrough, startDate: Date(), duration: 5, folder: "/", frames: frames, words: [], markers: [], annotationLines: [], hasClicks: false)
        let md = MarkdownComposer.compose(doc)
        XCTAssertTrue(md.contains("frame 2 · 00:03 · display \"DELL (2)\""))
        XCTAssertTrue(md.contains("several displays"))
    }
}

final class LanguageListTests: XCTestCase {
    func testListMatchesWhisperKit() {
        XCTAssertEqual(Set(WhisperLanguage.all.map(\.code)), Constants.languageCodes)
        XCTAssertEqual(WhisperLanguage.all.count, 100)
    }

    func testListIsAlphabetical() {
        let names = WhisperLanguage.all.map(\.name)
        XCTAssertEqual(names, names.sorted())
    }

    func testMenuTitleStartsWithTheEnglishName() {
        XCTAssertEqual(WhisperLanguage.named(code: "ru")?.menuTitle, "Russian  Русский")
        XCTAssertEqual(WhisperLanguage.named(code: "en")?.menuTitle, "English")
        for language in WhisperLanguage.all {
            XCTAssertTrue(language.menuTitle.hasPrefix(language.name))
        }
    }
}

final class LanguageSettingTests: XCTestCase {
    func testMissingOrUnknownSettingIsEnglish() {
        XCTAssertEqual(TranscriptionLanguage(storage: nil), .english)
        XCTAssertEqual(TranscriptionLanguage(storage: "tlh"), .english)
    }

    func testSettingRoundTrips() {
        for language in [TranscriptionLanguage.auto, .english, TranscriptionLanguage(argument: "yue")!] {
            XCTAssertEqual(TranscriptionLanguage(storage: language.storageValue), language)
        }
    }

    func testPersistedModelsStillDecode() {
        XCTAssertEqual(WhisperModel(rawValue: "openai_whisper-large-v3-v20240930_turbo_632MB"), .largeTurbo)
        XCTAssertEqual(WhisperModel(rawValue: "openai_whisper-small.en"), .smallEn)
        XCTAssertEqual(WhisperModel(rawValue: "openai_whisper-base.en"), .baseEn)
    }

    func testEnglishNeedsNoWarningOnAnyModel() {
        for model in WhisperModel.allCases {
            XCTAssertEqual(LanguagePlan.resolve(selection: .english, model: model), LanguagePlan(language: "en", warning: nil))
        }
    }

    func testEnglishOnlyModelWarnsForAnythingElse() {
        let russian = TranscriptionLanguage(argument: "ru")!
        for selection in [russian, .auto] {
            for model in [WhisperModel.smallEn, .baseEn] {
                let plan = LanguagePlan.resolve(selection: selection, model: model)
                XCTAssertEqual(plan.language, "en")
                XCTAssertNotNil(plan.warning)
            }
        }
    }

    func testAutoLeavesDetectionToWhisper() {
        XCTAssertEqual(LanguagePlan.resolve(selection: .auto, model: .base), LanguagePlan(language: nil, warning: nil))
    }

    func testCantoneseDecodesAsChinese() {
        let cantonese = TranscriptionLanguage(argument: "yue")!
        for model in [WhisperModel.largeTurbo, .small, .base] {
            XCTAssertEqual(LanguagePlan.resolve(selection: cantonese, model: model), LanguagePlan(language: "zh", warning: nil))
        }
    }

    func testSuggestedModelResolvesTheWarning() {
        XCTAssertNil(LanguagePlan.resolve(selection: TranscriptionLanguage(argument: "ru")!, model: .small).warning)
    }
}

final class TranscriptTextTests: XCTestCase {
    private func word(_ text: String, _ start: Double) -> Word {
        Word(text: text, start: start, end: start + 0.1)
    }

    private func transcript(_ words: [Word], markers: [Marker] = []) -> String {
        let frame = FrameRef(number: 1, time: 0, path: "/f.png", pixelSize: CGSize(width: 100, height: 100))
        return MarkdownComposer.transcriptParagraphs(frames: [frame], words: words, markers: markers).map(\.text).joined(separator: "\n")
    }

    func testSpacedWordsKeepTheirSeparators() {
        XCTAssertEqual(transcript([word(" This", 0), word(" save", 1), word("button", 2)]), "This save button")
    }

    func testJapaneseFragmentsJoinWithoutSpaces() {
        let words = ["これは", "設", "定", "画", "面", "です。", "この"].enumerated().map { word($1, Double($0)) }
        XCTAssertEqual(transcript(words), "これは設定画面です。この")
    }

    func testLatinTermInsideChineseKeepsItsOwnSpace() {
        XCTAssertEqual(transcript([word("点击", 0), word(" Save", 1), word("按", 2), word("钮", 3)]), "点击 Save按钮")
    }

    func testMarkIsSetOffInsideUnspacedText() {
        let marker = Marker(time: 1.5, text: "#1 pin at (1,2) in frame 1")
        XCTAssertEqual(transcript([word("設", 1), word("定", 2)], markers: [marker]), "設 [#1 pin at (1,2) in frame 1] 定")
    }

    func testKoreanStaysSpaced() {
        XCTAssertEqual(transcript([word(" 저장", 0), word("버튼", 1)]), "저장 버튼")
    }

    func testThaiCombiningMarksMergeIntoTheirCharacter() {
        let merged = TranscriptText.mergedFragments([word("น", 0), word("ี่", 0.1), word("ค", 0.2), word("ื", 0.3), word("อ", 0.4)])
        XCTAssertEqual(merged.map(\.text), ["นี่", "คื", "อ"])
        XCTAssertEqual(merged[0].start, 0)
        XCTAssertEqual(merged[0].end, 0.2, accuracy: 0.001)
    }

    func testBlankFragmentBecomesLeadingSpace() {
        let merged = TranscriptText.mergedFragments([word("ค่า", 0), word(" ", 1), word("ภ", 2)])
        XCTAssertEqual(merged.map(\.text), ["ค่า", " ภ"])
        XCTAssertEqual(transcript(merged), "ค่า ภ")
    }

    func testSpacedWordsAreLeftAlone() {
        let words = [word(" Вот", 0), word(" страница", 1), word(" настроек.", 2)]
        XCTAssertEqual(TranscriptText.mergedFragments(words), words)
    }

    func testJoinedSegments() {
        XCTAssertEqual(TranscriptText.joined(["One.", "Two."]), "One. Two.")
        XCTAssertEqual(TranscriptText.joined(["一。", "二。"]), "一。二。")
        XCTAssertEqual(TranscriptText.joined([]), "")
    }
}
