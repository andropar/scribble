import Foundation
import VoiceAnnotateCore
import WhisperKit

/// Audio that is still being recorded (`AudioRecorder`).
protocol LiveSampleSource: AnyObject, Sendable {
    var sampleCount: Int { get }
    func samples(from start: Int, count: Int) -> [Float]
}

/// Transcribes a recording while it is still running, so finishing only has to transcribe
/// the last half minute.
///
/// WhisperKit transcribes long audio by cutting it into pieces of at most 30 s (each ends in
/// the longest silence of its second half) and decoding every piece on its own. Where a
/// piece ends depends only on the 30 s after its start, so it is known as soon as that much
/// has been recorded. This class makes the same cuts with WhisperKit's own chunker and
/// decodes each piece once it is complete; `finish` decodes the rest. The pieces, their
/// decoding options and the way results are joined are those of the one-pass path, so the
/// transcript is the same apart from Whisper's own run-to-run variation (`--eval-live`
/// checks this on recordings). If anything fails, `finish` returns nil and the caller
/// transcribes the whole recording in one pass, as before.
@MainActor
final class LiveTranscriber {
    private let recorder: LiveSampleSource
    private let model: String
    private let language: TranscriptionLanguage
    private let vocabulary: String
    /// Samples already cut into pieces.
    private var committed = 0
    private var results: [TranscriptionResult] = []
    private var warning: String?
    private var inFlight: Task<Void, Never>?
    private var failed = false
    private var timer: Timer?

    init(recorder: LiveSampleSource, model: String, language: TranscriptionLanguage, vocabulary: String) {
        self.recorder = recorder
        self.model = model
        self.language = language
        self.vocabulary = vocabulary
        let timer = Timer(timeInterval: 0.5, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.poll() }
        }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    private func poll() {
        // One piece at a time. If decoding falls behind, the pieces wait; whatever is still
        // undone when the recording ends is decoded by `finish`.
        guard inFlight == nil, !failed, Transcriber.shared.status.isReady else { return }
        // Nothing to cut before more than 30 s have come in since the last piece.
        guard recorder.sampleCount - committed > Self.minimumForCut else { return }
        inFlight = Task { [weak self] in
            await self?.cutAndDecode()
            self?.inFlight = nil
        }
    }

    private func cutAndDecode() async {
        do {
            let (chunker, window) = try await Transcriber.shared.chunker(model: model)
            // A cut needs audio beyond the 30 s it is chosen from, like the one-pass chunker.
            let slice = recorder.samples(from: committed, count: window + 1)
            guard let cut = try await Self.firstCut(slice, chunker: chunker, window: window) else { return }
            let offset = committed
            committed += cut
            try await decode(Array(slice[..<cut]), offset: offset)
        } catch {
            Log.transcribe.error("Live transcription failed: \(error.localizedDescription, privacy: .public)")
            failed = true
        }
    }

    private func decode(_ samples: [Float], offset: Int) async throws {
        let decoded = try await Transcriber.shared.decode(samples, model: model, language: language, vocabulary: vocabulary)
        results += Self.shifted(decoded.results, by: offset)
        warning = decoded.warning ?? warning
    }

    /// The transcript of `samples` (the full recording), or nil if nothing was transcribed
    /// while recording or a piece failed: the caller then transcribes it in one pass.
    func finish(samples: [Float]) async -> TranscriptionOutput? {
        timer?.invalidate()
        timer = nil
        await inFlight?.value
        guard !failed, committed > 0, committed <= samples.count else { return nil }
        guard Transcriber.hasSpeech(samples) else { return TranscriptionOutput(words: [], segments: []) }
        let started = Date()
        let rest = samples.count - committed
        // The one-pass chunker stops when less than a second is left.
        if rest > Self.endPadding {
            do {
                try await decode(Array(samples[committed...]), offset: committed)
            } catch {
                Log.transcribe.error("Live transcription failed at the end: \(error.localizedDescription, privacy: .public)")
                return nil
            }
        }
        var output = Transcriber.convert(results, duration: Double(samples.count) / AudioResult.sampleRate)
        output.warning = warning
        Log.transcribe.notice("Live transcription: \(String(format: "%.1f", Double(samples.count) / AudioResult.sampleRate), privacy: .public)s audio, \(String(format: "%.1f", Double(rest) / AudioResult.sampleRate), privacy: .public)s left at the end took \(String(format: "%.2f", Date().timeIntervalSince(started)), privacy: .public)s, \(output.words.count) words")
        return output
    }

    func cancel() {
        timer?.invalidate()
        timer = nil
        inFlight?.cancel()
    }

    // MARK: Shared with the `--eval-live` dev command

    /// `VADAudioChunker.windowPadding`: audio shorter than this after the last cut is ignored.
    nonisolated static let endPadding = 16000
    /// `Constants.defaultWindowSamples`: 30 s. The exact window comes from the loaded model.
    nonisolated static let minimumForCut = 480_000

    /// Where the piece starting at the beginning of `audio` ends, or nil while `audio` is not
    /// longer than `window` (the piece may still be the last one, which is not cut).
    nonisolated static func firstCut(_ audio: [Float], chunker: VADAudioChunker, window: Int) async throws -> Int? {
        guard audio.count > window else { return nil }
        let chunks = try await chunker.chunkAll(audioArray: Array(audio[...window]), maxChunkLength: window, decodeOptions: nil)
        guard let first = chunks.first, first.seekOffsetIndex == 0, !first.audioSamples.isEmpty else { return nil }
        return first.audioSamples.count
    }

    /// Results of a piece, moved to where the piece starts in the recording.
    nonisolated static func shifted(_ results: [TranscriptionResult], by offset: Int) -> [TranscriptionResult] {
        VADAudioChunker().updateSeekOffsetsForResults(
            chunkedResults: [.success(results)],
            audioChunks: [AudioChunk(seekOffsetIndex: offset, audioSamples: [])]
        )
    }

    /// The transcript `LiveTranscriber` produces for a finished recording: cut as it would
    /// have been cut while recording, every piece decoded on its own. Also returns the cuts
    /// and how long decoding the audio after the last cut took (the wait after finishing).
    static func replay(_ samples: [Float], model: String, language: TranscriptionLanguage, vocabulary: String) async throws -> (output: TranscriptionOutput, cuts: [Int], wait: Double) {
        let (chunker, window) = try await Transcriber.shared.chunker(model: model)
        var cuts: [Int] = []
        var results: [TranscriptionResult] = []
        var committed = 0
        while let cut = try await firstCut(Array(samples[committed..<min(samples.count, committed + window + 1)]), chunker: chunker, window: window) {
            let decoded = try await Transcriber.shared.decode(Array(samples[committed..<(committed + cut)]), model: model, language: language, vocabulary: vocabulary)
            results += shifted(decoded.results, by: committed)
            committed += cut
            cuts.append(committed)
        }
        guard committed > 0 else {
            let started = Date()
            let output = try await Transcriber.shared.transcribe(samples, model: model, language: language, vocabulary: vocabulary)
            return (output, [], Date().timeIntervalSince(started))
        }
        guard Transcriber.hasSpeech(samples) else { return (TranscriptionOutput(words: [], segments: []), cuts, 0) }
        let started = Date()
        if samples.count - committed > endPadding {
            let decoded = try await Transcriber.shared.decode(Array(samples[committed...]), model: model, language: language, vocabulary: vocabulary)
            results += shifted(decoded.results, by: committed)
        }
        let output = Transcriber.convert(results, duration: Double(samples.count) / AudioResult.sampleRate)
        return (output, cuts, Date().timeIntervalSince(started))
    }
}
