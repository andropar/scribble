"""Picks the keyframes that summarize a walkthrough recording.

Mirrors ``Sources/ScribbleCore/KeyframeSelector.swift`` (see SPEC.md §4).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Set, Tuple

import numpy as np

from .models import Annotation, FrameSample

Interval = Tuple[float, float]


@dataclass
class KeyframeSelection:
    #: Session time in seconds.
    time: float
    reasons: List[str]
    #: Annotations this frame was chosen to show.
    annotation_ids: List[int]
    score: float = 1.0
    #: Always kept (annotations, start, end) even when merging across displays.
    mandatory: bool = False


@dataclass
class KeyframeConfig:
    max_frames: int = 30
    min_frames: int = 6
    #: Frame budget grows by one frame per this many seconds of recording.
    seconds_per_frame: float = 5.0
    min_gap: float = 1.2
    #: Per-frame mean abs luminance diff (0…1) that counts as "screen is changing".
    change_threshold: float = 0.004
    #: Net before/after diff of a burst that counts as a meaningful new view.
    significant_change: float = 0.012
    burst_gap: float = 0.6
    settle_delay: float = 0.3
    long_burst_interval: float = 4.0
    annotation_group_gap: float = 1.5
    #: Delay after a stroke ends until it is guaranteed to be in a captured frame.
    annotation_render_delay: float = 0.25
    #: How long annotations stay fully visible on screen (Walkthrough fade).
    fade_duration: float = 3.0
    periodic_interval: float = 15.0
    #: Optional frames closer than this (thumbnail diff) to a neighboring chosen frame are
    #: dropped. Kept above ``change_threshold`` so frames that never "changed" count as equal.
    duplicate_threshold: float = 0.005


@dataclass
class _Candidate:
    time: float
    score: float
    mandatory: bool
    reasons: Set[str]
    annotation_ids: List[int] = field(default_factory=list)


def diff(a: np.ndarray, b: np.ndarray) -> float:
    """Mean absolute difference of two thumbnails, normalized to 0…1."""
    n = min(len(a), len(b))
    if n == 0:
        return 0.0 if len(a) == len(b) else 1.0
    total = np.abs(np.asarray(a[:n], dtype=np.int16) - np.asarray(b[:n], dtype=np.int16)).sum()
    return float(total) / n / 255


def _thumb_at(t: float, samples: Sequence[FrameSample]) -> Optional[np.ndarray]:
    """Thumbnail of the frame on screen at time ``t`` (latest sample at or before ``t``)."""
    if not samples:
        return None
    lo, hi, best = 0, len(samples) - 1, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if samples[mid].time <= t:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return samples[best].thumb


def frame_budget(duration: float, config: KeyframeConfig) -> int:
    return min(
        config.max_frames, max(config.min_frames, int(math.ceil(max(duration, 0) / config.seconds_per_frame)))
    )


def select(
    samples: Sequence[FrameSample],
    annotations: Sequence[Annotation],
    clicks: Sequence[float],
    speech_starts: Sequence[float],
    duration: float,
    active_intervals: Optional[Sequence[Interval]] = None,
    include_start: bool = True,
    include_end: bool = True,
    config: Optional[KeyframeConfig] = None,
) -> List[KeyframeSelection]:
    """Selects keyframes for one display.

    ``active_intervals``: when recording several displays, the time ranges the cursor was
    on this display. Optional (non-annotation) frames outside them are ignored, and each
    interval start adds a "display switch" candidate. None = always active.
    ``include_start``/``include_end``: whether this display gets the mandatory first/last frame.
    """
    config = config or KeyframeConfig()
    end = max(duration, 0.0)
    latest = max(end - 0.05, 0.0)

    def clamp(t: float) -> float:
        return min(max(t, 0.0), latest)

    samples = sorted(samples, key=lambda s: s.time)
    cands: List[_Candidate] = []
    settle_times: List[float] = []

    # 1. Activity bursts → settle frames.
    if len(samples) >= 2:
        i = 1
        while i < len(samples):
            if not diff(samples[i - 1].thumb, samples[i].thumb) > config.change_threshold:
                i += 1
                continue
            a = i
            b = i
            j = i + 1
            while j < len(samples) and samples[j].time - samples[b].time <= config.burst_gap:
                if diff(samples[j - 1].thumb, samples[j].thumb) > config.change_threshold:
                    b = j
                j += 1
            net = diff(samples[a - 1].thumb, samples[b].thumb)
            if net > config.significant_change:
                settle = clamp(samples[b].time + config.settle_delay)
                cands.append(_Candidate(settle, 1 + min(net * 25, 3), False, {"screen change"}))
                settle_times.append(settle)
            span = samples[b].time - samples[a].time
            if span > config.long_burst_interval:
                t = samples[a].time + config.long_burst_interval
                while t < samples[b].time - 1:
                    cands.append(_Candidate(clamp(t), 0.8, False, {"ongoing change"}))
                    t += config.long_burst_interval
            i = b + 1

    # 2. Annotation bursts → mandatory frames showing every annotation.
    groups: List[List[Annotation]] = []
    for ann in sorted(annotations, key=lambda x: x.end_time):
        if groups:
            group = groups[-1]
            first, last = group[0], group[-1]
            close_in_time = ann.start_time - last.end_time <= config.annotation_group_gap
            first_still_visible = (
                ann.end_time + config.annotation_render_delay < first.end_time + config.fade_duration
            )
            screen_stable = not any(last.end_time < s < ann.start_time for s in settle_times)
            if close_in_time and first_still_visible and screen_stable:
                group.append(ann)
                continue
        groups.append([ann])
    for group in groups:
        last = group[-1]
        cands.append(
            _Candidate(
                clamp(last.end_time + config.annotation_render_delay),
                100,
                True,
                {"annotation"},
                [g.id for g in group],
            )
        )

    # 3. Clicks boost the following settle frame, or add their own.
    for c in clicks:
        boosted = False
        for cand in cands:
            if not cand.mandatory and "screen change" in cand.reasons and c < cand.time <= c + 1.5:
                cand.score += 1
                cand.reasons.add("click")
                boosted = True
                break
        if not boosted:
            cands.append(_Candidate(clamp(c + 0.5), 1.2, False, {"click"}))

    # 4. Speech segment starts (low weight).
    for s in speech_starts:
        cands.append(_Candidate(clamp(s + 0.2), 0.6, False, {"speech"}))

    # 5. Display switches (kept when the user stayed ≥ 2 s, so the transcript after the
    # switch is attached to this display), first and last frame.
    for lo, hi in active_intervals or []:
        if not lo > 0.5:
            continue
        stayed = hi - lo >= 2
        cands.append(_Candidate(clamp(lo + 0.4), 40 if stayed else 1.5, stayed, {"display switch"}))
    if end > 0 and include_start:
        cands.append(_Candidate(clamp(min(0.3, end)), 50, True, {"start"}))
    if end > 0 and include_end:
        if end > 1 or not include_start:
            cands.append(_Candidate(latest, 50, True, {"end"}))

    # Focus follows the cursor: drop optional frames from times this display wasn't used.
    if active_intervals is not None:
        grace = 1.0
        cands = [
            c
            for c in cands
            if c.mandatory or any(lo - grace <= c.time <= hi + grace for lo, hi in active_intervals)
        ]

    # 6. Periodic fallback for long quiet stretches.
    times = sorted(c.time for c in cands)
    prev = 0.0
    for t in times + [end]:
        x = prev + config.periodic_interval
        while x < t - config.periodic_interval / 2:
            if active_intervals is not None and not any(lo <= x <= hi for lo, hi in active_intervals):
                x += config.periodic_interval
                continue
            cands.append(_Candidate(clamp(x), 0.4, False, {"periodic"}))
            x += config.periodic_interval
        prev = max(prev, t)

    # Selection.
    budget = frame_budget(end, config)
    mandatory: List[_Candidate] = []
    for c in sorted((c for c in cands if c.mandatory), key=lambda c: c.time):
        if mandatory and abs(mandatory[-1].time - c.time) < 0.35:
            merged = mandatory[-1]
            merged.time = max(merged.time, c.time)
            merged.reasons |= c.reasons
            merged.annotation_ids = merged.annotation_ids + c.annotation_ids
        else:
            mandatory.append(_Candidate(c.time, c.score, True, set(c.reasons), list(c.annotation_ids)))
    # A plain end frame is only kept if it shows something new (decided last), so it
    # doesn't suppress the earlier frame where the final view first appeared.
    end_frame: Optional[_Candidate] = None
    if mandatory and mandatory[-1].reasons == {"end"}:
        end_frame = mandatory.pop()
    chosen = list(mandatory)

    def is_duplicate_of_neighbor(t: float) -> bool:
        th = _thumb_at(t, samples)
        if th is None:
            return False
        before = [c for c in chosen if c.time <= t]
        after = [c for c in chosen if c.time > t]
        neighbors = []
        if before:
            neighbors.append(max(before, key=lambda c: c.time))
        if after:
            neighbors.append(min(after, key=lambda c: c.time))
        for n in neighbors:
            nt = _thumb_at(n.time, samples)
            if nt is not None and diff(th, nt) < config.duplicate_threshold:
                return True
        return False

    optional_budget = budget - (0 if end_frame is None else 1)
    optional = sorted((c for c in cands if not c.mandatory), key=lambda c: (-c.score, c.time))
    for c in optional:
        if len(chosen) >= optional_budget:
            break
        if any(abs(o.time - c.time) < config.min_gap for o in chosen):
            continue
        if is_duplicate_of_neighbor(c.time):
            continue
        chosen.append(c)

    if end_frame is not None:
        earlier = [c for c in chosen if c.time < end_frame.time]
        previous = max(earlier, key=lambda c: c.time) if earlier else None
        too_close = previous is not None and end_frame.time - previous.time < config.min_gap
        if previous is None or (not too_close and not is_duplicate_of_neighbor(end_frame.time)):
            chosen.append(end_frame)

    return [
        KeyframeSelection(c.time, sorted(c.reasons), sorted(c.annotation_ids), c.score, c.mandatory)
        for c in sorted(chosen, key=lambda c: c.time)
    ]


def merge(
    lists: Sequence[Sequence[KeyframeSelection]], max_frames: int
) -> List[Tuple[int, KeyframeSelection]]:
    """Merges per-display selections into one chronological list under a shared budget:
    all mandatory frames are kept, the rest are filled by score. Returns ``(display, selection)``."""
    everything = [(i, sel) for i, lst in enumerate(lists) for sel in lst]
    kept = [item for item in everything if item[1].mandatory]
    optional = sorted(
        (item for item in everything if not item[1].mandatory),
        key=lambda item: (-item[1].score, item[1].time),
    )
    for item in optional:
        if len(kept) >= max_frames:
            break
        kept.append(item)
    return sorted(kept, key=lambda item: (item[1].time, item[0]))


def active_intervals(
    timeline: Sequence[Tuple[float, int]], display_count: int, duration: float
) -> List[List[Interval]]:
    """Converts a cursor-display timeline ``(time, display index)``, sorted by time, into the
    intervals each display was active, ending at ``duration``."""
    result: List[List[Interval]] = [[] for _ in range(display_count)]
    if not timeline:
        return result
    start = 0.0
    current = timeline[0][1]
    for time, display in timeline[1:]:
        if display == current:
            continue
        if 0 <= current < display_count and time > start:
            result[current].append((start, time))
        start = time
        current = display
    if 0 <= current < display_count and duration > start:
        result[current].append((start, duration))
    return result
