"""Reply-клавиатуры для подтверждения закрытия смены."""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

__all__ = [
    "CONFIRM_CLOSE_BUTTON",
    "CANCEL_CLOSE_BUTTON",
    "close_confirmation_keyboard",
]

CONFIRM_CLOSE_BUTTON = "✅ Подтвердить закрытие"
CANCEL_CLOSE_BUTTON = "↩ Отмена"


def close_confirmation_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру с кнопками подтверждения или отмены закрытия смены."""

    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text=CONFIRM_CLOSE_BUTTON))
    builder.add(KeyboardButton(text=CANCEL_CLOSE_BUTTON))
    builder.adjust(1, 1)
    return builder.as_markup(resize_keyboard=True)
