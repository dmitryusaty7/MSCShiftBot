"""Reply-клавиатуры для сценария регистрации."""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

# Текстовые константы для reply-кнопок сценария регистрации
START_BUTTON = "Начать регистрацию"
SKIP_BUTTON = "Пропустить"
CONFIRM_BUTTON = "Подтвердить"
RETRY_BUTTON = "Ввести заново"
CANCEL_BUTTON = "Отмена"


def start_registration_kb() -> ReplyKeyboardMarkup:
    """Reply-клавиатура запуска регистрации с кнопками старта и отмены."""

    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        keyboard=[
            [KeyboardButton(text=START_BUTTON)],
            [KeyboardButton(text=CANCEL_BUTTON)],
        ],
    )


def skip_button_kb() -> ReplyKeyboardMarkup:
    """Reply-клавиатура шага отчества с кнопкой пропуска и отмены."""

    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        keyboard=[
            [KeyboardButton(text=SKIP_BUTTON)],
            [KeyboardButton(text=CANCEL_BUTTON)],
        ],
    )


def confirm_retry_kb() -> ReplyKeyboardMarkup:
    """Reply-клавиатура подтверждения данных с повторами и отменой."""

    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        keyboard=[
            [
                KeyboardButton(text=CONFIRM_BUTTON),
                KeyboardButton(text=RETRY_BUTTON),
            ],
            [KeyboardButton(text=CANCEL_BUTTON)],
        ],
    )
