from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from src import auth, expenses, main, materials, team
from src.dependencies import BOT_TOKEN


async def main_async() -> None:
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp.include_router(auth.router)
    dp.include_router(main.router)
    dp.include_router(team.router)
    dp.include_router(materials.router)
    dp.include_router(expenses.router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main_async())
