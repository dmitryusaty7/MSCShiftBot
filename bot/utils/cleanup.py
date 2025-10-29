"""Утилиты для массовой очистки сообщений в рамках сценариев.

Функция ``cleanup_screen`` удаляет накопленные сообщения, а вспомогательные
помогают фиксировать идентификаторы сообщений, которые нужно зачистить после
завершения режима.
"""

from __future__ import annotations

import logging
from typing import Iterable, List

from aiogram import types
from aiogram.exceptions import TelegramBadRequest

from aiogram.fsm.context import FSMContext

__all__ = [
    "cleanup_screen",
    "cleanup_after_confirm",
    "remember_message",
    "remember_messages",
    "reset_history",
    "send_screen_message",
]

logger = logging.getLogger(__name__)

_HISTORY: dict[int, List[int]] = {}


def remember_message(chat_id: int, message_id: int) -> None:
    """Регистрирует сообщение для последующего удаления."""

    if message_id <= 0:
        return
    bucket = _HISTORY.setdefault(chat_id, [])
    bucket.append(message_id)


def remember_messages(chat_id: int, message_ids: Iterable[int]) -> None:
    """Добавляет несколько сообщений в очередь очистки."""

    bucket = _HISTORY.setdefault(chat_id, [])
    for message_id in message_ids:
        if message_id > 0:
            bucket.append(message_id)


def reset_history(chat_id: int) -> None:
    """Полностью очищает очередь сообщений для чата."""

    _HISTORY.pop(chat_id, None)


async def cleanup_screen(
    bot: types.Bot,
    chat_id: int,
    *,
    keep_start: bool = True,
) -> None:
    """Удаляет накопленные сообщения, оставляя при необходимости первое.

    В большинстве сценариев достаточно удалить все сообщения режима, чтобы в
    ленте остался только актуальный экран (меню смены, главная панель и т.п.).
    Если ``keep_start`` установлен, первое сообщение в очереди сохраняется
    (например, приветствие или /start).
    """

    history = _HISTORY.pop(chat_id, [])
    if not history:
        return

    unique_ids = sorted(set(history))
    if keep_start and unique_ids:
        unique_ids = unique_ids[1:]

    for message_id in reversed(unique_ids):
        try:
            await bot.delete_message(chat_id, message_id)
        except TelegramBadRequest:
            logger.debug("Сообщение %s уже удалено", message_id)
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось удалить сообщение %s", message_id, exc_info=True)


async def send_screen_message(
    message: types.Message,
    text: str,
    *,
    reply_markup=None,
    parse_mode: str | None = None,
    disable_web_page_preview: bool | None = None,
) -> types.Message:
    """Отправляет сообщение-экран и запоминает его для последующей очистки."""

    screen = await message.answer(
        text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
        disable_web_page_preview=disable_web_page_preview,
    )
    remember_message(message.chat.id, screen.message_id)
    return screen


async def cleanup_after_confirm(
    message: types.Message,
    state: FSMContext | None = None,
    *,
    keep_start: bool = False,
) -> None:
    """Очищает ленту чата после подтверждения закрытия смены."""

    bot = message.bot
    chat_id = message.chat.id

    if state is not None:
        try:
            data = await state.get_data()
        except Exception:  # noqa: BLE001
            logger.warning(
                "Не удалось получить данные состояния для очистки (chat_id=%s)",
                chat_id,
                exc_info=True,
            )
            data = {}
        menu_id = data.get("shift_menu_message_id") if isinstance(data, dict) else None
        if isinstance(menu_id, int) and menu_id > 0:
            try:
                await bot.delete_message(chat_id, menu_id)
            except TelegramBadRequest:
                logger.debug("Сообщение меню уже удалено: %s", menu_id)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Не удалось удалить сообщение меню %s", menu_id, exc_info=True
                )
            await state.update_data(shift_menu_message_id=None)

    await cleanup_screen(bot, chat_id, keep_start=keep_start)
