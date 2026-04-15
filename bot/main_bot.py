from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from loguru import logger

from bot.handlers import router
from config import config


def create_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)
    return dispatcher


dispatcher = create_dispatcher()


async def run_bot() -> None:
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Polling started")
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


async def main() -> None:
    await run_bot()


if __name__ == "__main__":
    asyncio.run(main())
