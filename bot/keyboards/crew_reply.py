"""Reply-клавиатуры для раздела «Бригада» в новом сценарии."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

__all__ = [
    "ADD_WORKER_BUTTON",
    "CLEAR_WORKERS_BUTTON",
    "CONFIRM_BUTTON",
    "EDIT_BUTTON",
    "BACK_BUTTON",
    "MENU_BUTTON",
    "crew_start_keyboard",
    "crew_confirm_keyboard",
]

ADD_WORKER_BUTTON = "➕ добавить рабочего"
CLEAR_WORKERS_BUTTON = "🧹 очистить список рабочих"
CONFIRM_BUTTON = "✅ подтвердить"
EDIT_BUTTON = "✏️ изменить"
BACK_BUTTON = "⬅ назад"
MENU_BUTTON = "🏠 в меню смены"


def crew_start_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает стартовую клавиатуру режима «Бригада» с основными действиями."""

    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text=ADD_WORKER_BUTTON),
        KeyboardButton(text=CLEAR_WORKERS_BUTTON),
    )
    builder.row(KeyboardButton(text=CONFIRM_BUTTON))
    builder.row(
        KeyboardButton(text=BACK_BUTTON),
        KeyboardButton(text=MENU_BUTTON),
    )
    return builder.as_markup(resize_keyboard=True)


def crew_confirm_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура экрана подтверждения состава бригады."""

    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text=CONFIRM_BUTTON),
        KeyboardButton(text=EDIT_BUTTON),
    )
    return builder.as_markup(resize_keyboard=True)
