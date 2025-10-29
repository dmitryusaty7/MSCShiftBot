"""Inline-клавиатуры шагов режима «Бригада» (водитель → рабочие)."""

from __future__ import annotations

from math import ceil
from typing import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services import CrewWorker

__all__ = [
    "DRIVER_PICK_PREFIX",
    "DRIVER_LIST_PREFIX",
    "DRIVER_ADD_CALLBACK",
    "WORKER_TOGGLE_PREFIX",
    "WORKER_LIST_PREFIX",
    "WORKER_ADD_CALLBACK",
    "WORKER_CLEAR_CALLBACK",
    "CONFIRM_CALLBACK",
    "NAV_HOME_CALLBACK",
    "NAV_BACK_CALLBACK",
    "NOOP_CALLBACK",
    "build_driver_keyboard",
    "build_worker_keyboard",
]

ITEMS_PER_PAGE = 6

DRIVER_PICK_PREFIX = "crew:drv:pick:"
DRIVER_LIST_PREFIX = "crew:drv:list:"
DRIVER_ADD_CALLBACK = "crew:drv:add"

WORKER_TOGGLE_PREFIX = "crew:wrk:toggle:"
WORKER_LIST_PREFIX = "crew:wrk:list:"
WORKER_ADD_CALLBACK = "crew:wrk:add"
WORKER_CLEAR_CALLBACK = "crew:wrk:clear"

CONFIRM_CALLBACK = "crew:confirm"

NAV_HOME_CALLBACK = "nav:home"
NAV_BACK_CALLBACK = "nav:back"

NOOP_CALLBACK = "crew:noop"


def _paginate(items: Sequence[CrewWorker], page: int) -> tuple[list[CrewWorker], int, int]:
    if not items:
        return [], 0, 1

    total_pages = max(1, ceil(len(items) / ITEMS_PER_PAGE))
    actual_page = max(0, min(page, total_pages - 1))
    start = actual_page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    return list(items[start:end]), actual_page, total_pages


def build_driver_keyboard(
    drivers: Sequence[CrewWorker],
    *,
    page: int,
    selected_driver_id: int | None,
) -> tuple[InlineKeyboardMarkup, int, int]:
    """Формирует клавиатуру выбора водителя с пагинацией."""

    rows: list[list[InlineKeyboardButton]] = []

    page_items, actual_page, total_pages = _paginate(drivers, page)

    for driver in page_items:
        prefix = "✔" if driver.worker_id == selected_driver_id else "👤"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{prefix} {driver.name}",
                    callback_data=f"{DRIVER_PICK_PREFIX}{driver.worker_id}",
                )
            ]
        )

    rows.append([InlineKeyboardButton(text="➕ добавить водителя", callback_data=DRIVER_ADD_CALLBACK)])

    if total_pages > 1:
        prev_page = actual_page - 1 if actual_page > 0 else actual_page
        next_page = actual_page + 1 if actual_page + 1 < total_pages else actual_page
        rows.append(
            [
                InlineKeyboardButton(
                    text="‹ предыдущая",
                    callback_data=(
                        f"{DRIVER_LIST_PREFIX}{prev_page}" if actual_page > 0 else NOOP_CALLBACK
                    ),
                ),
                InlineKeyboardButton(
                    text="следующая ›",
                    callback_data=(
                        f"{DRIVER_LIST_PREFIX}{next_page}"
                        if actual_page + 1 < total_pages
                        else NOOP_CALLBACK
                    ),
                ),
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(text="⬅ назад", callback_data=NAV_BACK_CALLBACK),
            InlineKeyboardButton(text="🏠 в меню смены", callback_data=NAV_HOME_CALLBACK),
        ]
    )

    next_callback = f"{WORKER_LIST_PREFIX}0" if selected_driver_id is not None else NOOP_CALLBACK
    rows.append([
        InlineKeyboardButton(text="➡ далее", callback_data=next_callback),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows), actual_page, total_pages


def build_worker_keyboard(
    workers: Sequence[CrewWorker],
    *,
    page: int,
    selected_ids: Sequence[int],
) -> tuple[InlineKeyboardMarkup, int, int]:
    """Формирует клавиатуру выбора рабочих с пагинацией и «чипами» выбранных."""

    rows: list[list[InlineKeyboardButton]] = []
    selected_set = set(selected_ids)

    selected_workers = [worker for worker in workers if worker.worker_id in selected_set]
    if selected_workers:
        chip_row: list[InlineKeyboardButton] = []
        for worker in selected_workers:
            chip_row.append(
                InlineKeyboardButton(
                    text=f"✖ {worker.name}",
                    callback_data=f"{WORKER_TOGGLE_PREFIX}{worker.worker_id}",
                )
            )
            if len(chip_row) == 2:
                rows.append(chip_row)
                chip_row = []
        if chip_row:
            rows.append(chip_row)

    page_items, actual_page, total_pages = _paginate(workers, page)

    for worker in page_items:
        prefix = "✔" if worker.worker_id in selected_set else "👷"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{prefix} {worker.name}",
                    callback_data=f"{WORKER_TOGGLE_PREFIX}{worker.worker_id}",
                )
            ]
        )

    if total_pages > 1:
        prev_page = actual_page - 1 if actual_page > 0 else actual_page
        next_page = actual_page + 1 if actual_page + 1 < total_pages else actual_page
        rows.append(
            [
                InlineKeyboardButton(
                    text="‹ предыдущая",
                    callback_data=(
                        f"{WORKER_LIST_PREFIX}{prev_page}" if actual_page > 0 else NOOP_CALLBACK
                    ),
                ),
                InlineKeyboardButton(
                    text="следующая ›",
                    callback_data=(
                        f"{WORKER_LIST_PREFIX}{next_page}"
                        if actual_page + 1 < total_pages
                        else NOOP_CALLBACK
                    ),
                ),
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(text="➕ добавить рабочего", callback_data=WORKER_ADD_CALLBACK),
            InlineKeyboardButton(text="🧹 очистить список", callback_data=WORKER_CLEAR_CALLBACK),
        ]
    )

    confirm_callback = CONFIRM_CALLBACK if selected_workers else NOOP_CALLBACK
    rows.append(
        [
            InlineKeyboardButton(text="⬅ назад", callback_data=f"{DRIVER_LIST_PREFIX}0"),
            InlineKeyboardButton(text="🏠 в меню смены", callback_data=NAV_HOME_CALLBACK),
            InlineKeyboardButton(text="✅ подтвердить", callback_data=confirm_callback),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows), actual_page, total_pages
