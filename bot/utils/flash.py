"""Утилита для временных flash-сообщений с автоматическим удалением."""

from __future__ import annotations

import asyncio
from typing import Any

from aiogram import types
from aiogram.exceptions import TelegramBadRequest


async def flash_message(
    message: types.Message,
    text: str,
    *,
    ttl: float = 2.0,
    reply_markup: Any | None = None,
    disable_notification: bool | None = None,
) -> types.Message:
    """Отправляет временное сообщение и удаляет его по истечении TTL."""

    flash = await message.answer(
        text,
        reply_markup=reply_markup,
        disable_notification=disable_notification,
    )

    async def _cleanup(target: types.Message) -> None:
        await asyncio.sleep(ttl)
        try:
            await target.delete()
        except TelegramBadRequest:
            pass

    asyncio.create_task(_cleanup(flash))
    return flash
