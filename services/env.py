"""Утилиты для работы с переменными окружения."""

from __future__ import annotations

import os


def require_env(name: str) -> str:
    """Возвращает значение переменной окружения или выбрасывает понятную ошибку."""

    try:
        return os.environ[name]
    except KeyError as exc:  # noqa: PERF203 - нужно пробросить понятную ошибку
        raise RuntimeError(f"Не задана обязательная переменная окружения {name}") from exc
