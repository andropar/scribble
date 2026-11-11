import CoreML
import Foundation
import VoiceAnnotateCore
import WhisperKit

struct TranscriptionOutput {
    /// Times relative to the start of the audio.
    var words: [Word]
    var segments: [SpeechSegment]
    /// Languages the speech was decoded in, in order of first use.
    var languages: [String] = []
    /// Why the transcript may be wrong; shown to the user.
    var warning: String?

    /// The transcript as plain prose.
    var plainText: String {
        TranscriptText.joined(segments.map(\.text))
    }
}

/// On-device transcription with WhisperKit. The model is downloaded once into
/// `~/.voice-annotate/models` and kept loaded.
@MainActor
final class Transcriber: ObservableObject {
    enum Status: Equatable {
        case idle
        case downloading(Double)
        case loading
        /// Usable; `optimizing` = a faster Neural Engine build is compiling in the background.
        case ready(optimizing: Bool)
        case failed(String)

        var title: String {
            switch self {
            case .idle: return "Transcription model not loaded"
            case let .downloading(p): return "Downloading model… \(Int(p * 100))%"
            case .loading: return "Loading transcription model…"
            case .ready(optimizing: false): return "Transcription model ready"
            case .ready(optimizing: true): return "Transcription ready (optimizing for Neural Engine in background)"
            case let .failed(msg): return "Model failed: \(msg)"
            }
        }

        var isReady: Bool {
            if case .ready = self { return true }
            return false
        }
    }

    static let shared = Transcriber()

    @Published private(set) var status: Status = .idle
    private var loadTask: Task<WhisperKit, Error>?
    private var upgradeTask: Task<Void, Never>?
    /// "model|compute" of the loaded (or loading) model.
    private var loadedKey: String?

    func preload(_ model: String) {
        _ = ensureLoaded(model)
    }

    /// Returns a task resolving to a usable model. In `.auto` mode a quick-loading build
    /// (encoder on GPU) is returned first, while the Neural Engine build compiles in the
    /// background (minutes on first use, seconds once cached) and replaces it when ready.
    @discardableResult
    func ensureLoaded(_ model: String) -> Task<WhisperKit, Error> {
        let compute = AppSettings.shared.compute
        let key = "\(model)|\(compute.rawValue)"
        if let loadTask, loadedKey == key { return loadTask }
        loadedKey = key
        upgradeTask?.cancel()
        upgradeTask = nil

        let encoder: MLComputeUnits = compute == .neuralEngine ? .cpuAndNeuralEngine : .cpuAndGPU
        let task = Task<WhisperKit, Error> {
            self.status = .downloading(0)
            let folder = try await Self.modelFolder(model) { progress in
                Task { @MainActor in
                    if case .downloading = Transcriber.shared.status {
                        Transcriber.shared.status = .downloading(progress)
                    }
                }
            }
            self.status = .loading
            return try await Self.load(model: model, folder: folder, encoder: encoder)
        }
        loadTask = task
        Task {
            do {
                _ = try await task.value
                guard self.loadedKey == key else { return }
                Log.transcribe.notice("WhisperKit \(key, privacy: .public) ready")
                if compute == .auto {
                    self.status = .ready(optimizing: true)
                    self.startUpgrade(model: model, key: key)
                } else {
                    self.status = .ready(optimizing: false)
                }
            } catch {
                Log.transcribe.error("Model load failed: \(error.localizedDescription, privacy: .public)")
                if self.loadedKey == key {
                    self.status = .failed(error.localizedDescription)
                    self.loadTask = nil
                    self.loadedKey = nil
                }
            }
        }
        return task
    }

    private func startUpgrade(model: String, key: String) {
        upgradeTask = Task {
            do {
                let folder = try await Self.modelFolder(model) { _ in }
                let started = Date()
                let kit = try await Self.load(model: model, folder: folder, encoder: .cpuAndNeuralEngine)
                guard !Task.isCancelled, self.loadedKey == key else { return }
                self.loadTask = Task { kit }
                self.status = .ready(optimizing: false)
                Log.transcribe.notice("Switched to Neural Engine build after \(String(format: "%.0f", Date().timeIntervalSince(started)), privacy: .public)s")
            } catch {
                Log.transcribe.error("Neural Engine build failed, staying on GPU: \(error.localizedDescription, privacy: .public)")
                if self.loadedKey == key { self.status = .ready(optimizing: false) }
            }
        }
    }

    private nonisolated static func load(model: String, folder: URL, encoder: MLComputeUnits) async throws -> WhisperKit {
        let config = WhisperKitConfig(
            model: model,
            downloadBase: Paths.models,
            modelFolder: folder.path,
            tokenizerFolder: Paths.models,
            computeOptions: ModelComputeOptions(melCompute: .cpuAndGPU, audioEncoderCompute: encoder, textDecoderCompute: .cpuAndNeuralEngine),
            segmentSeeker: PatchedSegmentSeeker(),
            verbose: false,
            logLevel: .error,
            load: true,
            download: false
        )
        return try await WhisperKit(config)
    }

    private nonisolated static func modelFolder(_ model: String, progress: @escaping @Sendable (Double) -> Void) async throws -> URL {
        let key = "modelFolder.\(model)"
        if let path = UserDefaults.standard.string(forKey: key),
           FileManager.default.fileExists(atPath: (path as NSString).appendingPathComponent("AudioEncoder.mlmodelc")) {
            return URL(fileURLWithPath: path)
        }
        Paths.ensure()
        let url = try await WhisperKit.download(variant: model, downloadBase: Paths.models, progressCallback: { p in
            progress(p.fractionCompleted)
        })
        UserDefaults.standard.set(url.path, forKey: key)
        return url
    }

    func transcribe(_ samples: [Float], model: String, language: TranscriptionLanguage, vocabulary: String) async throws -> TranscriptionOutput {
        // Silence (or no mic): skip, Whisper would hallucinate.
        guard samples.count > Int(AudioResult.sampleRate / 2), AudioRecorder.rms(samples) > 0.0015 else {
            return TranscriptionOutput(words: [], segments: [])
        }
        let kit = try await ensureLoaded(model).value
        let plan = LanguagePlan.resolve(selection: language, isMultilingual: kit.textDecoder.isModelMultilingual)
        var options = DecodingOptions(
            verbose: false,
            task: .transcribe,
            language: plan.language,
            temperature: 0,
            usePrefillPrompt: true,
            detectLanguage: plan.language == nil,
            skipSpecialTokens: true,
            withoutTimestamps: false,
            wordTimestamps: true,
            chunkingStrategy: .vad
        )
        // Each retry detects the language again at a higher temperature, where it can flip:
        // clear Russian came back as Ukrainian at 0.2 and 0.4. So detection gets one attempt,
        // and the first-token check, which only abandons a window for a retry, goes with it.
        if plan.language == nil {
            options.temperatureFallbackCount = 0
            options.firstTokenLogProbThreshold = nil
        }
        let vocab = vocabulary.trimmingCharacters(in: .whitespacesAndNewlines)
        if !vocab.isEmpty, let tokenizer = kit.tokenizer {
            options.promptTokens = tokenizer.encode(text: " " + vocab).filter { $0 < tokenizer.specialTokens.specialTokenBegin }
        }
        let started = Date()
        let results = try await kit.transcribe(audioArray: samples, decodeOptions: options)
        let duration = Double(samples.count) / AudioResult.sampleRate
        var output = Self.convert(results, duration: duration)
        output.warning = plan.warning
        Log.transcribe.info("Transcribed \(String(format: "%.1f", duration), privacy: .public)s audio in \(String(format: "%.2f", Date().timeIntervalSince(started)), privacy: .public)s, \(output.words.count) words")
        return output
    }

    /// Skips anything starting after `duration`: the last window is padded to 30 s, and
    /// Whisper can repeat itself in the padding.
    nonisolated static func convert(_ results: [TranscriptionResult], duration: Double) -> TranscriptionOutput {
        var words: [Word] = []
        var segments: [SpeechSegment] = []
        var languages: [String] = []
        for result in results {
            for segment in result.segments where Double(segment.start) < duration {
                let text = cleanText(segment.text)
                if !text.isEmpty {
                    segments.append(SpeechSegment(text: text, start: Double(segment.start), end: Double(segment.end)))
                    if !languages.contains(result.language) { languages.append(result.language) }
                }
                // Blank fragments carry the spaces of scripts split into fragments.
                let kept = (segment.words ?? []).filter { isBlank($0.word) || !isNoise($0.word) }
                words += TranscriptText.mergedFragments(kept.map { Word(text: $0.word, start: Double($0.start), end: Double($0.end)) })
                    .filter { $0.start < duration }
            }
        }
        words.sort { $0.start < $1.start }
        segments.sort { $0.start < $1.start }
        return TranscriptionOutput(words: words, segments: segments, languages: languages)
    }

    private nonisolated static func isBlank(_ word: String) -> Bool {
        word.allSatisfy(\.isWhitespace)
    }

    nonisolated static func isNoise(_ word: String) -> Bool {
        let t = word.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { return true }
        if t.hasPrefix("<|") && t.hasSuffix("|>") { return true }
        if t.hasPrefix("[") && t.hasSuffix("]") { return true }
        if t.hasPrefix("(") && t.hasSuffix(")") && t.count > 2 { return true }
        return false
    }

    nonisolated static func cleanText(_ text: String) -> String {
        var t = text
        while let r = t.range(of: "<\\|[^|]*\\|>", options: .regularExpression) { t.removeSubrange(r) }
        return t.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
