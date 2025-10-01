"""Utility helpers shared across source connectors."""
from __future__ import annotations

LANGUAGE_LABELS = {
    "en": "English / 英语",
    "zh": "中文",
    "es": "Español / 西班牙语",
    "de": "Deutsch / 德语",
    "fr": "Français / 法语",
    "ja": "日本語",
    "ru": "Русский",
}

LANGUAGE_ALIASES = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "en-us": "en",
    "en-gb": "en",
    "zh": "zh",
    "zho": "zh",
    "chi": "zh",
    "zh-cn": "zh",
    "zh-tw": "zh",
    "chinese": "zh",
    "es": "es",
    "spa": "es",
    "spanish": "es",
    "de": "de",
    "ger": "de",
    "deu": "de",
    "german": "de",
    "fr": "fr",
    "fra": "fr",
    "fre": "fr",
    "french": "fr",
    "ja": "ja",
    "jpn": "ja",
    "japanese": "ja",
    "ru": "ru",
    "rus": "ru",
    "russian": "ru",
}


def normalize_language_code(value: str | None) -> str | None:
    """Return a canonical ISO-like language code for comparisons."""

    if not value:
        return None
    token = value.strip().lower()
    if not token:
        return None
    if "-" in token:
        token = token.split("-", 1)[0]
    return LANGUAGE_ALIASES.get(token, token)


def language_display(code: str | None, fallback: str | None = None) -> str | None:
    """Return a human readable label for the given canonical code."""

    if code:
        return LANGUAGE_LABELS.get(code, fallback or code)
    return fallback


__all__ = ["language_display", "normalize_language_code", "LANGUAGE_LABELS"]
