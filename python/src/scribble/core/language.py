"""Transcription languages and how a language setting is decoded on a given model.

Mirrors ``Sources/ScribbleCore/TranscriptionLanguage.swift``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

#: The setting that leaves the language to Whisper.
AUTO = "auto"
ENGLISH = "en"

#: Every language of the multilingual Whisper models as (code, English name), sorted by name.
#: The code is Whisper's language token (``<|ru|>``).
LANGUAGES: List[Tuple[str, str]] = [
    ("af", "Afrikaans"), ("sq", "Albanian"), ("am", "Amharic"), ("ar", "Arabic"), ("hy", "Armenian"),
    ("as", "Assamese"), ("az", "Azerbaijani"), ("ba", "Bashkir"), ("eu", "Basque"), ("be", "Belarusian"),
    ("bn", "Bengali"), ("bs", "Bosnian"), ("br", "Breton"), ("bg", "Bulgarian"), ("my", "Burmese"),
    ("yue", "Cantonese"), ("ca", "Catalan"), ("zh", "Chinese"), ("hr", "Croatian"), ("cs", "Czech"),
    ("da", "Danish"), ("nl", "Dutch"), ("en", "English"), ("et", "Estonian"), ("fo", "Faroese"),
    ("fi", "Finnish"), ("fr", "French"), ("gl", "Galician"), ("ka", "Georgian"), ("de", "German"),
    ("el", "Greek"), ("gu", "Gujarati"), ("ht", "Haitian Creole"), ("ha", "Hausa"), ("haw", "Hawaiian"),
    ("he", "Hebrew"), ("hi", "Hindi"), ("hu", "Hungarian"), ("is", "Icelandic"), ("id", "Indonesian"),
    ("it", "Italian"), ("ja", "Japanese"), ("jw", "Javanese"), ("kn", "Kannada"), ("kk", "Kazakh"),
    ("km", "Khmer"), ("ko", "Korean"), ("lo", "Lao"), ("la", "Latin"), ("lv", "Latvian"),
    ("ln", "Lingala"), ("lt", "Lithuanian"), ("lb", "Luxembourgish"), ("mk", "Macedonian"),
    ("mg", "Malagasy"), ("ms", "Malay"), ("ml", "Malayalam"), ("mt", "Maltese"), ("mi", "Maori"),
    ("mr", "Marathi"), ("mn", "Mongolian"), ("ne", "Nepali"), ("no", "Norwegian"),
    ("nn", "Norwegian Nynorsk"), ("oc", "Occitan"), ("ps", "Pashto"), ("fa", "Persian"), ("pl", "Polish"),
    ("pt", "Portuguese"), ("pa", "Punjabi"), ("ro", "Romanian"), ("ru", "Russian"), ("sa", "Sanskrit"),
    ("sr", "Serbian"), ("sn", "Shona"), ("sd", "Sindhi"), ("si", "Sinhala"), ("sk", "Slovak"),
    ("sl", "Slovenian"), ("so", "Somali"), ("es", "Spanish"), ("su", "Sundanese"), ("sw", "Swahili"),
    ("sv", "Swedish"), ("tl", "Tagalog"), ("tg", "Tajik"), ("ta", "Tamil"), ("tt", "Tatar"),
    ("te", "Telugu"), ("th", "Thai"), ("bo", "Tibetan"), ("tr", "Turkish"), ("tk", "Turkmen"),
    ("uk", "Ukrainian"), ("ur", "Urdu"), ("uz", "Uzbek"), ("vi", "Vietnamese"), ("cy", "Welsh"),
    ("yi", "Yiddish"), ("yo", "Yoruba"),
]  # fmt: skip

_NAMES = dict(LANGUAGES)


def name(code: str) -> Optional[str]:
    """English name of a language code, or None if Whisper doesn't have it."""
    return _NAMES.get(code)


def parse(value: Optional[str]) -> Optional[str]:
    """``"auto"`` or a Whisper language code from user input, or None if it is neither."""
    text = (value or "").strip().lower()
    return text if text == AUTO or text in _NAMES else None


def from_storage(value: Optional[str]) -> str:
    """Reads a stored setting. A missing or unknown value is English, the app's original
    behaviour."""
    return parse(value) or ENGLISH


@dataclass(frozen=True)
class LanguagePlan:
    """How a capture gets decoded for a language setting on a given model."""

    #: Language forced on the decoder; None lets Whisper detect it for each piece of audio.
    language: Optional[str]
    #: Why the transcript may disappoint. The model can't be switched for the user (it may
    #: mean a large download), so this has to reach them.
    warning: Optional[str] = None


ENGLISH_ONLY_WARNING = (
    "This model only understands English, so other speech comes out translated or garbled. "
    "Pick a multilingual model."
)


def resolve(selection: str, is_multilingual: bool) -> LanguagePlan:
    if not is_multilingual:
        return LanguagePlan(ENGLISH, None if selection == ENGLISH else ENGLISH_ONLY_WARNING)
    if selection == AUTO:
        return LanguagePlan(None)
    # The Cantonese token gives an empty transcript, on large too, and small and base don't
    # have it. As Chinese the speech comes out readable, as it does with Auto-detect.
    return LanguagePlan("zh" if selection == "yue" else selection)
