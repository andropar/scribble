"""On-device transcription with faster-whisper (CTranslate2).

The model is downloaded once into ``~/.voice-annotate/models`` and kept loaded. It runs on
the CPU, or on an NVIDIA GPU when CUDA with cuDNN is usable; that is checked in a child
process first, because a broken CUDA setup aborts the process instead of raising.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

import numpy as np

from . import paths, video
from .core.models import SpeechSegment, Word
from .settings import WhisperCompute, WhisperModel

log = logging.getLogger(__name__)

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

REPOSITORIES = {
    "tiny.en": "Systran/faster-whisper-tiny.en",
    "base.en": "Systran/faster-whisper-base.en",
    "small.en": "Systran/faster-whisper-small.en",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}
_MODEL_FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]


@dataclass(frozen=True)
class Status:
    kind: str = "idle"  # idle | downloading | loading | ready | failed
    progress: float = 0.0
    message: str = ""
    #: "cpu" or "cuda" once ready.
    device: str = ""
    model: str = ""

    @property
    def title(self) -> str:
        if self.kind == "downloading":
            return "Downloading model… {}%".format(int(self.progress * 100))
        if self.kind == "loading":
            return "Loading transcription model…"
        if self.kind == "ready":
            where = "NVIDIA GPU" if self.device == "cuda" else "CPU"
            return "Transcription model ready ({} on {})".format(self.model, where)
        if self.kind == "failed":
            return "Model failed: {}".format(self.message)
        return "Transcription model not loaded"

    @property
    def is_ready(self) -> bool:
        return self.kind == "ready"


@dataclass
class TranscriptionOutput:
    #: Times relative to the start of the audio.
    words: List[Word] = field(default_factory=list)
    segments: List[SpeechSegment] = field(default_factory=list)

    @property
    def plain_text(self) -> str:
        """The transcript as plain prose."""
        return " ".join(s.text for s in self.segments)


def is_noise(word: str) -> bool:
    t = word.strip()
    if not t:
        return True
    if t.startswith("<|") and t.endswith("|>"):
        return True
    if t.startswith("[") and t.endswith("]"):
        return True
    if t.startswith("(") and t.endswith(")") and len(t) > 2:
        return True
    return False


def clean_text(text: str) -> str:
    return re.sub(r"<\|[^|]*\|>", "", text).strip()


def convert(segments: Any) -> TranscriptionOutput:
    """Converts faster-whisper segments (with ``words``) into the capture model."""
    out = TranscriptionOutput()
    for segment in segments:
        text = clean_text(segment.text)
        if text:
            out.segments.append(SpeechSegment(text, float(segment.start), float(segment.end)))
        for w in segment.words or []:
            if not is_noise(w.word):
                out.words.append(Word(w.word, float(w.start), float(w.end)))
    out.words.sort(key=lambda w: w.start)
    out.segments.sort(key=lambda s: s.start)
    return out


def resolve_model(model: WhisperModel, device: str) -> str:
    if model == WhisperModel.AUTO:
        return "large-v3-turbo" if device == "cuda" else "small.en"
    return model.value


# MARK: model files


def _repo_folder(repo_id: str, models_dir: Path) -> Path:
    return models_dir / ("models--" + repo_id.replace("/", "--"))


def _folder_bytes(folder: Path) -> int:
    total = 0
    for base, _, files in os.walk(folder / "blobs"):
        for name in files:
            try:
                total += os.lstat(os.path.join(base, name)).st_size
            except OSError:
                pass
    return total


def _complete(path: Optional[str]) -> bool:
    return bool(path) and all(
        (Path(path) / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")
    )


def download_model(name: str, models_dir: Path, on_progress: Callable[[float], None]) -> str:
    """Returns the local folder of a model, downloading it from the Hugging Face Hub if needed."""
    import huggingface_hub
    from faster_whisper.utils import disabled_tqdm

    repo_id = REPOSITORIES.get(name, name)
    models_dir.mkdir(parents=True, exist_ok=True)
    try:
        local = huggingface_hub.snapshot_download(
            repo_id, cache_dir=str(models_dir), allow_patterns=_MODEL_FILES, local_files_only=True
        )
        if _complete(local):
            return local
    except Exception:
        pass

    total = 0
    try:
        info = huggingface_hub.HfApi().model_info(repo_id, files_metadata=True)
        total = sum(
            (s.size or 0)
            for s in info.siblings
            if any(fnmatch.fnmatch(s.rfilename, pattern) for pattern in _MODEL_FILES)
        )
    except Exception as error:
        log.info("Could not read the model size (progress stays at 0%%): %s", error)

    done = threading.Event()

    def watch() -> None:
        folder = _repo_folder(repo_id, models_dir)
        while not done.wait(0.5):
            if total > 0:
                on_progress(min(0.99, _folder_bytes(folder) / total))

    watcher = threading.Thread(target=watch, name="model-download-progress", daemon=True)
    watcher.start()
    try:
        local = huggingface_hub.snapshot_download(
            repo_id, cache_dir=str(models_dir), allow_patterns=_MODEL_FILES, tqdm_class=disabled_tqdm
        )
    finally:
        done.set()
    if not _complete(local):
        raise RuntimeError("Model download is incomplete: {}".format(local))
    return local


# MARK: CUDA


def _register_nvidia_libraries() -> None:
    """Makes cuBLAS/cuDNN from the ``nvidia-*`` pip wheels loadable by CTranslate2."""
    import importlib.util

    spec = importlib.util.find_spec("nvidia")
    if spec is None or not spec.submodule_search_locations:
        return
    for base in spec.submodule_search_locations:
        for sub in ("cublas", "cudnn"):
            if sys.platform == "win32":
                folder = Path(base) / sub / "bin"
                if folder.is_dir():
                    os.add_dll_directory(str(folder))
                    os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
            else:
                import ctypes

                for lib in sorted((Path(base) / sub / "lib").glob("*.so.*")):
                    try:
                        ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
                    except OSError as error:
                        log.debug("Could not preload %s: %s", lib, error)


def probe_cuda_main(model_dir: str) -> int:
    """Child-process entry (``--probe-cuda``): exits 0 if the model runs on CUDA."""
    _register_nvidia_libraries()
    from faster_whisper import WhisperModel as Model

    model = Model(model_dir, device="cuda", compute_type="default")
    segments, _ = model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language="en")
    list(segments)
    return 0


def cuda_device_count() -> int:
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def cuda_works(model_dir: str, timeout: float = 120) -> bool:
    if cuda_device_count() <= 0:
        return False
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--probe-cuda", model_dir]
    else:
        command = [sys.executable, "-m", "voice_annotate", "--probe-cuda", model_dir]
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(command, capture_output=True, timeout=timeout, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as error:
        log.warning("CUDA probe did not finish: %s", error)
        return False
    if result.returncode != 0:
        log.warning("CUDA is not usable, transcribing on the CPU. Probe output: %s", result.stderr[-400:])
    return result.returncode == 0


SAMPLE_RATE = video.SAMPLE_RATE


class Transcriber:
    """Keeps one Whisper model loaded and transcribes 16 kHz mono samples with word timings."""

    def __init__(self, models_dir: Optional[Path] = None):
        self._models_dir = models_dir
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._status = Status()
        self._listeners: List[Callable[[Status], None]] = []
        self._future: Optional[Future] = None
        self._key: Optional[Tuple[WhisperModel, WhisperCompute]] = None

    @property
    def models_dir(self) -> Path:
        return self._models_dir or paths.models()

    @property
    def status(self) -> Status:
        return self._status

    def subscribe(self, listener: Callable[[Status], None]) -> None:
        """``listener`` is called from worker threads whenever the status changes."""
        self._listeners.append(listener)

    def _set_status(self, status: Status) -> None:
        if status == self._status:
            return
        self._status = status
        for listener in list(self._listeners):
            try:
                listener(status)
            except Exception:
                log.exception("Status listener failed")

    def preload(self, model: WhisperModel, compute: WhisperCompute) -> None:
        self.ensure_loaded(model, compute)

    def ensure_loaded(self, model: WhisperModel, compute: WhisperCompute) -> Future:
        """Starts loading (once per model/compute pair) and returns a future of the model."""
        key = (model, compute)
        with self._lock:
            if self._future is not None and self._key == key:
                failed = self._future.done() and self._future.exception() is not None
                if not failed:
                    return self._future
            future: Future = Future()
            self._future, self._key = future, key
        thread = threading.Thread(target=self._load, args=(future, key), name="model-load", daemon=True)
        thread.start()
        return future

    def _load(self, future: Future, key: Tuple[WhisperModel, WhisperCompute]) -> None:
        model_setting, compute = key

        def current() -> bool:
            return self._key == key

        try:
            from faster_whisper import WhisperModel as Model

            want_cuda = compute != WhisperCompute.CPU and cuda_device_count() > 0
            device = "cuda" if want_cuda else "cpu"
            name = resolve_model(model_setting, device)

            def fetch(model_name: str) -> str:
                if current():
                    self._set_status(Status("downloading", 0.0, model=model_name))
                return download_model(
                    model_name,
                    self.models_dir,
                    lambda p: current() and self._set_status(Status("downloading", p, model=model_name)),
                )

            folder = fetch(name)
            if current():
                self._set_status(Status("loading", model=name))
            if device == "cuda" and not cuda_works(folder):
                device = "cpu"
                fallback = resolve_model(model_setting, device)
                if fallback != name:
                    name, folder = fallback, fetch(fallback)
                    if current():
                        self._set_status(Status("loading", model=name))
            started = time.time()
            if device == "cuda":
                _register_nvidia_libraries()
                loaded = Model(folder, device="cuda", compute_type="default")
            else:
                threads = max(4, min(8, (os.cpu_count() or 4) // 2))
                loaded = Model(folder, device="cpu", compute_type="int8", cpu_threads=threads)
            log.info("Whisper %s ready on %s after %.1fs", name, device, time.time() - started)
            future.set_result(loaded)
            if current():
                self._set_status(Status("ready", device=device, model=name))
        except BaseException as error:  # also a broken install (ImportError) must surface
            log.error("Model load failed: %s", error)
            future.set_exception(error if isinstance(error, Exception) else RuntimeError(str(error)))
            if current():
                self._set_status(Status("failed", message=str(error) or type(error).__name__))

    def transcribe(
        self, samples: np.ndarray, model: WhisperModel, compute: WhisperCompute, vocabulary: str = ""
    ) -> TranscriptionOutput:
        """Blocking; call from a worker thread. Raises if the model cannot be loaded."""
        # Silence (or no mic): skip, Whisper would hallucinate.
        if len(samples) <= SAMPLE_RATE // 2 or video.rms(samples) <= 0.0015:
            return TranscriptionOutput()
        whisper = self.ensure_loaded(model, compute).result()
        vocab = vocabulary.strip()
        started = time.time()
        with self._run_lock:
            segments, _ = whisper.transcribe(
                np.ascontiguousarray(samples, dtype=np.float32),
                language="en",
                task="transcribe",
                word_timestamps=True,
                vad_filter=True,
                condition_on_previous_text=False,
                hotwords=vocab or None,
            )
            output = convert(segments)
        log.info(
            "Transcribed %.1fs audio in %.2fs, %d words",
            len(samples) / SAMPLE_RATE,
            time.time() - started,
            len(output.words),
        )
        return output


_shared: Optional[Transcriber] = None


def shared() -> Transcriber:
    global _shared
    if _shared is None:
        _shared = Transcriber()
    return _shared
