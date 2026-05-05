"""Smoke-тест парсера Telegram-чатов.

Перед запуском убедись что:
- В .env заполнены TG_PARSER_API_ID, TG_PARSER_API_HASH, TG_PARSER_PHONE
- Прошёл авторизацию: `python3 auth_telethon.py`
- В data/telegram_sources.json добавлен хотя бы один источник
  (через бот: 📡 Telegram-источники → ➕ Добавить)

Запуск:
    python -m test_env.test_telegram_chats
"""

from __future__ import annotations

import asyncio

from parsers.telegram_chats import TelegramChatsParser
from services.telegram_client import start_client, stop_client


async def test_telegram_chats() -> int:
    started = await start_client()
    if not started:
        print("❌ Telethon не запущен. Запусти `python auth_telethon.py` или проверь .env")
        return 0

    parser = TelegramChatsParser(lookback_minutes=60)  # на тесте берём шире окно
    try:
        projects = await parser.parse()
        print(f"✅ Telegram-чаты: найдено {len(projects)} кандидатов на заказы")
        for project in projects[:3]:
            print("---")
            print(f"📌 {project['title']}")
            print(f"💰 {project['price'] or '—'}")
            print(f"🔗 {project['link']}")
            print(f"📝 {project['description'][:200]}{'...' if len(project['description']) > 200 else ''}")
        return len(projects)
    finally:
        await parser.close()
        await stop_client()


if __name__ == "__main__":
    asyncio.run(test_telegram_chats())
