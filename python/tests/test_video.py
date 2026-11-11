import numpy as np
import pytest

from scribble import video


def solid(w, h, bgr):
    frame = np.zeros((h, w, 4), dtype=np.uint8)
    frame[..., 0], frame[..., 1], frame[..., 2], frame[..., 3] = bgr[0], bgr[1], bgr[2], 255
    return frame


def test_thumbnail_shape_and_luma():
    t = video.thumbnail(solid(640, 400, (0, 0, 0)))
    assert t.shape == (64 * 40,) and t.dtype == np.uint8
    assert t.max() == 0
    white = video.thumbnail(solid(641, 399, (255, 255, 255)))
    assert white.min() == 255
    # Left half dark, right half bright: the thumbnail keeps the layout.
    frame = solid(640, 400, (0, 0, 0))
    frame[:, 320:, :3] = 255
    cells = video.thumbnail(frame).reshape(40, 64)
    assert cells[:, :31].max() == 0 and cells[:, 33:].min() == 255


def test_thumbnail_matches_reference_loop():
    # The straightforward loop from the macOS Thumbnailer, as the reference.
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 256, size=(123, 217, 4), dtype=np.uint8)
    h, w = frame.shape[:2]
    out = np.zeros(64 * 40, dtype=np.uint8)
    for r in range(40):
        for c in range(64):
            acc = 0
            for sy in range(3):
                y = min(h - 1, ((r * 3 + sy) * h + h // (2 * 40 * 3)) // (40 * 3))
                for sx in range(3):
                    x = min(w - 1, ((c * 3 + sx) * w + w // (2 * 64 * 3)) // (64 * 3))
                    b, g, red = (int(v) for v in frame[y, x, :3])
                    acc += (red * 77 + g * 150 + b * 29) >> 8
            out[r * 64 + c] = acc // 9
    assert np.array_equal(video.thumbnail(frame), out)


def test_video_round_trip_returns_frame_on_screen_at_time(tmp_path):
    path = tmp_path / "screen.mp4"
    # Irregular timestamps (variable frame rate) and an odd size.
    shots = [(0.0, 20), (0.13, 60), (0.9, 100), (2.5, 140), (2.61, 180), (6.0, 220)]
    writer = video.VideoWriter(path, 321, 201, fps=10)
    assert (writer.width, writer.height) == (320, 200)
    for t, level in shots:
        writer.write(solid(321, 201, (level, level, level)), t)
    writer.close()

    def level_at(t):
        return [lv for ts, lv in shots if ts <= t + 1e-6][-1]

    # Out of order on purpose: results must still land on the right index.
    times = [5.9, 0.0, 0.5, 1.0, 2.55, 2.7, 0.14, 6.2, 3.0]
    seen = {}
    video.for_each_frame(path, times, lambda i, frame: seen.__setitem__(i, frame))
    assert sorted(seen) == list(range(len(times)))
    for i, t in enumerate(times):
        frame = seen[i]
        assert frame.shape == (200, 320, 4)
        assert abs(float(frame[..., :3].mean()) - level_at(t)) < 6, "wrong frame at t={}".format(t)


def test_video_writer_downscales_wide_displays(tmp_path):
    writer = video.VideoWriter(tmp_path / "wide.mp4", 800, 400, max_width=400)
    assert (writer.width, writer.height) == (400, 200)
    writer.write(solid(800, 400, (10, 200, 30)), 0)
    writer.write(solid(800, 400, (10, 200, 30)), 0.5)
    writer.close()
    frames = []
    video.for_each_frame(tmp_path / "wide.mp4", [0.2], lambda i, f: frames.append(f))
    assert frames[0].shape == (200, 400, 4)
    assert abs(int(frames[0][100, 200, 1]) - 200) < 12


def tone(seconds, freq=440.0, rate=video.SAMPLE_RATE):
    t = np.arange(int(seconds * rate)) / rate
    return (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_m4a_round_trip(tmp_path):
    samples = tone(2.0)
    path = tmp_path / "audio.m4a"
    video.write_m4a(samples, path)
    loaded = video.load_samples(path)
    assert abs(len(loaded) - len(samples)) < 3000  # AAC priming/padding
    assert video.rms(loaded) == pytest.approx(video.rms(samples), rel=0.15)


def test_resample_keeps_duration_and_pitch():
    src = tone(1.0, freq=440, rate=48_000)
    out = video.resample(src, 48_000)
    assert abs(len(out) - 16_000) < 200
    spectrum = np.abs(np.fft.rfft(out[: 16_000 // 2 * 2]))
    peak_hz = np.argmax(spectrum) * 16_000 / (len(out[: 16_000 // 2 * 2]))
    assert abs(peak_hz - 440) < 5


def test_mux_combines_video_and_shifted_audio(tmp_path):
    import av

    v, a, out = tmp_path / "v.mp4", tmp_path / "a.m4a", tmp_path / "recording.mp4"
    writer = video.VideoWriter(v, 160, 120)
    for i in range(30):
        writer.write(solid(160, 120, (i * 8, 0, 0)), i * 0.1)
    writer.close()
    video.write_m4a(tone(3.0), a)

    for offset in (0.5, -0.5):
        video.mux(v, a, offset, out)
        with av.open(str(out)) as c:
            assert len(c.streams.video) == 1 and len(c.streams.audio) == 1
            audio_start = min(p.pts * p.time_base for p in c.demux(c.streams.audio[0]) if p.pts is not None)
        if offset > 0:
            assert float(audio_start) == pytest.approx(0.5, abs=0.08)
        else:
            assert float(audio_start) < 0.08
        # The video is still decodable at the right times after the remux.
        frames = []
        video.for_each_frame(out, [1.0], lambda i, f: frames.append(f))
        assert abs(int(frames[0][60, 80, 0]) - 80) < 12
