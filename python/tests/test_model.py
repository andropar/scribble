"""Real transcription with real Whisper models (tiny.en, ~75 MB download, and the
multilingual base, ~145 MB).

Opt in with ``SCRIBBLE_TEST_MODEL=1`` and point ``SCRIBBLE_SPEECH`` at a
recording of: "This button is broken. Please move the legend below the chart."
``SCRIBBLE_SPEECH_DE`` (optional) is a recording of German speech.
``SCRIBBLE_TEST_HOME`` (optional) is a home folder whose ``models`` are reused.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pytest

from scribble import processor, transcriber, video
from scribble.audio import AudioResult
from scribble.core import chunks, language as languages
from scribble.core.geometry import Size
from scribble.core.models import RED, Annotation, AnnotationTool, CaptureMode
from scribble.live import LiveTranscriber, replay
from scribble.processor import CaptureInput, DisplayInfo, ShotData
from scribble.settings import WhisperCompute, WhisperModel
from scribble.transcriber import Transcriber

sys.path.insert(0, str(Path(__file__).parent))
from helpers import Playback, gradient_image, repeated  # noqa: E402

pytestmark = pytest.mark.model

KEYWORDS = ("button", "broken", "move", "legend", "chart")


def home() -> Path:
    path = Path(os.environ.get("SCRIBBLE_TEST_HOME") or tempfile.mkdtemp(prefix="scribble-model-"))
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="module")
def speech():
    path = os.environ.get("SCRIBBLE_SPEECH")
    if not path or not os.path.isfile(path):
        pytest.skip("SCRIBBLE_SPEECH is not set")
    return video.load_samples(path)


@pytest.fixture(scope="module")
def whisper():
    return Transcriber(models_dir=home() / "models")


def spoken(words) -> list:
    return [re.sub(r"[^a-z]", "", w.text.lower()) for w in words]


def test_transcribes_speech_with_word_timings(speech, whisper):
    statuses = []
    whisper.subscribe(lambda s: statuses.append(s.kind))
    out = whisper.transcribe(speech, WhisperModel.TINY_EN, WhisperCompute.AUTO, "scribble")
    assert whisper.status.is_ready and whisper.status.model == "tiny.en"
    assert "loading" in statuses and statuses[-1] == "ready"
    found = [k for k in KEYWORDS if k in spoken(out.words)]
    assert len(found) >= 3, "heard: {}".format(out.plain_text)
    # Word timings are ordered, inside the recording, and words keep their leading space.
    duration = len(speech) / video.SAMPLE_RATE
    starts = [w.start for w in out.words]
    assert starts == sorted(starts) and all(0 <= w.start <= w.end <= duration + 0.5 for w in out.words)
    assert all(w.text.startswith(" ") for w in out.words)
    assert out.segments and out.plain_text


def test_marks_land_on_the_spoken_word(qapp, speech, whisper, tmp_path):
    """The point of the whole app: a mark drawn while a word is spoken is cited there."""
    heard = whisper.transcribe(speech, WhisperModel.TINY_EN, WhisperCompute.AUTO)
    anchor = next((w for w in heard.words if "legend" in w.text.lower() or "chart" in w.text.lower()), None)
    assert anchor is not None, heard.plain_text
    canvas = Size(1280, 800)
    t0, audio_start = 500.0, 0.25
    mark = Annotation(
        tool=AnnotationTool.ELLIPSE,
        points=[(300, 200), (500, 300)],
        color=RED,
        # Drawn just before the anchor word starts.
        start_time=audio_start + anchor.start - 0.05,
        id=1,
        number=1,
    )
    mark.finalize(mark.start_time + 0.4)
    inp = CaptureInput(
        mode=CaptureMode.SNAPSHOT,
        folder=tmp_path,
        start_date=datetime.now().astimezone(),
        t0=t0,
        duration=len(speech) / video.SAMPLE_RATE + audio_start,
        displays=[DisplayInfo(0, "Demo", canvas)],
        shots=[ShotData(gradient_image(canvas, 1.0), 0.1, canvas, 0, 0, True)],
        annotations=[mark],
        audio=AudioResult(speech, t0 + audio_start),
        model=WhisperModel.TINY_EN,
    )
    capture = processor.process(inp, whisper)
    md = Path(capture.markdown_path).read_text(encoding="utf-8")
    reference = "[#1 circled (300,200)-(500,300) in frame 1]"
    assert reference in md and capture.note is None
    before, after = md.split(reference)
    word = re.sub(r"[^a-z]", "", anchor.text.lower())
    assert re.sub(r"[^a-z]", "", after.split()[0].lower()) == word, md
    assert capture.word_count == len(heard.words)
    events = json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
    # Word times were moved onto the session clock (the audio started 0.25 s in).
    assert events["words"][0]["start"] == pytest.approx(heard.words[0].start + audio_start, abs=1e-6)
    assert (tmp_path / "audio.m4a").stat().st_size > 2000


@pytest.fixture
def repeatable(monkeypatch):
    """Decoding without random retries, so two runs over the same audio can be compared."""
    monkeypatch.setitem(transcriber.DECODING_OVERRIDES, "temperature", 0.0)


def timed(out):
    return [(w.text, round(w.start, 3), round(w.end, 3)) for w in out.words]


def test_long_recording_is_decoded_in_pieces(speech, whisper, repeatable):
    recording = repeated(speech, 75)
    pieces = chunks.pieces(recording, video.SAMPLE_RATE)
    assert len(pieces) >= 3
    out = whisper.transcribe(recording, WhisperModel.TINY_EN, WhisperCompute.AUTO)
    heard = spoken(out.words)
    times = round(len(recording) / (len(speech) + 0.9 * video.SAMPLE_RATE))
    # Every repetition is heard, in every piece, and nothing is said twice at a cut.
    for keyword in ("legend", "chart"):
        assert times - 1 <= heard.count(keyword) <= times, out.plain_text
    starts = [w.start for w in out.words]
    assert starts == sorted(starts) and starts[-1] > len(recording) / video.SAMPLE_RATE - len(speech) / 8_000
    assert out.languages == ["en"] and out.warning is None


def test_transcribing_while_recording_gives_the_same_transcript(speech, whisper, repeatable):
    """The promise of ``live``: the same words at the same times as transcribing the
    finished recording, with only the end left to do after finishing."""
    recording = repeated(speech, 75)
    args = (WhisperModel.TINY_EN, WhisperCompute.AUTO, "", "en")
    whole = whisper.transcribe(recording, *args)
    assert len(whole.words) > 50

    # The audio arrives over time (20x faster than spoken), as from a microphone.
    live = LiveTranscriber(Playback(recording, 20), whisper, *args)
    time.sleep(len(recording) / video.SAMPLE_RATE / 20 + 1.5)
    during = list(live._outputs)
    assert during, "nothing was decoded while recording"
    out = live.finish(recording)
    assert out is not None and timed(out) == timed(whole)
    assert [s.text for s in out.segments] == [s.text for s in whole.segments]

    # Cut and decoded piece by piece without the clock: the same again.
    again, cuts, _ = replay(recording, whisper, *args)
    assert timed(again) == timed(whole)
    assert cuts == [end for _, end in chunks.pieces(recording, video.SAMPLE_RATE)[:-1]]


def test_multilingual_model_detects_the_language(speech, whisper):
    out = whisper.transcribe(speech, WhisperModel.BASE, WhisperCompute.AUTO, "", languages.AUTO)
    assert whisper.status.model == "base"
    assert out.languages == ["en"] and out.warning is None
    assert len([k for k in KEYWORDS if k in spoken(out.words)]) >= 3, out.plain_text


def test_german_is_transcribed_as_german(whisper):
    path = os.environ.get("SCRIBBLE_SPEECH_DE")
    if not path or not os.path.isfile(path):
        pytest.skip("SCRIBBLE_SPEECH_DE is not set")
    german = video.load_samples(path)
    for selection in (languages.AUTO, "de"):
        out = whisper.transcribe(german, WhisperModel.BASE, WhisperCompute.AUTO, "", selection)
        assert out.languages == ["de"] and out.words, (selection, out.languages, out.plain_text)


def test_english_only_model_warns_about_another_language(speech, whisper):
    out = whisper.transcribe(speech, WhisperModel.TINY_EN, WhisperCompute.AUTO, "", "de")
    assert out.warning == languages.ENGLISH_ONLY_WARNING and out.languages == ["en"]
    assert len([k for k in KEYWORDS if k in spoken(out.words)]) >= 3, out.plain_text


def test_command_line_compares_live_and_one_go(speech, tmp_path):
    """``scribble --eval-live``: the check a developer runs on their own recordings."""
    base = home()
    recording = tmp_path / "long.m4a"
    video.write_m4a(repeated(speech, 45), recording)
    env = dict(os.environ, SCRIBBLE_HOME=str(base), QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "scribble", "--model", "tiny.en", "--eval-live", "--deterministic"]
        + ["--realtime", str(recording)],
        capture_output=True,
        encoding="utf-8",
        timeout=600,
        env=env,
    )
    assert result.returncode == 0, result.stdout[-1500:] + result.stderr[-2000:]
    assert "total: 1 recordings" in result.stdout and ", 0 differ," in result.stdout, result.stdout


def test_command_line_pipeline(speech, tmp_path):
    """``scribble --demo-snapshot``: model download/load, pipeline and files, as a user runs it."""
    base = home()
    (base / "settings.json").write_text(json.dumps({"model": "tiny.en"}), encoding="utf-8")
    env = dict(os.environ, SCRIBBLE_HOME=str(base), QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "scribble", "--demo-snapshot", os.environ["SCRIBBLE_SPEECH"]],
        capture_output=True,
        encoding="utf-8",
        timeout=600,
        env=env,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    out = result.stdout
    assert "[model] Transcription model ready (tiny.en on CPU)" in out
    assert "# Screen capture · Snapshot" in out and "## Transcript" in out
    assert "#1 arrow from (" in out and "drawing over (" in out and "#5 circled (" in out
    # The demo canvas is a 6K display: frames get a detail crop.
    assert "  - detail of frame 1, region (" in out
    assert len([k for k in KEYWORDS if k in out.lower()]) >= 3, out
    folder = Path(re.search(r"^Folder: (.+)$", out, re.M).group(1).strip())
    for name in ("capture.md", "events.json", "frame_01.png", "frame_01_detail.png", "audio.m4a"):
        assert (folder / name).is_file(), name
