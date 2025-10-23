"""Утилиты для ограничения параллельных операций пользователей."""

from __future__ import annotations

import asyncio
from typing import Dict

__all__ = ["acquire_user_lock", "release_user_lock"]

# Глобальный реестр блокировок по Telegram ID
_USER_LOCKS: Dict[int, asyncio.Lock] = {}
_REGISTRY_LOCK = asyncio.Lock()


async def acquire_user_lock(user_id: int) -> asyncio.Lock | None:
    """Пытается захватить блокировку для пользователя.

    Возвращает объект ``asyncio.Lock`` при успешном захвате или ``None``,
    если операция уже выполняется и повторное обращение нужно отклонить.
    """

    async with _REGISTRY_LOCK:
        lock = _USER_LOCKS.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _USER_LOCKS[user_id] = lock

    if lock.locked():
        return None

    await lock.acquire()
    return lock


def release_user_lock(lock: asyncio.Lock) -> None:
    """Освобождает ранее захваченную блокировку пользователя."""

    if lock.locked():
        lock.release()
