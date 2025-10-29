from __future__ import annotations

"""Точка входа бота MSC Shift.

На текущем этапе подключается только FSM регистрации. При расширении функционала
сюда будут добавляться новые роутеры.
"""

from pathlib import Path

from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from bot.handlers.dashboard import router as dashboard_router
from bot.handlers.expenses import router as expenses_router
from bot.handlers.materials import router as materials_router
from bot.handlers.registration import router as registration_router
from bot.handlers.crew import router as crew_router
from bot.handlers.shift_close import router as shift_close_router
from bot.handlers.shift_menu import router as shift_menu_router
from services.env import require_env


def main() -> None:
    """Запускает бота."""

    project_root = Path(__file__).resolve().parent
    load_dotenv()
    load_dotenv(project_root / ".env")
    bot = Bot(token=require_env("BOT_TOKEN"))
    dispatcher = Dispatcher()
    dispatcher.include_router(registration_router)
    dispatcher.include_router(dashboard_router)
    dispatcher.include_router(expenses_router)
    dispatcher.include_router(materials_router)
    dispatcher.include_router(crew_router)
    dispatcher.include_router(shift_menu_router)
    dispatcher.include_router(shift_close_router)
    dispatcher.run_polling(bot)


if __name__ == "__main__":
    main()
