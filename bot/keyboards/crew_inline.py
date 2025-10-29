"""Inline-клавиатуры для списка выбранных рабочих."""

from __future__ import annotations

from typing import Sequence

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services import CrewWorker

__all__ = ["REMOVE_PREFIX", "make_list_kb"]

REMOVE_PREFIX = "crew:rm:"


def make_list_kb(selected: Sequence[CrewWorker]) -> InlineKeyboardMarkup:
    """Формирует inline-клавиатуру удаления выбранных рабочих."""

    builder = InlineKeyboardBuilder()
    for worker in selected:
        builder.button(
            text=f"✖ {worker.name}",
            callback_data=f"{REMOVE_PREFIX}{worker.worker_id}",
        )
    builder.adjust(1)
    return builder.as_markup()
