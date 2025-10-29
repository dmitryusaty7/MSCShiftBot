"""Совместимость для импорта сценария материалов из нового модуля."""

from __future__ import annotations

from bot.handlers.materials import router, start_materials

__all__ = ["router", "start_materials"]
