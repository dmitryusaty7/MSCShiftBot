"""Валидаторы числовых значений для сценариев FSM."""

from __future__ import annotations

import re

__all__ = ["parse_amount"]

_DIGITS_RE = re.compile(r"^\d{1,9}$")


def parse_amount(value: str, *, skip_token: str | None = None) -> int:
    """Преобразует текст в сумму расходов.

    Возвращает целое число, выбрасывая ``ValueError`` при некорректном формате.
    Если передан ``skip_token`` и значение соответствует ему, возвращается ``0``.
    """

    text = (value or "").strip()
    if not text:
        raise ValueError("только цифры (рубли) или «Пропустить».")
    if skip_token and text == skip_token:
        return 0
    if not _DIGITS_RE.fullmatch(text):
        raise ValueError("только цифры (рубли) или «Пропустить».")
    return int(text)
