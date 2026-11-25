"""
Canonical language inventory shared by Shelfmark, Komga and Audiobookshelf.

All three systems can be spoken to in ISO 639-1 two-letter codes:

- Shelfmark takes/returns ISO 639-1 in its `languages` release parameter.
- Komga stores `metadata.language` as an ISO 639-1 code.
- Audiobookshelf stores `media.metadata.language` as free text
  ("English", "French", ...) or null.

Language is therefore the ISO 639-1 code, and `normalize_language` accepts
every spelling those systems (and release titles) produce.
"""

import re
import unicodedata
from enum import Enum
from typing import Dict, List, Optional


class Language(str, Enum):
    ARABIC = "ar"
    CATALAN = "ca"
    CHINESE = "zh"
    CZECH = "cs"
    DANISH = "da"
    DUTCH = "nl"
    ENGLISH = "en"
    FINNISH = "fi"
    FRENCH = "fr"
    GERMAN = "de"
    GREEK = "el"
    HEBREW = "he"
    HINDI = "hi"
    HUNGARIAN = "hu"
    ITALIAN = "it"
    JAPANESE = "ja"
    KOREAN = "ko"
    NORWEGIAN = "no"
    POLISH = "pl"
    PORTUGUESE = "pt"
    ROMANIAN = "ro"
    RUSSIAN = "ru"
    SPANISH = "es"
    SWEDISH = "sv"
    TURKISH = "tr"
    UKRAINIAN = "uk"


LANGUAGE_LABELS: Dict[str, str] = {
    Language.ARABIC.value: "Arabic",
    Language.CATALAN.value: "Catalan",
    Language.CHINESE.value: "Chinese",
    Language.CZECH.value: "Czech",
    Language.DANISH.value: "Danish",
    Language.DUTCH.value: "Dutch",
    Language.ENGLISH.value: "English",
    Language.FINNISH.value: "Finnish",
    Language.FRENCH.value: "French",
    Language.GERMAN.value: "German",
    Language.GREEK.value: "Greek",
    Language.HEBREW.value: "Hebrew",
    Language.HINDI.value: "Hindi",
    Language.HUNGARIAN.value: "Hungarian",
    Language.ITALIAN.value: "Italian",
    Language.JAPANESE.value: "Japanese",
    Language.KOREAN.value: "Korean",
    Language.NORWEGIAN.value: "Norwegian",
    Language.POLISH.value: "Polish",
    Language.PORTUGUESE.value: "Portuguese",
    Language.ROMANIAN.value: "Romanian",
    Language.RUSSIAN.value: "Russian",
    Language.SPANISH.value: "Spanish",
    Language.SWEDISH.value: "Swedish",
    Language.TURKISH.value: "Turkish",
    Language.UKRAINIAN.value: "Ukrainian",
}

# ISO 639-2 (bibliographic and terminological) plus the English and French
# spellings the three systems use, mapped to ISO 639-1.
_ALIASES: Dict[str, str] = {
    "ar": "ar", "ara": "ar", "arabic": "ar", "arabe": "ar",
    "ca": "ca", "cat": "ca", "catalan": "ca",
    "zh": "zh", "zho": "zh", "chi": "zh", "chinese": "zh", "chinois": "zh",
    "cs": "cs", "ces": "cs", "cze": "cs", "czech": "cs", "tcheque": "cs",
    "da": "da", "dan": "da", "danish": "da", "danois": "da",
    "nl": "nl", "nld": "nl", "dut": "nl", "dutch": "nl", "neerlandais": "nl",
    "en": "en", "eng": "en", "english": "en", "anglais": "en",
    "fi": "fi", "fin": "fi", "finnish": "fi", "finnois": "fi",
    "fr": "fr", "fre": "fr", "fra": "fr", "french": "fr", "francais": "fr",
    "de": "de", "ger": "de", "deu": "de", "german": "de", "allemand": "de", "deutsch": "de",
    "el": "el", "gre": "el", "ell": "el", "greek": "el", "grec": "el",
    "he": "he", "heb": "he", "hebrew": "he", "hebreu": "he",
    "hi": "hi", "hin": "hi", "hindi": "hi",
    "hu": "hu", "hun": "hu", "hungarian": "hu", "hongrois": "hu",
    "it": "it", "ita": "it", "italian": "it", "italien": "it", "italiano": "it",
    "ja": "ja", "jpn": "ja", "japanese": "ja", "japonais": "ja",
    "ko": "ko", "kor": "ko", "korean": "ko", "coreen": "ko",
    "no": "no", "nor": "no", "norwegian": "no", "norvegien": "no",
    "pl": "pl", "pol": "pl", "polish": "pl", "polonais": "pl",
    "pt": "pt", "por": "pt", "portuguese": "pt", "portugais": "pt", "portugues": "pt",
    "ro": "ro", "ron": "ro", "rum": "ro", "romanian": "ro", "roumain": "ro",
    "ru": "ru", "rus": "ru", "russian": "ru", "russe": "ru",
    "es": "es", "spa": "es", "spanish": "es", "espagnol": "es", "espanol": "es",
    "sv": "sv", "swe": "sv", "swedish": "sv", "suedois": "sv",
    "tr": "tr", "tur": "tr", "turkish": "tr", "turc": "tr",
    "uk": "uk", "ukr": "uk", "ukrainian": "uk", "ukrainien": "uk",
}

_FOLD_RE = re.compile(r"[^a-z0-9]+")
_WORD_RE = re.compile(r"[a-z]{3,}")


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def normalize_language(value: Optional[str]) -> Optional[Language]:
    """Map a language code/name to its Language, or None when unknown."""
    if not value:
        return None
    text = _fold(str(value)).strip()
    if not text:
        return None
    code = _ALIASES.get(text)
    if code is None:
        # "en-US", "French (France)", "pt_BR": keep the leading token.
        token = _FOLD_RE.split(text)[0]
        code = _ALIASES.get(token)
    if code is None or code not in LANGUAGE_LABELS:
        return None
    return Language(code)


def detect_language_in_text(text: Optional[str]) -> Optional[Language]:
    """Best-effort language hint from a release title ("...[Spanish]"...).

    Only whole words of 3+ letters are considered, so French articles such as
    "de" or "la" never match a language code.
    """
    if not text:
        return None
    for word in _WORD_RE.findall(_fold(text)):
        code = _ALIASES.get(word)
        if code and code in LANGUAGE_LABELS:
            return Language(code)
    return None


def language_options() -> List[Dict[str, str]]:
    """Language list for the UI, sorted by label."""
    return [
        {"value": code, "label": LANGUAGE_LABELS[code]}
        for code in sorted(LANGUAGE_LABELS, key=lambda c: LANGUAGE_LABELS[c])
    ]


def language_label(value: Optional[str]) -> str:
    lang = normalize_language(value)
    if lang is None:
        return str(value) if value else "unknown"
    return LANGUAGE_LABELS[lang.value]
