from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from loguru import logger

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from aiogram.exceptions import TelegramNetworkError

from bot.main_bot import dispatcher, run_bot
from database.db import init_db
from services.scheduler import parser_scheduler

LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"


def setup_logging() -> None:
    logs_dir = Path(__file__).resolve().parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, format=LOG_FORMAT)
    logger.add(logs_dir / "parser.log", format=LOG_FORMAT, encoding="utf-8")
    logger.add("data/bot.log", rotation="1 MB", retention="3 days")


async def run_app() -> None:
    await init_db()
    await parser_scheduler.start()
    logger.info("Бот запущен, автопарсинг выключен по умолчанию")

    try:
        while True:
            try:
                await run_bot()
                break
            except TelegramNetworkError as error:
                logger.warning("Не удалось подключить бота: {}", error)
                await asyncio.sleep(10)
    finally:
        await parser_scheduler.shutdown()


def main() -> None:
    setup_logging()
    asyncio.run(run_app())


if __name__ == "__main__":
    main()
