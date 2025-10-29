"""Reply-клавиатуры пошагового режима «Бригада».

Модуль предоставляет фабрики клавиатур для интро, шага выбора водителя и
шага выбора рабочих. Каждая функция возвращает не только клавиатуру, но и
отображение нажатых текстов на идентификаторы справочника, чтобы обработчики
могли надёжно определить выбранный элемент без парсинга ФИО.
"""

from __future__ import annotations

from typing import Dict, Iterable, Sequence, Tuple

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from bot.services import CrewWorker

__all__ = [
    "START_BUTTON",
    "MENU_BUTTON",
    "BACK_BUTTON",
    "ADD_DRIVER_BUTTON",
    "ADD_WORKER_BUTTON",
    "CLEAR_WORKERS_BUTTON",
    "CONFIRM_BUTTON",
    "EDIT_BUTTON",
    "make_intro_kb",
    "make_driver_kb",
    "make_workers_kb",
    "make_confirmation_kb",
    "make_middle_prompt_kb",
]


START_BUTTON = "▶️ Начать заполнение"
MENU_BUTTON = "🏠 в меню смены"
BACK_BUTTON = "⬅ назад"
ADD_DRIVER_BUTTON = "➕ добавить водителя"
ADD_WORKER_BUTTON = "➕ добавить рабочего"
CLEAR_WORKERS_BUTTON = "🧹 очистить список"
CONFIRM_BUTTON = "✅ подтвердить"
EDIT_BUTTON = "✏️ изменить"
SKIP_MIDDLE_BUTTON = "Пропустить"


def _builder_with_resize() -> ReplyKeyboardBuilder:
    builder = ReplyKeyboardBuilder()
    builder.adjust(1)
    return builder


def make_intro_kb() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру стартового экрана режима «Бригада»."""

    builder = _builder_with_resize()
    builder.row(KeyboardButton(text=START_BUTTON))
    builder.row(KeyboardButton(text=MENU_BUTTON))
    return builder.as_markup(resize_keyboard=True)


def make_driver_kb(
    drivers: Sequence[CrewWorker],
    driver_id: int | None,
) -> Tuple[ReplyKeyboardMarkup, Dict[str, int]]:
    """Формирует клавиатуру списка водителей и отображение текстов на id."""

    builder = _builder_with_resize()
    mapping: Dict[str, int] = {}

    for worker in drivers:
        prefix = "✔" if worker.worker_id == driver_id else "👤"
        text = f"{prefix} {worker.name}"
        builder.row(KeyboardButton(text=text))
        mapping[text] = worker.worker_id

    builder.row(KeyboardButton(text=ADD_DRIVER_BUTTON))

    builder.row(KeyboardButton(text=BACK_BUTTON), KeyboardButton(text=MENU_BUTTON))

    return builder.as_markup(resize_keyboard=True), mapping


def make_workers_kb(
    workers: Sequence[CrewWorker],
    selected_ids: Iterable[int],
    *,
    include_confirm: bool | None = None,
) -> Tuple[ReplyKeyboardMarkup, Dict[str, int]]:
    """Формирует клавиатуру мультивыбора рабочих и карту текстов."""

    selected_set = set(selected_ids)
    builder = ReplyKeyboardBuilder()
    mapping: Dict[str, int] = {}

    row: list[KeyboardButton] = []
    for worker in workers:
        prefix = "✔" if worker.worker_id in selected_set else "👷"
        text = f"{prefix} {worker.name}"
        row.append(KeyboardButton(text=text))
        mapping[text] = worker.worker_id
        if len(row) == 2:
            builder.row(*row)
            row = []

    if row:
        builder.row(*row)

    builder.row(KeyboardButton(text=ADD_WORKER_BUTTON))
    builder.row(KeyboardButton(text=CLEAR_WORKERS_BUTTON))

    include_confirm_flag = bool(selected_set) if include_confirm is None else include_confirm
    if include_confirm_flag:
        builder.row(KeyboardButton(text=CONFIRM_BUTTON))

    navigation: list[KeyboardButton] = [
        KeyboardButton(text=BACK_BUTTON),
        KeyboardButton(text=MENU_BUTTON),
    ]
    builder.row(*navigation)

    return builder.as_markup(resize_keyboard=True), mapping


def make_confirmation_kb() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру экрана подтверждения."""

    builder = _builder_with_resize()
    builder.row(KeyboardButton(text=CONFIRM_BUTTON))
    builder.row(KeyboardButton(text=EDIT_BUTTON))
    builder.row(KeyboardButton(text=MENU_BUTTON))
    return builder.as_markup(resize_keyboard=True)


def make_middle_prompt_kb() -> ReplyKeyboardMarkup:
    """Клавиатура шага ввода отчества с кнопкой пропуска."""

    builder = _builder_with_resize()
    builder.row(KeyboardButton(text=SKIP_MIDDLE_BUTTON))
    builder.row(KeyboardButton(text=BACK_BUTTON), KeyboardButton(text=MENU_BUTTON))
    return builder.as_markup(resize_keyboard=True)
