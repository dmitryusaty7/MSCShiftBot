"""Утилиты для временных flash-сообщений с автоматическим удалением."""

from __future__ import annotations

import asyncio
from typing import Any

from aiogram import types
from aiogram.exceptions import TelegramBadRequest


async def flash_message(
    target: types.Message | types.CallbackQuery,
    text: str,
    *,
    ttl: float = 2.0,
    reply_markup: Any | None = None,
    disable_notification: bool | None = None,
) -> types.Message:
    """Отправляет временное сообщение и удаляет его по истечении TTL."""

    bounded_ttl = max(0.1, min(ttl, 5.0))

    if isinstance(target, types.CallbackQuery):
        message = target.message
        if message is None:
            raise RuntimeError("Невозможно отправить flash: отсутствует message у callback.")
    else:
        message = target

    flash = await message.answer(
        text,
        reply_markup=reply_markup,
        disable_notification=disable_notification,
    )

    async def _cleanup(target: types.Message) -> None:
        await asyncio.sleep(bounded_ttl)
        try:
            await target.delete()
        except TelegramBadRequest:
            pass

    asyncio.create_task(_cleanup(flash))
    return flash


_MODE_HINTS = {
    "crew": "Открываю раздел «Бригада»…",
    "materials": "Открываю раздел «Материалы»…",
    "expenses": "Открываю раздел «Расходы»…",
}


async def start_mode_flash(
    target: types.Message | types.CallbackQuery,
    mode: str,
    *,
    ttl: float = 1.5,
) -> types.Message:
    """Отправляет стандартизированное flash-сообщение о запуске раздела."""

    hint = _MODE_HINTS.get(mode.lower(), f"Открываю раздел «{mode.title()}»…")
    return await flash_message(target, hint, ttl=ttl)
