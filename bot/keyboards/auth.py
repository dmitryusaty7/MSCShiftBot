"""Inline-клавиатуры для сценария регистрации."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Константы callback-данных для сценария регистрации
START_PAYLOAD = "registration:start"
SKIP_PAYLOAD = "registration:skip"
CONFIRM_PAYLOAD = "registration:confirm"
RETRY_PAYLOAD = "registration:retry"


def start_registration_kb() -> InlineKeyboardMarkup:
    """Кнопка запуска сценария регистрации."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Начать регистрацию", callback_data=START_PAYLOAD)]
        ]
    )


def skip_button_kb() -> InlineKeyboardMarkup:
    """Клавиатура с возможностью пропустить ввод отчества."""

    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Пропустить", callback_data=SKIP_PAYLOAD)]]
    )


def confirm_retry_kb() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения данных или возврата к повторному вводу."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Подтвердить", callback_data=CONFIRM_PAYLOAD),
                InlineKeyboardButton(text="Ввести заново", callback_data=RETRY_PAYLOAD),
            ]
        ]
    )
