"""Одноразовая интерактивная авторизация Telethon-сессии для парсера.

Запускать ОДИН РАЗ на сервере (или там, где будет жить main.py):

    python3 auth_telethon.py

Потребует код подтверждения из Telegram (придёт в официальное приложение
или SMS) и, если включена 2FA, пароль. После успешной авторизации создаст
файл sessions/parser.session — после этого main.py работает автономно.

Если телефон не сменился, повторно запускать не нужно.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import config
from services.telegram_client import get_client


async def main() -> None:
    if not (config.TG_PARSER_API_ID and config.TG_PARSER_API_HASH and config.TG_PARSER_PHONE):
        print(
            "❌ Заполни TG_PARSER_API_ID, TG_PARSER_API_HASH, TG_PARSER_PHONE в .env. "
            "api_id/api_hash берётся на https://my.telegram.org → API development tools."
        )
        return

    client = get_client()
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"✅ Уже авторизован как {me.first_name} (@{me.username}, id={me.id})")
        await client.disconnect()
        return

    print(f"📨 Запрашиваю код подтверждения для {config.TG_PARSER_PHONE}...")
    await client.send_code_request(config.TG_PARSER_PHONE)
    code = input("Введи код из Telegram: ").strip()

    try:
        await client.sign_in(phone=config.TG_PARSER_PHONE, code=code)
    except Exception as exc:
        # Чаще всего SessionPasswordNeededError — включена 2FA
        if "password" in str(exc).lower() or exc.__class__.__name__ == "SessionPasswordNeededError":
            password = input("Введи пароль 2FA: ")
            await client.sign_in(password=password)
        else:
            print(f"❌ Ошибка авторизации: {exc}")
            await client.disconnect()
            return

    me = await client.get_me()
    print(f"✅ Готово. Авторизован как {me.first_name} (@{me.username}, id={me.id})")
    print(f"📁 Сессия сохранена в {config.TELETHON_SESSION_PATH}.session")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
