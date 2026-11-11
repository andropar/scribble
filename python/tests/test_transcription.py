"""Transcription parts that need no Whisper model: languages, joining text in scripts with
and without spaces, cutting a recording into pieces, and transcribing while recording."""

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from scribble import audio, transcriber, video
from scribble.core import chunks, language as languages, markdown, transcript_text
from scribble.core.markdown import FrameRef, Marker
from scribble.core.geometry import Size
from scribble.core.models import Word
from scribble.live import LiveTranscriber, replay
from scribble.settings import AppSettings, WhisperCompute, WhisperModel
from scribble.transcriber import Status, TranscriptionOutput, convert, resolve_model

sys.path.insert(0, str(Path(__file__).parent))
from fakes import wait_until  # noqa: E402

RATE = video.SAMPLE_RATE


# MARK: languages


def test_missing_or_unknown_setting_is_english():
    assert languages.from_storage(None) == "en"
    assert languages.from_storage("tlh") == "en"
    assert languages.parse(" RU ") == "ru" and languages.parse("auto") == "auto"
    assert languages.parse("klingon") is None


def test_languages_are_sorted_by_name_and_unique():
    names = [name for _, name in languages.LANGUAGES]
    codes = [code for code, _ in languages.LANGUAGES]
    assert names == sorted(names) and len(set(codes)) == len(codes) == 100
    assert languages.name("de") == "German" and languages.name("xx") is None


def test_english_needs_no_warning_on_any_model():
    for model in WhisperModel:
        assert languages.resolve("en", model.is_multilingual) == languages.LanguagePlan("en", None)


def test_english_only_model_warns_for_anything_else():
    for selection in ("ru", "auto"):
        for model in (WhisperModel.SMALL_EN, WhisperModel.BASE_EN, WhisperModel.TINY_EN):
            plan = languages.resolve(selection, model.is_multilingual)
            assert plan.language == "en" and plan.warning
    # The model the settings window offers resolves it.
    assert languages.resolve("ru", WhisperModel.SMALL.is_multilingual) == languages.LanguagePlan("ru", None)


def test_auto_leaves_detection_to_whisper_and_cantonese_decodes_as_chinese():
    assert languages.resolve("auto", True) == languages.LanguagePlan(None, None)
    assert languages.resolve("yue", True) == languages.LanguagePlan("zh", None)


def test_auto_model_fits_the_language():
    assert resolve_model(WhisperModel.AUTO, "cpu", "en") == "small.en"
    assert resolve_model(WhisperModel.AUTO, "cpu", "de") == "small"
    assert resolve_model(WhisperModel.AUTO, "cpu", "auto") == "small"
    assert resolve_model(WhisperModel.AUTO, "cuda", "de") == "large-v3-turbo"
    assert resolve_model(WhisperModel.BASE_EN, "cpu", "de") == "base.en"
    assert set(transcriber.REPOSITORIES) == {m.value for m in WhisperModel if m != WhisperModel.AUTO}


def test_language_setting_is_stored_and_validated(tmp_path):
    settings = AppSettings(tmp_path / "settings.json")
    assert settings.language == "en"
    settings.set("language", "de")
    settings.set("model", WhisperModel.SMALL)
    again = AppSettings(tmp_path / "settings.json")
    assert again.language == "de" and again.model == WhisperModel.SMALL
    with pytest.raises(ValueError):
        again.set("language", "klingon")


# MARK: text in scripts with and without spaces


def word(text, start):
    return Word(text, start, start + 0.1)


def transcript(words, markers=()):
    frame = FrameRef(1, 0, "/f.png", Size(100, 100))
    return "\n".join(p.text for p in markdown.transcript_paragraphs([frame], words, list(markers)))


def test_spaced_words_keep_their_separators():
    assert transcript([word(" This", 0), word(" save", 1), word("button", 2)]) == "This save button"


def test_japanese_fragments_join_without_spaces():
    words = [word(t, i) for i, t in enumerate(["これは", "設", "定", "画", "面", "です。", "この"])]
    assert transcript(words) == "これは設定画面です。この"


def test_latin_term_inside_chinese_keeps_its_own_space():
    assert transcript([word("点击", 0), word(" Save", 1), word("按", 2), word("钮", 3)]) == "点击 Save按钮"


def test_mark_is_set_off_inside_unspaced_text():
    marker = Marker(1.5, "#1 pin at (1,2) in frame 1")
    assert transcript([word("設", 1), word("定", 2)], [marker]) == "設 [#1 pin at (1,2) in frame 1] 定"


def test_korean_stays_spaced():
    assert transcript([word(" 저장", 0), word("버튼", 1)]) == "저장 버튼"


def test_thai_combining_marks_merge_into_their_character():
    merged = transcript_text.merged_fragments(
        [word("น", 0), word("ี่", 0.1), word("ค", 0.2), word("ื", 0.3), word("อ", 0.4)]
    )
    assert [w.text for w in merged] == ["นี่", "คื", "อ"]
    assert merged[0].start == 0 and merged[0].end == pytest.approx(0.2)


def test_blank_fragment_becomes_leading_space():
    merged = transcript_text.merged_fragments([word("ค่า", 0), word(" ", 1), word("ภ", 2)])
    assert [w.text for w in merged] == ["ค่า", " ภ"]
    assert transcript(merged) == "ค่า ภ"


def test_spaced_words_are_left_alone():
    words = [word(" Вот", 0), word(" страница", 1), word(" настроек.", 2)]
    assert transcript_text.merged_fragments(words) == words


def test_joined_segments():
    assert transcript_text.joined(["One.", "Two."]) == "One. Two."
    assert transcript_text.joined(["一。", "二。"]) == "一。二。"
    assert transcript_text.joined([]) == ""


# MARK: converting Whisper's segments


def segment(text, start, end, words):
    return SimpleNamespace(
        text=text, start=start, end=end, words=[SimpleNamespace(word=w, start=s, end=e) for w, s, e in words]
    )


def test_convert_moves_a_piece_to_its_place_in_the_recording():
    out = convert(
        [segment(" Hello there.", 0.5, 1.4, [(" Hello", 0.5, 0.9), (" there.", 1.0, 1.4)])], 20.0, 10.0
    )
    assert [(w.text, w.start, w.end) for w in out.words] == [(" Hello", 20.5, 20.9), (" there.", 21.0, 21.4)]
    assert [(s.text, s.start, s.end) for s in out.segments] == [("Hello there.", 20.5, 21.4)]


def test_convert_drops_what_whisper_repeats_in_the_padding():
    said = segment(" One two.", 8.0, 9.5, [(" One", 8.0, 8.5), (" two.", 8.6, 9.5)])
    # A repeat entirely after the piece's 10 s, and one that starts just before its end.
    after = segment(" One two.", 10.5, 12.0, [(" One", 10.5, 11.0), (" two.", 11.1, 12.0)])
    across = segment(
        " One two three.", 9.9, 12.0, [(" One", 9.9, 10.2), (" two", 10.3, 11.0), (" three.", 11.1, 12.0)]
    )
    out = convert([said, after, across], 0.0, 10.0)
    assert [w.text for w in out.words] == [" One", " two."]
    assert [s.text for s in out.segments] == ["One two."]
    # Without a length (a file transcribed as a whole) nothing is dropped.
    assert len(convert([said, after, across]).words) == 7


def test_convert_skips_noise_and_keeps_spaces_of_fragmented_scripts():
    out = convert(
        [
            segment(
                "[MUSIC] ค่า ภ", 0, 3, [(" [MUSIC]", 0, 1), ("ค่า", 1, 1.5), (" ", 1.5, 1.6), ("ภ", 1.6, 2)]
            )
        ]
    )
    assert [w.text for w in out.words] == ["ค่า", " ภ"]


def test_merged_joins_pieces_in_time_order():
    first = TranscriptionOutput([word(" a", 1)], [], ["en"], None)
    second = TranscriptionOutput([word(" b", 40)], [], ["de"], "careful")
    out = transcriber.merged([second, first])
    assert [w.text for w in out.words] == [" a", " b"]
    assert out.languages == ["de", "en"] and out.warning == "careful"


# MARK: cutting a recording into pieces


def speech_like(seconds, pauses, seed=0):
    """Noise with silent stretches at ``pauses`` (start, length in seconds)."""
    rng = np.random.default_rng(seed)
    samples = (0.1 * rng.standard_normal(int(seconds * RATE))).astype(np.float32)
    for start, length in pauses:
        samples[int(start * RATE) : int((start + length) * RATE)] *= 0.001
    return samples


def test_short_recording_is_one_piece():
    samples = speech_like(12, [])
    assert chunks.first_cut(samples, RATE) is None
    assert chunks.pieces(samples, RATE) == [(0, len(samples))]
    exactly = speech_like(30, [])
    assert chunks.first_cut(exactly, RATE) is None and chunks.pieces(exactly, RATE) == [(0, len(exactly))]
    assert chunks.pieces(np.zeros(0, dtype=np.float32), RATE) == []


def test_cut_lands_in_a_pause_in_the_second_half():
    # The pause at 8 s is in the first half of the window and must not be taken.
    samples = speech_like(40, [(8.0, 0.6), (21.0, 0.6)])
    cut = chunks.first_cut(samples, RATE)
    assert 21.0 * RATE <= cut <= 21.6 * RATE
    assert chunks.pieces(samples, RATE) == [(0, cut), (cut, len(samples))]


def test_last_of_equally_quiet_pauses_is_taken():
    samples = speech_like(40, [(18.0, 0.5), (26.0, 0.5)])
    samples[int(18.0 * RATE) : int(18.5 * RATE)] = 0
    samples[int(26.0 * RATE) : int(26.5 * RATE)] = 0
    assert 26.0 * RATE <= chunks.first_cut(samples, RATE) <= 26.5 * RATE


def test_pieces_cover_the_recording_and_fit_the_window():
    samples = speech_like(200, [(t, 0.4) for t in range(5, 200, 7)], seed=1)
    pieces = chunks.pieces(samples, RATE)
    assert pieces[0][0] == 0 and pieces[-1][1] == len(samples)
    assert all(a[1] == b[0] for a, b in zip(pieces, pieces[1:]))
    assert all(15 * RATE <= end - start <= 30 * RATE for start, end in pieces[:-1])
    assert pieces[-1][1] - pieces[-1][0] <= 30 * RATE
    # Every cut is in a pause.
    for _, end in pieces[:-1]:
        assert float(np.abs(samples[end - 800 : end + 800]).max()) < 0.01


def test_a_tail_too_short_for_a_word_is_ignored():
    samples = speech_like(30.2, [(29.6, 0.3)])
    pieces = chunks.pieces(samples, RATE)
    assert len(pieces) == 1 and len(samples) - pieces[0][1] <= chunks.MIN_TAIL_SECONDS * RATE


def test_cuts_made_while_recording_are_those_of_the_finished_recording():
    """What makes transcribing while recording exact: a cut depends only on the 30 s after
    the piece's start, so it is the same however much has been recorded since."""
    samples = speech_like(150, [(t, 0.35) for t in range(4, 150, 5)], seed=2)
    window = int(chunks.WINDOW_SECONDS * RATE)
    cuts, committed = [], 0
    for recorded in range(RATE, len(samples) + 1, RATE // 2):  # the recording grows
        cut = chunks.first_cut(samples[committed:recorded][: window + 1], RATE)
        if cut is not None:
            committed += cut
            cuts.append(committed)
    whole = chunks.pieces(samples, RATE)
    assert cuts == [end for _, end in whole[:-1]]
    assert chunks.pieces(samples, RATE, start=committed) == whole[-1:]


# MARK: reading the microphone while it records


class FakeStream:
    def __init__(self, callback, rate, channels=1):
        self.callback, self.samplerate, self.channels = callback, rate, channels
        self.position = 0

    def feed(self, seconds):
        n = int(seconds * self.samplerate)
        t = (self.position + np.arange(n)) / self.samplerate
        self.position += n
        block = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        self.callback(np.repeat(block[:, None], self.channels, axis=1), n, None, None)

    def stop(self):
        pass

    def close(self):
        pass


@pytest.mark.parametrize("rate", [16_000, 44_100, 48_000])
def test_samples_read_while_recording_are_the_start_of_the_result(rate):
    holder = {}

    def opener(callback):
        holder["stream"] = FakeStream(callback, rate, channels=2)
        return holder["stream"]

    recorder = audio.AudioRecorder(open_stream=opener)
    recorder.start()
    seen = []
    for _ in range(8):
        holder["stream"].feed(0.1)
        seen.append(np.array(recorder.samples(0, 10 * RATE)))
    assert len(recorder.samples(4000, 100)) == 100
    result = recorder.stop()
    assert abs(len(result.samples) - int(0.8 * RATE)) < 400
    assert [len(s) for s in seen] == sorted(len(s) for s in seen) and len(seen[-1]) > 0.7 * RATE
    for part in seen:
        assert np.array_equal(part, result.samples[: len(part)])
    # And the result is what converting the whole recording at once gives.
    t = np.arange(int(0.8 * rate)) / rate
    expected = video.resample((0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), rate, RATE)
    assert np.allclose(result.samples, expected, atol=1e-6)
    # A new recording starts empty.
    recorder.start()
    assert len(recorder.samples(0, RATE)) == 0
    recorder.stop()


# MARK: transcribing while recording


class GrowingRecording:
    """A recording that is as long as the test says it is."""

    def __init__(self, samples):
        self.all, self.recorded = samples, 0

    def samples(self, start, count):
        return self.all[start : min(start + count, self.recorded)]


class PieceTranscriber:
    """Says which piece it was given instead of running Whisper."""

    def __init__(self, fail_at=None):
        self.status = Status("ready", device="cpu", model="tiny.en")
        self.decoded, self.whole, self.fail_at = [], 0, fail_at
        self.lock = threading.Lock()

    def decode(self, piece, offset, model, compute, vocabulary="", language="en"):
        with self.lock:
            if self.fail_at is not None and len(self.decoded) == self.fail_at:
                raise RuntimeError("model exploded")
            self.decoded.append((offset, offset + len(piece)))
        at = offset / RATE
        return TranscriptionOutput([Word(" piece{}".format(offset), at, at + 1)], [], [language], None)

    def transcribe(self, samples, model, compute, vocabulary="", language="en"):
        self.whole += 1
        return transcriber.merged(
            [self.decode(samples[a:b], a, model, compute) for a, b in chunks.pieces(samples, RATE)]
        )


def live_for(recording, whisper):
    return LiveTranscriber(recording, whisper, WhisperModel.TINY_EN, WhisperCompute.CPU, "", "en")


@pytest.fixture
def fast_polling(monkeypatch):
    monkeypatch.setattr("scribble.live.POLL_SECONDS", 0.01)


def test_pieces_are_decoded_while_recording(fast_polling):
    samples = speech_like(100, [(t, 0.4) for t in range(5, 100, 6)], seed=3)
    expected = chunks.pieces(samples, RATE)
    recording, whisper = GrowingRecording(samples), PieceTranscriber()
    live = live_for(recording, whisper)
    recording.recorded = 20 * RATE
    time.sleep(0.1)
    assert whisper.decoded == []  # nothing can be cut in the first 30 s
    recording.recorded = len(samples)
    assert wait_until(lambda: len(whisper.decoded) == len(expected) - 1, 5)
    time.sleep(0.05)
    assert whisper.decoded == expected[:-1]  # the last piece may still grow
    out = live.finish(samples)
    assert whisper.decoded == expected and whisper.whole == 0
    assert [w.text for w in out.words] == [" piece{}".format(start) for start, _ in expected]
    assert not live._thread.is_alive()


def test_finishing_decodes_whatever_recording_did_not_get_to(fast_polling):
    samples = speech_like(100, [(t, 0.4) for t in range(5, 100, 6)], seed=4)
    expected = chunks.pieces(samples, RATE)
    recording, whisper = GrowingRecording(samples), PieceTranscriber()
    live = live_for(recording, whisper)
    recording.recorded = 45 * RATE
    assert wait_until(lambda: len(whisper.decoded) == 1, 5)
    out = live.finish(samples)
    assert whisper.decoded == expected and len(out.words) == len(expected)


def test_short_recording_is_left_to_the_caller(fast_polling):
    samples = speech_like(20, [])
    recording, whisper = GrowingRecording(samples), PieceTranscriber()
    live = live_for(recording, whisper)
    recording.recorded = len(samples)
    time.sleep(0.05)
    assert live.finish(samples) is None and whisper.decoded == []


def test_nothing_is_decoded_before_the_model_is_ready(fast_polling):
    samples = speech_like(50, [(20, 0.5)])
    recording, whisper = GrowingRecording(samples), PieceTranscriber()
    whisper.status = Status("loading")
    live = live_for(recording, whisper)
    recording.recorded = len(samples)
    time.sleep(0.05)
    assert whisper.decoded == []
    assert live.finish(samples) is None


def test_a_failed_piece_leaves_the_recording_to_the_caller(fast_polling):
    samples = speech_like(100, [(t, 0.4) for t in range(5, 100, 6)], seed=5)
    recording, whisper = GrowingRecording(samples), PieceTranscriber(fail_at=1)
    live = live_for(recording, whisper)
    recording.recorded = len(samples)
    assert wait_until(lambda: not live._thread.is_alive(), 5)
    assert live.finish(samples) is None


def test_silence_gives_an_empty_transcript(fast_polling):
    samples = np.zeros(40 * RATE, dtype=np.float32)
    recording, whisper = GrowingRecording(samples), PieceTranscriber()
    live = live_for(recording, whisper)
    recording.recorded = len(samples)
    assert wait_until(lambda: len(whisper.decoded) == 1, 5)
    out = live.finish(samples)
    assert out is not None and out.words == []


def test_cancel_stops_the_worker(fast_polling):
    samples = speech_like(40, [(20, 0.5)])
    live = live_for(GrowingRecording(samples), PieceTranscriber())
    live.cancel()
    assert wait_until(lambda: not live._thread.is_alive(), 5)


def test_replay_gives_the_pieces_of_one_go():
    samples = speech_like(100, [(t, 0.4) for t in range(5, 100, 6)], seed=6)
    whisper = PieceTranscriber()
    args = (WhisperModel.TINY_EN, WhisperCompute.CPU, "", "en")
    out, cuts, _ = replay(samples, whisper, *args)
    assert [w.text for w in out.words] == [w.text for w in whisper.transcribe(samples, *args).words]
    assert cuts == [end for _, end in chunks.pieces(samples, RATE)[:-1]]
    short = speech_like(10, [])
    out, cuts, _ = replay(short, whisper, *args)
    assert cuts == [] and len(out.words) == 1
