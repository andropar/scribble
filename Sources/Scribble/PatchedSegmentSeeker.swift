import CoreML
import Foundation
import WhisperKit

/// WhisperKit's `SegmentSeeker` with two fixes (WhisperKit 1.1.1).
///
/// Word timings with a prompt (the custom vocabulary): alignment rows are indexed from the
/// start of the decoder sequence, which begins with `<|startofprev|>` and the prompt, but the
/// tokens they are matched with start at `<|startoftranscript|>`. Every word then reads the
/// timing of a row `prompt + 1` too early, moving mark references onto earlier words.
/// Dropping those rows first lines them up again.
///
/// Windows cut off at the token limit: a window's prompt and text share 224 tokens, so dense
/// speech, scripts that take many tokens, or a long prompt can stop decoding early, and
/// WhisperKit then skips the rest of the window. Here the next window starts where the text
/// stopped instead.
struct PatchedSegmentSeeker: SegmentSeeking {
    private let seeker = SegmentSeeker()

    func findSeekPointAndSegments(
        decodingResult: DecodingResult,
        options: DecodingOptions,
        allSegmentsCount: Int,
        currentSeek seek: Int,
        segmentSize: Int,
        sampleRate: Int,
        timeToken: Int,
        specialToken: Int,
        tokenizer: WhisperTokenizer
    ) -> (Int, [TranscriptionSegment]?) {
        let (nextSeek, segments) = seeker.findSeekPointAndSegments(
            decodingResult: decodingResult, options: options, allSegmentsCount: allSegmentsCount,
            currentSeek: seek, segmentSize: segmentSize, sampleRate: sampleRate,
            timeToken: timeToken, specialToken: specialToken, tokenizer: tokenizer
        )
        guard let segments, options.wordTimestamps, Self.isCutOff(segments, options: options, tokenizer: tokenizer) else {
            return (nextSeek, segments)
        }
        // WhisperKit moves on to the end of the last segment after adding word timestamps,
        // which `droppingLastWord` sets. The second is a floor, so the window loop always ends.
        return (seek + sampleRate, segments)
    }

    func addWordTimestamps(
        segments: [TranscriptionSegment],
        alignmentWeights: MLMultiArray,
        tokenizer: WhisperTokenizer,
        seek: Int,
        segmentSize: Int,
        prependPunctuations: String,
        appendPunctuations: String,
        lastSpeechTimestamp: Float,
        options: DecodingOptions,
        timings: TranscriptionTimings
    ) throws -> [TranscriptionSegment]? {
        let rows = Self.promptRows(options: options, tokenizer: tokenizer)
        let timed = try seeker.addWordTimestamps(
            segments: segments,
            alignmentWeights: rows == 0 ? alignmentWeights : Self.dropRows(rows, from: alignmentWeights),
            tokenizer: tokenizer, seek: seek, segmentSize: segmentSize,
            prependPunctuations: prependPunctuations, appendPunctuations: appendPunctuations,
            lastSpeechTimestamp: lastSpeechTimestamp, options: options, timings: timings
        )
        guard let timed, Self.isCutOff(segments, options: options, tokenizer: tokenizer) else { return timed }
        return Self.droppingLastWord(from: timed)
    }

    /// Decoding stops one token short of the limit, and WhisperKit appends the end token.
    private static func isCutOff(_ segments: [TranscriptionSegment], options: DecodingOptions, tokenizer: WhisperTokenizer) -> Bool {
        let tokens = segments.reduce(0) { $0 + $1.tokens.count }
        return promptRows(options: options, tokenizer: tokenizer) + tokens >= Constants.maxTokenContext
    }

    /// The last word may be incomplete, and alignment stretches it to the end of the window,
    /// so it can't say where the text stopped. It is decoded again in the next window.
    private static func droppingLastWord(from segments: [TranscriptionSegment]) -> [TranscriptionSegment] {
        guard let index = segments.lastIndex(where: { !($0.words ?? []).isEmpty }) else { return segments }
        var kept = Array(segments[...index])
        var words = kept[index].words ?? []
        let last = words.removeLast()
        if words.isEmpty {
            kept.removeLast()
        } else {
            kept[index].words = words
            kept[index].text = words.map(\.word).joined()
            kept[index].end = last.start
        }
        return kept
    }

    /// Rows taken by `<|startofprev|>` and the prompt, trimmed like `prefillDecoderInputs` does.
    private static func promptRows(options: DecodingOptions, tokenizer: WhisperTokenizer) -> Int {
        let prompt = (options.promptTokens ?? [])
            .suffix(Constants.maxTokenContext / 2 - 1)
            .filter { $0 < tokenizer.specialTokens.specialTokenBegin }
        return prompt.isEmpty ? 0 : prompt.count + 1
    }

    /// Copies by row stride: Core ML pads rows, 1500 columns are stored 1504 apart.
    private static func dropRows(_ count: Int, from weights: MLMultiArray) throws -> MLMultiArray {
        let rows = weights.shape[0].intValue
        let columns = weights.shape[1].intValue
        let shifted = try MLMultiArray(shape: weights.shape, dataType: weights.dataType, initialValue: FloatType(0))
        let sourceStride = weights.strides[0].intValue
        let targetStride = shifted.strides[0].intValue
        let scalarSize = MemoryLayout<FloatType>.stride
        weights.withUnsafeBytes { source in
            shifted.withUnsafeMutableBytes { target, _ in
                for row in 0..<max(0, rows - count) {
                    memcpy(
                        target.baseAddress! + row * targetStride * scalarSize,
                        source.baseAddress! + (row + count) * sourceStride * scalarSize,
                        columns * scalarSize
                    )
                }
            }
        }
        return shifted
    }
}
