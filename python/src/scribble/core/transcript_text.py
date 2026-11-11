"""Joining transcript pieces in scripts with and without spaces between words.

Mirrors ``Sources/ScribbleCore/TranscriptText.swift``.
"""

from __future__ import annotations

import unicodedata
from typing import List

from .models import Word

_UNSPACED = (
    (0x0E00, 0x0EFF),  # Thai, Lao
    (0x0F00, 0x0FFF),  # Tibetan
    (0x1000, 0x109F),  # Myanmar
    (0x1780, 0x17FF),  # Khmer
    (0x3000, 0x312F),  # CJK punctuation, Hiragana, Katakana, Bopomofo
    (0x31F0, 0x31FF),  # Katakana extensions
    (0x3400, 0x4DBF),  # CJK extension A
    (0x4E00, 0x9FFF),  # CJK ideographs
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0xFF00, 0xFFEF),  # Fullwidth forms
    (0x20000, 0x3FFFF),  # CJK supplementary planes
)

#: Thai and Lao SARA AM: letters by category, but they attach to the character before them.
_SPACING_MARKS = {0x0E33, 0x0EB3}


def is_unspaced(character: str) -> bool:
    """Scripts written without spaces between words. Whisper returns these as small
    fragments without leading spaces, so a separator must not be added. Korean uses spaces."""
    if not character:
        return False
    code = ord(character[0])
    return any(lo <= code <= hi for lo, hi in _UNSPACED)


def spaced(piece: str, after: str) -> str:
    """``piece`` as it should be appended to the text ``after``."""
    if piece.startswith(" "):
        return piece
    # A mark reference stays set off from the text around it.
    if piece and is_unspaced(piece[0]) and after and not after.endswith("]"):
        return piece
    return " " + piece


def joined(segments: List[str]) -> str:
    """Segments as prose."""
    text = ""
    for segment in segments:
        text += spaced(segment, text)
    return text.strip()


def _combines(text: str) -> bool:
    """Whether ``text`` starts with a mark that belongs to the character before it."""
    first = text[0]
    return unicodedata.category(first) in ("Mn", "Mc", "Me") or ord(first) in _SPACING_MARKS


def merged_fragments(words: List[Word]) -> List[Word]:
    """Repairs fragments from Whisper's word timing: a combining mark (Thai vowels and
    tones) joins the character it belongs to, so a mark reference can't split them, and a
    whitespace-only fragment becomes the leading space of the next word."""
    merged: List[Word] = []
    pending_space = False
    for word in words:
        if not word.text:
            continue
        if not word.text.strip():
            pending_space = True
            continue
        if not pending_space and merged and _combines(word.text):
            last = merged[-1]
            merged[-1] = Word(last.text + word.text, last.start, max(last.end, word.end))
        else:
            text = word.text
            if pending_space and not text.startswith(" "):
                text = " " + text
            merged.append(Word(text, word.start, word.end) if text != word.text else word)
        pending_space = False
    return merged
