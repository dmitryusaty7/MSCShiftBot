"""Reply-клавиатуры для раздела «Материалы».

В сценарии раздела используются только reply-кнопки согласно политике проекта:
стартовая кнопка, шаги ввода чисел, управление загрузкой фото и подтверждение.
"""

from __future__ import annotations

from aiogram.types import ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiogram.utils.keyboard import ReplyKeyboardBuilder

START_MATERIALS_BUTTON = "📦 Начать ввод материалов"
MENU_BUTTON = "🏠 В меню смены"
SKIP_BUTTON = "Пропустить"
CONFIRM_BUTTON = "✅ Подтвердить"
EDIT_BUTTON = "✏️ Изменить"
DELETE_LAST_BUTTON = "🗑 Удалить последнее"

__all__ = [
    "START_MATERIALS_BUTTON",
    "MENU_BUTTON",
    "SKIP_BUTTON",
    "CONFIRM_BUTTON",
    "EDIT_BUTTON",
    "DELETE_LAST_BUTTON",
    "materials_start_keyboard",
    "materials_amount_keyboard",
    "materials_photos_keyboard",
    "materials_confirm_keyboard",
    "materials_remove_keyboard",
]


def materials_start_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает стартовую клавиатуру раздела с кнопками запуска и выхода."""

    builder = ReplyKeyboardBuilder()
    builder.button(text=START_MATERIALS_BUTTON)
    builder.button(text=MENU_BUTTON)
    builder.adjust(1, 1)
    return builder.as_markup(resize_keyboard=True)


def materials_amount_keyboard(*, include_skip: bool = True) -> ReplyKeyboardMarkup:
    """Клавиатура для числовых шагов: «Пропустить» и возврат в меню."""

    builder = ReplyKeyboardBuilder()
    if include_skip:
        builder.button(text=SKIP_BUTTON)
    builder.button(text=MENU_BUTTON)
    builder.adjust(1, 1)
    return builder.as_markup(resize_keyboard=True)


def materials_photos_keyboard() -> ReplyKeyboardMarkup:
    """Управление этапом загрузки фото (подтверждение и удаление)."""

    builder = ReplyKeyboardBuilder()
    builder.button(text=CONFIRM_BUTTON)
    builder.button(text=DELETE_LAST_BUTTON)
    builder.button(text=MENU_BUTTON)
    builder.adjust(2, 1)
    return builder.as_markup(resize_keyboard=True)


def materials_confirm_keyboard() -> ReplyKeyboardMarkup:
    """Экран подтверждения данных раздела."""

    builder = ReplyKeyboardBuilder()
    builder.button(text=CONFIRM_BUTTON)
    builder.button(text=EDIT_BUTTON)
    builder.button(text=MENU_BUTTON)
    builder.adjust(2, 1)
    return builder.as_markup(resize_keyboard=True)


def materials_remove_keyboard() -> ReplyKeyboardRemove:
    """Скрывает клавиатуру после завершения сценария."""

    return ReplyKeyboardRemove()
