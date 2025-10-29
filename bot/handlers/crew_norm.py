"""Утилиты нормализации текста для Reply-кнопок раздела «Бригада».

Функции из модуля приводят текст к устойчивому виду, убирая вариационные
селекторы, лишние пробелы и регистровые различия. Это необходимо, потому что
Telegram может подставлять различные emoji-представления (например, добавлять
``\uFE0F``), из-за чего прямое сравнение строк перестаёт работать.
"""

from __future__ import annotations

import unicodedata

__all__ = ["norm_text"]


def norm_text(text: str | None) -> str:
    """Нормализует текст Reply-кнопок для надёжного сравнения."""

    if not text:
        return ""
    normalized = text.replace("\uFE0F", "")
    normalized = unicodedata.normalize("NFKC", normalized)
    normalized = " ".join(normalized.split())
    return normalized.casefold()
