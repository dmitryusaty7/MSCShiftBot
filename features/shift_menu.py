"""Совместимость с новым модулем меню смены."""

from __future__ import annotations

from bot.handlers.shift_menu import (  # noqa: F401
    Mode,
    ShiftState,
    build_group_report,
    mark_mode_done,
    mark_shift_closed,
    render_shift_menu,
    reset_shift_session,
    router,
)

__all__ = [
    "router",
    "ShiftState",
    "Mode",
    "render_shift_menu",
    "mark_mode_done",
    "mark_shift_closed",
    "reset_shift_session",
    "build_group_report",
]
