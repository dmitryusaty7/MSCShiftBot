"""Inline-сводка шага выбора рабочих для раздела «Бригада»."""

from __future__ import annotations

from typing import Sequence

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services import CrewWorker

__all__ = [
    "WORKER_TOGGLE_PREFIX",
    "WORKERS_CONFIRM_CALLBACK",
    "make_workers_inline_summary",
]

WORKER_TOGGLE_PREFIX = "crew:wrk:toggle:"
WORKERS_CONFIRM_CALLBACK = "crew:wrk:confirm"


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
        "выбранные рабочие:",
    ]

    if selected:
        lines.extend(f"• {worker.name}" for worker in selected)
        lines.extend(["", "✖️ — удалить рабочего из списка"])
    else:
        lines.append("рабочие пока не выбраны")
        return "\n".join(lines), None

    builder = InlineKeyboardBuilder()
    for worker in selected:
        builder.button(
            text=f"✖ {worker.name}",
            callback_data=f"{WORKER_TOGGLE_PREFIX}{worker.worker_id}",
        )
    builder.adjust(2)

    return "\n".join(lines), builder.as_markup()
