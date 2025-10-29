"""Совместимый слой для старых импортов главной панели."""

from __future__ import annotations

from bot.handlers.dashboard import router, show_dashboard

__all__ = ["router", "show_menu"]


def show_menu(*args, **kwargs):  # noqa: ANN002, ANN003
    """Обёртка для обратной совместимости с прежним названием."""

    return show_dashboard(*args, **kwargs)
