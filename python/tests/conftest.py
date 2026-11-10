import os
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_addoption(parser):
    parser.addoption(
        "--desktop",
        action="store_true",
        default=False,
        help="run tests that need a real desktop session (X11 or Windows) and drive real input",
    )


def pytest_configure(config):
    # Everything except the desktop tests runs without a display.
    if not config.getoption("--desktop"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Never touch the user's real ~/.voice-annotate from tests.
    if "VOICE_ANNOTATE_HOME" not in os.environ:
        import tempfile

        os.environ["VOICE_ANNOTATE_HOME"] = tempfile.mkdtemp(prefix="voice-annotate-tests-")


def pytest_collection_modifyitems(config, items):
    skip_desktop = pytest.mark.skip(reason="needs --desktop")
    skip_model = pytest.mark.skip(reason="needs VOICE_ANNOTATE_TEST_MODEL=1")
    for item in items:
        if "desktop" in item.keywords and not config.getoption("--desktop"):
            item.add_marker(skip_desktop)
        if "model" in item.keywords and not os.environ.get("VOICE_ANNOTATE_TEST_MODEL"):
            item.add_marker(skip_model)


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    yield app


@pytest.fixture
def fixtures():
    return FIXTURES
