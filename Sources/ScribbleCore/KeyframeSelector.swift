import Foundation

public struct KeyframeSelection: Codable, Equatable, Sendable {
    /// Session time in seconds.
    public var time: Double
    public var reasons: [String]
    /// Annotations this frame was chosen to show.
    public var annotationIDs: [Int]
    public var score: Double
    /// Always kept (annotations, start, end) even when merging across displays.
    public var mandatory: Bool

    public init(time: Double, reasons: [String], annotationIDs: [Int], score: Double = 1, mandatory: Bool = false) {
        self.time = time
        self.reasons = reasons
        self.annotationIDs = annotationIDs
        self.score = score
        self.mandatory = mandatory
    }
}

/// Picks the keyframes that summarize a walkthrough recording.
public enum KeyframeSelector {
    public struct Config: Sendable {
        public var maxFrames = 30
        public var minFrames = 6
        /// Frame budget grows by one frame per this many seconds of recording.
        public var secondsPerFrame = 5.0
        public var minGap = 1.2
        /// Per-frame mean abs luminance diff (0…1) that counts as "screen is changing".
        public var changeThreshold = 0.004
        /// Net before/after diff of a burst that counts as a meaningful new view.
        public var significantChange = 0.012
        public var burstGap = 0.6
        public var settleDelay = 0.3
        public var longBurstInterval = 4.0
        public var annotationGroupGap = 1.5
        /// Delay after a stroke ends until it is guaranteed to be in a captured frame.
        public var annotationRenderDelay = 0.25
        /// How long annotations stay fully visible on screen (Walkthrough fade).
        public var fadeDuration = 3.0
        public var periodicInterval = 15.0
        /// Optional frames closer than this (thumbnail diff) to a neighboring chosen frame are
        /// dropped. Kept above `changeThreshold` so frames that never "changed" count as equal.
        public var duplicateThreshold = 0.005

        public init() {}
    }

    struct Candidate {
        var time: Double
        var score: Double
        var mandatory: Bool
        var reasons: Set<String>
        var annotationIDs: [Int]
    }

    /// - Parameters:
    ///   - activeIntervals: when recording several displays, the time ranges the cursor was on
    ///     this display. Optional (non-annotation) frames outside them are ignored, and each
    ///     interval start adds a "display switch" candidate. nil = always active.
    ///   - includeStart/includeEnd: whether this display gets the mandatory first/last frame.
    public static func select(
        samples: [FrameSample],
        annotations: [Annotation],
        clicks: [Double],
        speechStarts: [Double],
        duration: Double,
        activeIntervals: [ClosedRange<Double>]? = nil,
        includeStart: Bool = true,
        includeEnd: Bool = true,
        config: Config = Config()
    ) -> [KeyframeSelection] {
        let end = max(duration, 0)
        let latest = max(end - 0.05, 0)
        func clamp(_ t: Double) -> Double { min(max(t, 0), latest) }

        let samples = samples.sorted { $0.time < $1.time }
        var cands: [Candidate] = []
        var settleTimes: [Double] = []

        // 1. Activity bursts → settle frames.
        if samples.count >= 2 {
            var i = 1
            while i < samples.count {
                guard diff(samples[i - 1].thumb, samples[i].thumb) > config.changeThreshold else {
                    i += 1
                    continue
                }
                let a = i
                var b = i
                var j = i + 1
                while j < samples.count, samples[j].time - samples[b].time <= config.burstGap {
                    if diff(samples[j - 1].thumb, samples[j].thumb) > config.changeThreshold {
                        b = j
                    }
                    j += 1
                }
                let net = diff(samples[a - 1].thumb, samples[b].thumb)
                if net > config.significantChange {
                    let settle = clamp(samples[b].time + config.settleDelay)
                    cands.append(Candidate(
                        time: settle, score: 1 + min(net * 25, 3), mandatory: false,
                        reasons: ["screen change"], annotationIDs: []
                    ))
                    settleTimes.append(settle)
                }
                let span = samples[b].time - samples[a].time
                if span > config.longBurstInterval {
                    var t = samples[a].time + config.longBurstInterval
                    while t < samples[b].time - 1 {
                        cands.append(Candidate(
                            time: clamp(t), score: 0.8, mandatory: false,
                            reasons: ["ongoing change"], annotationIDs: []
                        ))
                        t += config.longBurstInterval
                    }
                }
                i = b + 1
            }
        }

        // 2. Annotation bursts → mandatory frames showing every annotation.
        var groups: [[Annotation]] = []
        for ann in annotations.sorted(by: { $0.endTime < $1.endTime }) {
            if let group = groups.last, let first = group.first, let last = group.last {
                let closeInTime = ann.startTime - last.endTime <= config.annotationGroupGap
                let firstStillVisible = ann.endTime + config.annotationRenderDelay < first.endTime + config.fadeDuration
                let screenStable = !settleTimes.contains { $0 > last.endTime && $0 < ann.startTime }
                if closeInTime && firstStillVisible && screenStable {
                    groups[groups.count - 1].append(ann)
                    continue
                }
            }
            groups.append([ann])
        }
        for group in groups {
            guard let last = group.last else { continue }
            cands.append(Candidate(
                time: clamp(last.endTime + config.annotationRenderDelay), score: 100, mandatory: true,
                reasons: ["annotation"], annotationIDs: group.map(\.id)
            ))
        }

        // 3. Clicks boost the following settle frame, or add their own.
        for c in clicks {
            if let idx = cands.firstIndex(where: {
                !$0.mandatory && $0.reasons.contains("screen change") && $0.time > c && $0.time <= c + 1.5
            }) {
                cands[idx].score += 1
                cands[idx].reasons.insert("click")
            } else {
                cands.append(Candidate(
                    time: clamp(c + 0.5), score: 1.2, mandatory: false, reasons: ["click"], annotationIDs: []
                ))
            }
        }

        // 4. Speech segment starts (low weight).
        for s in speechStarts {
            cands.append(Candidate(
                time: clamp(s + 0.2), score: 0.6, mandatory: false, reasons: ["speech"], annotationIDs: []
            ))
        }

        // 5. Display switches (kept when the user stayed ≥ 2 s, so the transcript after the
        // switch is attached to this display), first and last frame.
        for interval in activeIntervals ?? [] where interval.lowerBound > 0.5 {
            let stayed = interval.upperBound - interval.lowerBound >= 2
            cands.append(Candidate(
                time: clamp(interval.lowerBound + 0.4), score: stayed ? 40 : 1.5, mandatory: stayed,
                reasons: ["display switch"], annotationIDs: []
            ))
        }
        if end > 0 && includeStart {
            cands.append(Candidate(
                time: clamp(min(0.3, end)), score: 50, mandatory: true, reasons: ["start"], annotationIDs: []
            ))
        }
        if end > 0 && includeEnd {
            if end > 1 || !includeStart {
                cands.append(Candidate(
                    time: latest, score: 50, mandatory: true, reasons: ["end"], annotationIDs: []
                ))
            }
        }

        // Focus follows the cursor: drop optional frames from times this display wasn't used.
        if let activeIntervals {
            let grace = 1.0
            cands.removeAll { c in
                !c.mandatory && !activeIntervals.contains { c.time >= $0.lowerBound - grace && c.time <= $0.upperBound + grace }
            }
        }

        // 6. Periodic fallback for long quiet stretches.
        let times = cands.map(\.time).sorted()
        var prev = 0.0
        for t in times + [end] {
            var x = prev + config.periodicInterval
            while x < t - config.periodicInterval / 2 {
                if let activeIntervals, !activeIntervals.contains(where: { $0.contains(x) }) {
                    x += config.periodicInterval
                    continue
                }
                cands.append(Candidate(
                    time: clamp(x), score: 0.4, mandatory: false, reasons: ["periodic"], annotationIDs: []
                ))
                x += config.periodicInterval
            }
            prev = max(prev, t)
        }

        // Selection.
        let budget = min(config.maxFrames, max(config.minFrames, Int((end / config.secondsPerFrame).rounded(.up))))
        var mandatory: [Candidate] = []
        for c in cands.filter(\.mandatory).sorted(by: { $0.time < $1.time }) {
            if let lastIdx = mandatory.indices.last, abs(mandatory[lastIdx].time - c.time) < 0.35 {
                mandatory[lastIdx].time = max(mandatory[lastIdx].time, c.time)
                mandatory[lastIdx].reasons.formUnion(c.reasons)
                mandatory[lastIdx].annotationIDs += c.annotationIDs
            } else {
                mandatory.append(c)
            }
        }
        // A plain end frame is only kept if it shows something new (decided last), so it
        // doesn't suppress the earlier frame where the final view first appeared.
        var endFrame: Candidate?
        if let last = mandatory.last, last.reasons == ["end"] {
            endFrame = mandatory.removeLast()
        }
        var chosen = mandatory

        func isDuplicateOfNeighbor(_ t: Double) -> Bool {
            guard let th = thumb(at: t, in: samples) else { return false }
            let before = chosen.filter { $0.time <= t }.max { $0.time < $1.time }
            let after = chosen.filter { $0.time > t }.min { $0.time < $1.time }
            for n in [before, after].compactMap({ $0 }) {
                if let nt = thumb(at: n.time, in: samples), diff(th, nt) < config.duplicateThreshold { return true }
            }
            return false
        }

        let optionalBudget = budget - (endFrame == nil ? 0 : 1)
        let optional = cands.filter { !$0.mandatory }.sorted {
            $0.score != $1.score ? $0.score > $1.score : $0.time < $1.time
        }
        for c in optional {
            if chosen.count >= optionalBudget { break }
            if chosen.contains(where: { abs($0.time - c.time) < config.minGap }) { continue }
            if isDuplicateOfNeighbor(c.time) { continue }
            chosen.append(c)
        }

        if let endFrame {
            let previous = chosen.filter { $0.time < endFrame.time }.max { $0.time < $1.time }
            let tooClose = previous.map { endFrame.time - $0.time < config.minGap } ?? false
            if previous == nil || (!tooClose && !isDuplicateOfNeighbor(endFrame.time)) {
                chosen.append(endFrame)
            }
        }

        return chosen.sorted { $0.time < $1.time }.map {
            KeyframeSelection(
                time: $0.time, reasons: $0.reasons.sorted(), annotationIDs: $0.annotationIDs.sorted(),
                score: $0.score, mandatory: $0.mandatory
            )
        }
    }

    /// Merges per-display selections into one chronological list under a shared budget:
    /// all mandatory frames are kept, the rest are filled by score.
    public static func merge(_ lists: [[KeyframeSelection]], maxFrames: Int) -> [(display: Int, selection: KeyframeSelection)] {
        let all = lists.enumerated().flatMap { i, list in list.map { (display: i, selection: $0) } }
        var kept = all.filter { $0.selection.mandatory }
        let optional = all.filter { !$0.selection.mandatory }.sorted {
            $0.selection.score != $1.selection.score ? $0.selection.score > $1.selection.score : $0.selection.time < $1.selection.time
        }
        for item in optional where kept.count < maxFrames {
            kept.append(item)
        }
        return kept.sorted { $0.selection.time != $1.selection.time ? $0.selection.time < $1.selection.time : $0.display < $1.display }
    }

    /// Converts a cursor-display timeline (time, display index), sorted by time, into the
    /// intervals each display was active, ending at `duration`.
    public static func activeIntervals(timeline: [(time: Double, display: Int)], displayCount: Int, duration: Double) -> [[ClosedRange<Double>]] {
        var result = Array(repeating: [ClosedRange<Double>](), count: displayCount)
        guard !timeline.isEmpty else { return result }
        var start = 0.0
        var current = timeline[0].display
        for entry in timeline.dropFirst() where entry.display != current {
            if current >= 0 && current < displayCount && entry.time > start { result[current].append(start...entry.time) }
            start = entry.time
            current = entry.display
        }
        if current >= 0 && current < displayCount && duration > start { result[current].append(start...duration) }
        return result
    }

    /// Mean absolute difference of two thumbnails, normalized to 0…1.
    public static func diff(_ a: [UInt8], _ b: [UInt8]) -> Double {
        let n = min(a.count, b.count)
        guard n > 0 else { return a.count == b.count ? 0 : 1 }
        var total = 0
        a.withUnsafeBufferPointer { pa in
            b.withUnsafeBufferPointer { pb in
                for i in 0..<n {
                    total += abs(Int(pa[i]) - Int(pb[i]))
                }
            }
        }
        return Double(total) / Double(n) / 255
    }

    /// Thumbnail of the frame on screen at time `t` (latest sample at or before `t`).
    static func thumb(at t: Double, in samples: [FrameSample]) -> [UInt8]? {
        guard !samples.isEmpty else { return nil }
        var lo = 0, hi = samples.count - 1, best = 0
        while lo <= hi {
            let mid = (lo + hi) / 2
            if samples[mid].time <= t {
                best = mid
                lo = mid + 1
            } else {
                hi = mid - 1
            }
        }
        return samples[best].thumb
    }
}
