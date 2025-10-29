"""Валидаторы для проверки элементов ФИО при регистрации."""

from __future__ import annotations

import re

from services.sheets import normalize_name_piece

_NAME_PATTERN = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\- ]+$")


def validate_name(value: str) -> str:
    """Проверяет корректность части ФИО и возвращает нормализованное значение."""

    candidate = (value or "").strip()
    if not candidate or not _NAME_PATTERN.fullmatch(candidate):
        raise ValueError("Допустимы только буквы, пробел и дефис.")
    return normalize_name_piece(candidate)
