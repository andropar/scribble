import AVFoundation
import AppKit
import CoreMedia
import Foundation
import ScreenCaptureKit
import ScribbleCore

enum Screenshotter {
    /// Native-resolution screenshot of `screen`, excluding this app's own windows.
    static func capture(screen: NSScreen) async throws -> CGImage {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        let id = Screens.displayID(of: screen)
        guard let display = content.displays.first(where: { $0.displayID == id }) else {
            throw AppError("Display not available for capture")
        }
        let pid = ProcessInfo.processInfo.processIdentifier
        let ownApps = content.applications.filter { $0.processID == pid }
        let filter = SCContentFilter(display: display, excludingApplications: ownApps, exceptingWindows: [])
        let config = SCStreamConfiguration()
        let scale = CGFloat(filter.pointPixelScale)
        config.width = Int(filter.contentRect.width * scale)
        config.height = Int(filter.contentRect.height * scale)
        config.showsCursor = false
        config.captureResolution = .best
        config.colorSpaceName = CGColorSpace.sRGB
        return try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
    }
}

enum Thumbnailer {
    static let cols = 64
    static let rows = 40

    /// 64×40 luminance thumbnail of a BGRA pixel buffer (3×3 samples averaged per cell).
    static func thumb(_ pb: CVPixelBuffer) -> [UInt8] {
        CVPixelBufferLockBaseAddress(pb, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pb, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(pb) else { return [] }
        let w = CVPixelBufferGetWidth(pb), h = CVPixelBufferGetHeight(pb)
        let bpr = CVPixelBufferGetBytesPerRow(pb)
        let p = base.assumingMemoryBound(to: UInt8.self)
        var out = [UInt8](repeating: 0, count: cols * rows)
        let sub = 3
        for r in 0..<rows {
            for c in 0..<cols {
                var acc = 0
                for sy in 0..<sub {
                    let y = min(h - 1, ((r * sub + sy) * h + h / (2 * rows * sub)) / (rows * sub))
                    for sx in 0..<sub {
                        let x = min(w - 1, ((c * sub + sx) * w + w / (2 * cols * sub)) / (cols * sub))
                        let o = y * bpr + x * 4
                        acc += (Int(p[o + 2]) * 77 + Int(p[o + 1]) * 150 + Int(p[o]) * 29) >> 8
                    }
                }
                out[r * cols + c] = UInt8(acc / (sub * sub))
            }
        }
        return out
    }
}

struct RecordingResult {
    var videoURL: URL?
    /// Host time of the first video frame (= video time 0).
    var firstFrameHostTime: Double?
    /// Thumbnails with host-time timestamps.
    var samples: [FrameSample]
    var pixelSize: CGSize
}

/// Records one display with ScreenCaptureKit into an HEVC .mp4 while computing live
/// luminance thumbnails for keyframe selection.
final class WalkthroughRecorder: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    private let queue = DispatchQueue(label: "com.jroth.voiceannotate.frames", qos: .userInitiated)
    private var stream: SCStream?
    private var writer: AVAssetWriter?
    private var input: AVAssetWriterInput?
    private var adaptor: AVAssetWriterInputPixelBufferAdaptor?
    private var outputURL: URL?
    private var firstPTS: CMTime?
    private var lastPTS: CMTime?
    private var firstHost: Double?
    private var lastHost: Double?
    private var samples: [FrameSample] = []
    private(set) var pixelSize: CGSize = .zero
    private var stopped = false

    var onError: (@MainActor (Error) -> Void)?

    static let frameRate: Int32 = 10

    func start(displayID id: CGDirectDisplayID, excludingWindowIDs excludedIDs: Set<CGWindowID>, outputURL: URL, maxWidth: Int = 3840) async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        guard let display = content.displays.first(where: { $0.displayID == id }) else {
            throw AppError("Display not available for capture")
        }
        let excluded = content.windows.filter { excludedIDs.contains($0.windowID) }
        let filter = SCContentFilter(display: display, excludingWindows: excluded)

        let scale = CGFloat(filter.pointPixelScale)
        var w = filter.contentRect.width * scale
        var h = filter.contentRect.height * scale
        if w > CGFloat(maxWidth) {
            h = h * CGFloat(maxWidth) / w
            w = CGFloat(maxWidth)
        }
        let width = Int(w) & ~1, height = Int(h) & ~1
        pixelSize = CGSize(width: width, height: height)

        let config = SCStreamConfiguration()
        config.width = width
        config.height = height
        config.minimumFrameInterval = CMTime(value: 1, timescale: Self.frameRate)
        config.pixelFormat = kCVPixelFormatType_32BGRA
        config.showsCursor = true
        config.queueDepth = 6
        config.colorSpaceName = CGColorSpace.sRGB
        config.capturesAudio = false

        let writer = try AVAssetWriter(outputURL: outputURL, fileType: .mp4)
        let bitrate = max(4_000_000, width * height * 3 / 2)
        let settings: [String: Any] = [
            AVVideoCodecKey: AVVideoCodecType.hevc,
            AVVideoWidthKey: width,
            AVVideoHeightKey: height,
            AVVideoCompressionPropertiesKey: [
                AVVideoAverageBitRateKey: bitrate,
                AVVideoExpectedSourceFrameRateKey: Int(Self.frameRate),
                AVVideoMaxKeyFrameIntervalKey: Int(Self.frameRate) * 3,
            ] as [String: Any],
        ]
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
        input.expectsMediaDataInRealTime = true
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: nil)
        guard writer.canAdd(input) else { throw AppError("Cannot configure video writer") }
        writer.add(input)
        guard writer.startWriting() else {
            throw writer.error ?? AppError("Cannot start video writer")
        }
        queue.sync {
            self.writer = writer
            self.input = input
            self.adaptor = adaptor
            self.outputURL = outputURL
            self.samples = []
            self.samples.reserveCapacity(6000)
        }

        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        try stream.addStreamOutput(self, type: .screen, sampleHandlerQueue: queue)
        try await stream.startCapture()
        self.stream = stream
        Log.capture.info("Walkthrough recording \(width)x\(height) started")
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen, !stopped, sampleBuffer.isValid else { return }
        guard
            let attachments = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
            let info = attachments.first,
            let rawStatus = info[.status] as? Int,
            SCFrameStatus(rawValue: rawStatus) == .complete,
            let pixelBuffer = sampleBuffer.imageBuffer,
            let writer, let input, let adaptor
        else { return }

        let pts = sampleBuffer.presentationTimeStamp
        if let lastPTS, pts <= lastPTS { return }
        let host = (info[.displayTime] as? UInt64).map(HostClock.seconds(fromMach:)) ?? HostClock.now()

        if firstPTS == nil {
            writer.startSession(atSourceTime: pts)
            firstPTS = pts
            firstHost = host
        }
        if input.isReadyForMoreMediaData {
            if !adaptor.append(pixelBuffer, withPresentationTime: pts) {
                Log.capture.error("Frame append failed: \(writer.error?.localizedDescription ?? "unknown", privacy: .public)")
            }
        }
        lastPTS = pts
        lastHost = host
        samples.append(FrameSample(time: host, thumb: Thumbnailer.thumb(pixelBuffer)))
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        Log.capture.error("Stream stopped: \(error.localizedDescription, privacy: .public)")
        if let onError {
            DispatchQueue.main.async { MainActor.assumeIsolated { onError(error) } }
        }
    }

    func stop() async -> RecordingResult {
        if let stream {
            do {
                try await stream.stopCapture()
            } catch {
                Log.capture.error("stopCapture failed: \(error.localizedDescription, privacy: .public)")
            }
        }
        stream = nil
        let stopHost = HostClock.now()
        return await withCheckedContinuation { (cont: CheckedContinuation<RecordingResult, Never>) in
            queue.async {
                self.stopped = true
                let size = self.pixelSize
                let samples = self.samples
                guard let writer = self.writer, let input = self.input, let lastPTS = self.lastPTS, let lastHost = self.lastHost else {
                    self.writer?.cancelWriting()
                    cont.resume(returning: RecordingResult(videoURL: nil, firstFrameHostTime: nil, samples: samples, pixelSize: size))
                    return
                }
                let firstHost = self.firstHost
                let url = self.outputURL
                let tail = max(0.1, stopHost - lastHost)
                input.markAsFinished()
                writer.endSession(atSourceTime: CMTimeAdd(lastPTS, CMTime(seconds: tail, preferredTimescale: 600)))
                writer.finishWriting {
                    if writer.status == .completed {
                        cont.resume(returning: RecordingResult(videoURL: url, firstFrameHostTime: firstHost, samples: samples, pixelSize: size))
                    } else {
                        Log.capture.error("Video writing failed: \(writer.error?.localizedDescription ?? "unknown", privacy: .public)")
                        cont.resume(returning: RecordingResult(videoURL: nil, firstFrameHostTime: nil, samples: samples, pixelSize: size))
                    }
                }
            }
        }
    }
}

enum VideoTools {
    /// Decodes full-resolution frames at the given video times (seconds), one at a time so
    /// only a single frame is in memory.
    static func forEachFrame(in url: URL, at times: [Double], _ body: (Int, CGImage) throws -> Void) async throws {
        let asset = AVURLAsset(url: url)
        let duration = try await asset.load(.duration).seconds
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.requestedTimeToleranceBefore = .zero
        generator.requestedTimeToleranceAfter = .zero
        for (i, t) in times.enumerated() {
            let clamped = min(max(t, 0), max(duration - 0.02, 0))
            let (image, _) = try await generator.image(at: CMTime(seconds: clamped, preferredTimescale: 600))
            try autoreleasepool { try body(i, image) }
        }
    }

    /// Muxes the screen video and the mic audio into one .mp4 (passthrough, no re-encode).
    /// `audioOffset` = audio start − video start, in seconds.
    static func mux(video: URL, audio: URL, audioOffset: Double, output: URL) async throws {
        let videoAsset = AVURLAsset(url: video)
        let audioAsset = AVURLAsset(url: audio)
        let composition = AVMutableComposition()
        guard
            let vTrack = try await videoAsset.loadTracks(withMediaType: .video).first,
            let aTrack = try await audioAsset.loadTracks(withMediaType: .audio).first,
            let compV = composition.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid),
            let compA = composition.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)
        else { throw AppError("Missing tracks for mux") }

        let vDuration = try await videoAsset.load(.duration)
        let aDuration = try await audioAsset.load(.duration)
        try compV.insertTimeRange(CMTimeRange(start: .zero, duration: vDuration), of: vTrack, at: .zero)

        let offset = CMTime(seconds: abs(audioOffset), preferredTimescale: 600)
        if audioOffset >= 0 {
            try compA.insertTimeRange(CMTimeRange(start: .zero, duration: aDuration), of: aTrack, at: offset)
        } else if offset < aDuration {
            try compA.insertTimeRange(CMTimeRange(start: offset, duration: aDuration - offset), of: aTrack, at: .zero)
        }

        guard let export = AVAssetExportSession(asset: composition, presetName: AVAssetExportPresetPassthrough) else {
            throw AppError("Cannot create export session")
        }
        try? FileManager.default.removeItem(at: output)
        try await export.export(to: output, as: .mp4)
    }
}
