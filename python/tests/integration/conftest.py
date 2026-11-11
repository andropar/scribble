import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from fakes import FakeAudio, pump, wait_until  # noqa: E402
from support import AppWindow, Env, Transcriber  # noqa: E402

from scribble import sounds  # noqa: E402
from scribble.clipboard import RecentsStore  # noqa: E402
from scribble.session import Phase, SessionController  # noqa: E402
from scribble.settings import AppSettings  # noqa: E402

# Controllers stay referenced for the whole run (see tests/test_session.py).
_controllers = []


@pytest.fixture(scope="session")
def env(qapp):
    environment = Env()
    yield environment
    environment.inject.release_all()


@pytest.fixture
def target(env):
    """A blue window covering (0,0)-(900,650) of the first display, focused by a real click."""
    window = AppWindow("#2e6fd0", 0, 0, 900, 650, env.display)
    window.show()
    window.raise_()
    window.activateWindow()
    pump(0.4)
    # A real click makes it the foreground window on every system (and, on Windows, lets
    # this process take the foreground later, as a user-started app could).
    env.click(880, 630)
    assert wait_until(lambda: window.isActiveWindow(), 5), "test window did not get focus"
    window.clicks.clear()
    pump(0.3)
    yield window
    window.close()
    pump(0.1)


@pytest.fixture
def controller(env, tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBBLE_HOME", str(tmp_path / "home"))
    sounds.enabled = False
    c = SessionController(
        AppSettings(),
        RecentsStore(),
        Transcriber(start=0.2, step=0.2),
        env.platform,
        env.grabber,
        audio_factory=FakeAudio,
    )
    c.finished = []
    c.capture_finished.connect(c.finished.append)
    _controllers.append(c)
    yield c
    env.inject.release_all()
    if c.phase.is_capturing:
        c.cancel()
    wait_until(lambda: c.phase == Phase.IDLE, 20)
    c.wait_for_background()
    c.toast.hide()
    pump(0.2)
