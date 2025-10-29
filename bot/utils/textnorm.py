"""Утилиты нормализации текста для Reply- и Inline-кнопок."""

from __future__ import annotations

import unicodedata

__all__ = ["norm_text"]


def norm_text(text: str | None) -> str:
    """Приводит текст к устойчивому виду для сравнения.

    Telegram может подставлять variation selector ``\uFE0F`` и дополнительные
    пробелы либо символы управления. Чтобы корректно сопоставлять нажатые
    кнопки с константами, нормализуем строку: убираем ``\uFE0F``, приводим к
    форме NFKC, схлопываем пробелы и переводим в нижний регистр.
    """

    if not text:
        return ""

    normalized = text.replace("\uFE0F", "")
    normalized = unicodedata.normalize("NFKC", normalized)
    normalized = " ".join(normalized.split())
    return normalized.casefold()
