from __future__ import annotations

"""Точка входа бота MSC Shift.

На текущем этапе подключается только FSM регистрации. При расширении функционала
сюда будут добавляться новые роутеры.
"""

from pathlib import Path

from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from features.main_menu import router as main_menu_router
from features.registration import router as registration_router
from services.env import require_env


def main() -> None:
    """Запускает бота."""

    project_root = Path(__file__).resolve().parent
    load_dotenv()
    load_dotenv(project_root / ".env")
    bot = Bot(token=require_env("BOT_TOKEN"))
    dispatcher = Dispatcher()
    dispatcher.include_router(registration_router)
    dispatcher.include_router(main_menu_router)
    dispatcher.run_polling(bot)


if __name__ == "__main__":
    main()
