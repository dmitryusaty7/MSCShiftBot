"""Утилиты для работы с переменными окружения."""

from __future__ import annotations

import os


def _normalize_bool(value: str | None, default: bool) -> bool:
    """Преобразует строку окружения в булево значение."""

    if value is None:
        return default
    candidate = value.strip().lower()
    if candidate in {"1", "true", "yes", "y", "on"}:
        return True
    if candidate in {"0", "false", "no", "n", "off"}:
        return False
    return default


def require_env(name: str) -> str:
    """Возвращает значение переменной окружения или выбрасывает понятную ошибку."""

    try:
        return os.environ[name]
    except KeyError as exc:  # noqa: PERF203 - нужно пробросить понятную ошибку
        raise RuntimeError(f"Не задана обязательная переменная окружения {name}") from exc


def get_group_chat_id() -> int | None:
    """Возвращает ID группового чата для сводок или ``None`` если не указан."""

    raw_value = os.getenv("GROUP_CHAT_ID", "").strip()
    if not raw_value:
        return None
    try:
        return int(raw_value)
    except ValueError as exc:  # noqa: PERF203 - ошибка конфигурации должна быть явной
        raise RuntimeError(
            "GROUP_CHAT_ID должен содержать целое число (например, -1001234567890)"
        ) from exc


def group_notifications_enabled() -> bool:
    """Показывает, включена ли рассылка уведомлений в группу."""

    return _normalize_bool(os.getenv("GROUP_NOTIFICATIONS"), True)
