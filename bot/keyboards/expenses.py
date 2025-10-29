"""Reply-клавиатуры для сценария «Расходы смены».

В модуле собраны генераторы всех клавиатур, используемых в разделе: старт, 
пошаговый ввод и подтверждение. Все кнопки — reply, чтобы соответствовать 
общей политике проекта.
"""

from __future__ import annotations

from aiogram.types import ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiogram.utils.keyboard import ReplyKeyboardBuilder

START_EXPENSES_BUTTON = "🧾 Начать ввод расходов"
MENU_BUTTON = "🏠 В меню смены"
SKIP_BUTTON = "Пропустить"
CONFIRM_BUTTON = "✅ Подтвердить"
EDIT_BUTTON = "✏️ Изменить"


def expenses_start_keyboard() -> ReplyKeyboardMarkup:
    """Стартовая клавиатура раздела с кнопкой запуска и возвратом."""

    builder = ReplyKeyboardBuilder()
    builder.button(text=START_EXPENSES_BUTTON)
    builder.button(text=MENU_BUTTON)
    builder.adjust(1, 1)
    return builder.as_markup(resize_keyboard=True)


def expenses_ship_keyboard(suggestions: list[str]) -> ReplyKeyboardMarkup:
    """Клавиатура выбора судна. Предлагает подсказки и возврат."""

    builder = ReplyKeyboardBuilder()
    for name in suggestions:
        builder.button(text=name)
    builder.button(text=MENU_BUTTON)
    row_sizes: list[int] = [1] * len(suggestions)
    row_sizes.append(1)
    builder.adjust(*row_sizes)
    return builder.as_markup(resize_keyboard=True)


def expenses_holds_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура выбора числа трюмов (1–7) с кнопкой возврата."""

    builder = ReplyKeyboardBuilder()
    for number in range(1, 8):
        builder.button(text=str(number))
    builder.button(text=MENU_BUTTON)
    builder.adjust(7, 1)
    return builder.as_markup(resize_keyboard=True)


def expenses_amount_keyboard(*, include_skip: bool = True) -> ReplyKeyboardMarkup:
    """Клавиатура для денежных шагов: «Пропустить» и возврат в меню."""

    builder = ReplyKeyboardBuilder()
    if include_skip:
        builder.button(text=SKIP_BUTTON)
    builder.button(text=MENU_BUTTON)
    builder.adjust(1, 1)
    return builder.as_markup(resize_keyboard=True)


def expenses_confirm_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура экрана подтверждения данных."""

    builder = ReplyKeyboardBuilder()
    builder.button(text=CONFIRM_BUTTON)
    builder.button(text=EDIT_BUTTON)
    builder.button(text=MENU_BUTTON)
    builder.adjust(2, 1)
    return builder.as_markup(resize_keyboard=True)


def expenses_remove_keyboard() -> ReplyKeyboardRemove:
    """Скрывает клавиатуру после завершения сценария."""

    return ReplyKeyboardRemove()
