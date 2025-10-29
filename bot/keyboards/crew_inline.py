"""Inline-сводка шага выбора рабочих для раздела «Бригада»."""

from __future__ import annotations

from typing import Sequence

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services import CrewWorker

__all__ = ["WORKER_TOGGLE_PREFIX", "make_workers_inline_summary"]

WORKER_TOGGLE_PREFIX = "crew:wrk:toggle:"


def make_workers_inline_summary(
    driver: CrewWorker | None,
    selected: Sequence[CrewWorker],
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Формирует текст и inline-клавиатуру сводки выбранных рабочих."""

    driver_name = driver.name if driver else "—"
    lines: list[str] = [
        "👥 Состав бригады — сводка",
        f"водитель: {driver_name}",
        "",
    ]

    if selected:
        lines.append("выбранные рабочие:")
        lines.extend(f"• {worker.name}" for worker in selected)
    else:
        lines.append("рабочие пока не выбраны.")

    lines.extend(["", "✖ — удалить рабочего из списка"])

    if selected:
        builder = InlineKeyboardBuilder()
        for worker in selected:
            builder.button(
                text=f"✖ {worker.name}",
                callback_data=f"{WORKER_TOGGLE_PREFIX}{worker.worker_id}",
            )
        builder.adjust(2)
        markup: InlineKeyboardMarkup | None = builder.as_markup()
    else:
        markup = None

    return "\n".join(lines), markup
