from __future__ import annotations

from pathlib import Path

from loguru import logger
from telethon import TelegramClient

from config import config

_client: TelegramClient | None = None


def get_client() -> TelegramClient:
    global _client
    if _client is None:
        if not config.TG_PARSER_API_ID or not config.TG_PARSER_API_HASH:
            raise RuntimeError("TG_PARSER_API_ID/TG_PARSER_API_HASH не заполнены в .env")
        try:
            api_id = int(config.TG_PARSER_API_ID)
        except ValueError as exc:
            raise RuntimeError("TG_PARSER_API_ID должен быть числом") from exc

        session_path = Path(config.TELETHON_SESSION_PATH)
        session_path.parent.mkdir(parents=True, exist_ok=True)
        _client = TelegramClient(str(session_path), api_id, config.TG_PARSER_API_HASH)
    return _client


async def start_client() -> bool:
    """Подключает singleton-клиент. Возвращает True если клиент авторизован и готов."""
    try:
        client = get_client()
    except RuntimeError as exc:
        logger.warning("Telethon-клиент не инициализирован: {}", exc)
        return False

    if not client.is_connected():
        await client.connect()

    if not await client.is_user_authorized():
        logger.warning(
            "Telethon-сессия не авторизована. Запусти `python auth_telethon.py` "
            "на сервере, чтобы пройти авторизацию по SMS-коду."
        )
        return False

    logger.info("Telethon-клиент подключён и авторизован")
    return True


async def stop_client() -> None:
    global _client
    if _client is not None and _client.is_connected():
        await _client.disconnect()
        logger.info("Telethon-клиент отключён")


async def is_ready() -> bool:
    if _client is None or not _client.is_connected():
        return False
    return await _client.is_user_authorized()
