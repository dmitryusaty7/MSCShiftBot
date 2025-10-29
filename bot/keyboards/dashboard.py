"""Клавиатуры главной панели и меню смены на reply-кнопках."""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiogram.utils.keyboard import ReplyKeyboardBuilder

__all__ = [
    "ReplyKeyboardRemove",
    "START_SHIFT_BUTTON",
    "GUIDE_BUTTON",
    "SHIFT_BACK_BUTTON",
    "FINISH_SHIFT_BUTTON",
    "expenses_button_text",
    "materials_button_text",
    "crew_button_text",
    "dashboard_keyboard",
    "shift_menu_keyboard",
]

START_SHIFT_BUTTON = "🚀 Начать оформление смены"
GUIDE_BUTTON = "📘 Руководство"

EXPENSES_PREFIX = "🧾 Расходы"
MATERIALS_PREFIX = "📦 Материалы"
CREW_PREFIX = "👥 Состав бригады"
SHIFT_BACK_BUTTON = "⬅ В главное меню"
FINISH_SHIFT_BUTTON = "✅ Завершить смену"


def _status_badge(done: bool) -> str:
    """Возвращает подпись статуса для кнопки раздела."""

    return "✅ готово" if done else "✍️ заполнить"


def expenses_button_text(done: bool) -> str:
    """Генерирует текст кнопки раздела «Расходы» с учётом статуса."""

    return f"{EXPENSES_PREFIX} — {_status_badge(done)}"


def materials_button_text(done: bool) -> str:
    """Генерирует текст кнопки раздела «Материалы» с учётом статуса."""

    return f"{MATERIALS_PREFIX} — {_status_badge(done)}"


def crew_button_text(done: bool) -> str:
    """Генерирует текст кнопки раздела «Состав бригады» с учётом статуса."""

    return f"{CREW_PREFIX} — {_status_badge(done)}"


def dashboard_keyboard(*, include_guide: bool = False) -> ReplyKeyboardMarkup:
    """Формирует клавиатуру главной панели с кнопками запуска смены."""

    builder = ReplyKeyboardBuilder()
    builder.button(text=START_SHIFT_BUTTON)
    if include_guide:
        builder.button(text=GUIDE_BUTTON)
    builder.adjust(1, 1)
    return builder.as_markup(resize_keyboard=True)


def shift_menu_keyboard(
    *,
    expenses_done: bool,
    materials_done: bool,
    crew_done: bool,
    show_finish: bool,
) -> ReplyKeyboardMarkup:
    """Собирает клавиатуру меню смены с учётом прогресса разделов."""

    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text=expenses_button_text(expenses_done)))
    builder.add(KeyboardButton(text=materials_button_text(materials_done)))
    builder.add(KeyboardButton(text=crew_button_text(crew_done)))
    row_sizes = [1, 1, 1]
    if show_finish:
        builder.add(KeyboardButton(text=FINISH_SHIFT_BUTTON))
        row_sizes.append(1)
    builder.add(KeyboardButton(text=SHIFT_BACK_BUTTON))
    row_sizes.append(1)
    builder.adjust(*row_sizes)
    return builder.as_markup(resize_keyboard=True)
