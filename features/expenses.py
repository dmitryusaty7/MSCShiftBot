"""Совместимость для импорта сценария расходов из нового модуля."""

from __future__ import annotations

from bot.handlers.expenses import router, start_expenses

__all__ = ["router", "start_expenses"]
