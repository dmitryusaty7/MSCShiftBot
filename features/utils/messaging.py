"""Вспомогательные функции для управления сообщениями."""

from __future__ import annotations

import logging

from aiogram import types
from aiogram.exceptions import TelegramBadRequest

__all__ = ["safe_delete", "send_progress"]

logger = logging.getLogger(__name__)


async def safe_delete(message: types.Message | None) -> None:
    """Безопасно удаляет сообщение, игнорируя ограничения Telegram."""

    if message is None:
        return

    try:
        await message.delete()
    except TelegramBadRequest:
        logger.debug("Не удалось удалить сообщение (telegram ограничение).", exc_info=False)
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось удалить сообщение.")


async def send_progress(message: types.Message, text: str) -> types.Message | None:
    """Отправляет временное сообщение-прогресс. Может вернуть ``None``."""

    try:
        return await message.answer(text)
    except TelegramBadRequest:
        logger.debug("Не удалось отправить прогресс: ограничение Telegram.", exc_info=False)
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка при отправке прогресса.")
    return None
