from __future__ import annotations

"""Точка входа бота MSC Shift.

На текущем этапе подключается только FSM регистрации. При расширении функционала
сюда будут добавляться новые роутеры.
"""

import os

from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from features.registration import router as registration_router


def main() -> None:
    """Запускает бота."""

    load_dotenv()
    bot = Bot(token=os.environ["BOT_TOKEN"])
    dispatcher = Dispatcher()
    dispatcher.include_router(registration_router)
    dispatcher.run_polling(bot)


if __name__ == "__main__":
    main()
