from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from config import config
from database.db import init_db, is_duplicate, save_project
from filters import KeywordFilter, PriceFilter
from notifications import TelegramNotifier
from parsers.fl import FLParser
from parsers.kwork import KworkParser
from services import KeywordsManager
from services.settings_manager import settings_manager


async def test_kwork() -> int:
    parser = KworkParser(
        login=config.KWORK_LOGIN,
        password=config.KWORK_PASSWORD,
        phone_last=config.KWORK_PHONE_LAST,
        categories_ids=config.KWORK_CATEGORIES_IDS,
    )
    try:
        projects = await parser.parse()
        print(f"✅ Kwork: найдено {len(projects)} проектов")
        return len(projects)
    finally:
        await parser.close()


async def test_fl() -> int:
    parser = FLParser(categories=config.FL_CATEGORIES)
    try:
        projects = await parser.parse()
        print(f"✅ FL.ru: найдено {len(projects)} проектов")
        return len(projects)
    finally:
        await parser.close()


async def test_keywords() -> int:
    keywords_manager = KeywordsManager(Path(config.KEYWORDS_JSON_PATH))
    keywords = await keywords_manager.load_keywords()
    keyword_filter = KeywordFilter(keywords_path=config.KEYWORDS_JSON_PATH)
    sample_projects = [
        {
            "title": "Telegram bot for business",
            "description": "Need a Python bot for Telegram",
            "price": "10000",
            "link": "https://example.com/telegram-bot",
        },
        {
            "title": "Нужен повар",
            "description": "Ищу человека на подработу",
            "price": "2000",
            "link": "https://example.com/cook",
        },
        {
            "title": "Logo design",
            "description": "Create a logo",
            "price": "2000",
            "link": "https://example.com/logo",
        },
    ]
    filtered_projects = await keyword_filter.filter(sample_projects)
    print(f"✅ Фильтр: {len(filtered_projects)} релевантных")
    return len(keywords)


async def test_settings() -> dict[str, bool | int]:
    settings = await settings_manager.load_settings()
    print("✅ Настройки: загружены")
    return settings


async def test_price_filter() -> int:
    price_filter = PriceFilter(min_price=int(config.MIN_PRICE))
    sample_projects = [
        {
            "title": "High budget bot",
            "description": "Python Telegram bot",
            "price": "5000 ₽",
            "link": "https://example.com/high-budget",
        },
        {
            "title": "Low budget bot",
            "description": "Python Telegram bot",
            "price": "1000 ₽",
            "link": "https://example.com/low-budget",
        },
        {
            "title": "No price bot",
            "description": "Python Telegram bot",
            "price": "",
            "link": "https://example.com/no-price",
        },
    ]
    filtered_projects = await price_filter.filter(sample_projects)
    print(f"✅ Цена: {len(filtered_projects)} релевантных")
    return len(filtered_projects)


async def test_database() -> None:
    await init_db()
    test_link = f"https://example.com/test/{uuid4().hex}"
    if await is_duplicate(test_link):
        raise RuntimeError("Unexpected duplicate before save")
    await save_project(test_link)
    if not await is_duplicate(test_link):
        raise RuntimeError("Project was not saved")
    print("✅ БД: работает")


async def test_telegram() -> None:
    notifier = TelegramNotifier(
        bot_token=config.TELEGRAM_BOT_TOKEN,
        user_id=config.TELEGRAM_USER_ID,
    )
    await notifier.send_project(
        {
            "title": "Тестовое уведомление",
            "description": "Проверка отправки сообщения из test.py",
            "price": "0",
            "link": "https://example.com/test-notification",
        }
    )
    print("✅ Telegram: отправлено")


async def main() -> None:
    await test_kwork()
    await test_fl()
    await test_keywords()
    await test_settings()
    await test_price_filter()
    await test_database()
    await test_telegram()


if __name__ == "__main__":
    asyncio.run(main())


