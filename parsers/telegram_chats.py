from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
from telethon.errors import (
    ChannelPrivateError,
    FloodWaitError,
    UsernameNotOccupiedError,
)

from services.telegram_client import get_client, is_ready
from services.telegram_sources_manager import telegram_sources_manager

ORDER_KEYWORDS = (
    "ищу", "нужен", "нужна", "нужно", "требуется", "требуются",
    "заказ", "разработать", "разработка", "сделать", "написать бот",
    "оплачу", "бюджет", "₽", "руб", "$",
)
EXECUTOR_MARKERS = (
    "ищу заказы", "ищу работу", "возьму заказ", "возьму проект",
    "выполню", "опыт", "портфолио", "резюме",
)
PRICE_REGEX = re.compile(
    r"(\d[\d\s]*)\s*(руб|₽|k|к|тыс|\$|usd)",
    flags=re.IGNORECASE,
)


def _build_message_link(entity: Any, message_id: int) -> str:
    """Стабильный URL сообщения. Для публичных — t.me/<username>/<id>,
    для приватных — t.me/c/<channel_id_short>/<id>."""
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"

    raw_id = getattr(entity, "id", 0)
    if raw_id < 0:
        raw_id = abs(raw_id)
    s = str(raw_id)
    if s.startswith("100"):
        s = s[3:]
    return f"https://t.me/c/{s}/{message_id}"


def _looks_like_order(text: str) -> bool:
    if len(text) <= 100:
        return False

    haystack = text.lower()
    if not any(kw in haystack for kw in ORDER_KEYWORDS):
        return False
    if any(marker in haystack for marker in EXECUTOR_MARKERS):
        return False
    return True


def _extract_price(text: str) -> str | None:
    match = PRICE_REGEX.search(text)
    if not match:
        return None
    return match.group(0).strip()


def _make_title(text: str) -> str:
    first_line = text.split("\n", 1)[0].strip()
    if first_line:
        return first_line[:80]
    return text.strip()[:80]


@dataclass(slots=True)
class TelegramChatsParser:
    lookback_minutes: int = 15
    sources_manager: Any = field(default=telegram_sources_manager)

    async def parse(self) -> list[dict[str, str]]:
        if not await is_ready():
            logger.warning("Telethon-клиент не готов, пропускаю Telegram-парсер")
            return []

        sources = await self.sources_manager.get_enabled_sources()
        if not sources:
            return []

        client = get_client()
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.lookback_minutes)

        results: list[dict[str, str]] = []
        for source in sources:
            try:
                source_results = await self._parse_source(client, source, cutoff)
                results.extend(source_results)
            except FloodWaitError as exc:
                logger.warning(
                    "Telegram FloodWait для {}: спим {} сек",
                    source.get("title"), exc.seconds,
                )
                await asyncio.sleep(exc.seconds)
            except (ChannelPrivateError, UsernameNotOccupiedError) as exc:
                logger.warning(
                    "Telegram-источник {} недоступен: {}. Пропускаю.",
                    source.get("title"), type(exc).__name__,
                )
            except Exception:
                logger.exception(
                    "Ошибка парсинга Telegram-источника {}",
                    source.get("title"),
                )

        logger.info("Telegram: всего сообщений-кандидатов {}", len(results))
        return results

    async def close(self) -> None:
        # Singleton-клиент живёт всё время работы main.py — здесь ничего не закрываем.
        return

    async def _parse_source(
        self,
        client: Any,
        source: dict[str, Any],
        cutoff: datetime,
    ) -> list[dict[str, str]]:
        chat_id = source["chat_id"]
        try:
            entity = await client.get_entity(chat_id)
        except (ChannelPrivateError, UsernameNotOccupiedError, ValueError) as exc:
            logger.warning(
                "Не удалось получить entity для {}: {}",
                source.get("title"), exc,
            )
            return []

        collected: list[dict[str, str]] = []
        async for message in client.iter_messages(entity, limit=200):
            if message.date is None or message.date < cutoff:
                break
            text = (message.message or "").strip()
            if not _looks_like_order(text):
                continue

            link = _build_message_link(entity, message.id)
            collected.append(
                {
                    "title": _make_title(text),
                    "description": text,
                    "price": _extract_price(text) or "",
                    "link": link,
                }
            )

        return collected
