from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from config import config


@dataclass(slots=True)
class TelegramSourcesManager:
    """CRUD по data/telegram_sources.json.

    Структура одной записи:
        {
            "username": "@chatname" | "private:<chat_id>" | invite-link,
            "chat_id": -1001234567890,         # основной идентификатор для Telethon
            "type": "chat" | "channel",
            "title": "Человекочитаемое название",
            "enabled": true,
            "is_private": false
        }
    """

    sources_path: Path

    async def load_sources(self) -> list[dict[str, Any]]:
        if not self.sources_path.exists():
            return []

        try:
            content = self.sources_path.read_text(encoding="utf-8")
            if not content.strip():
                return []
            data = json.loads(content)
        except (OSError, json.JSONDecodeError):
            logger.exception("Failed to load telegram_sources JSON")
            return []

        if not isinstance(data, list):
            return []

        normalized: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            chat_id = item.get("chat_id")
            if not isinstance(chat_id, int):
                continue
            normalized.append(
                {
                    "username": str(item.get("username", "")).strip(),
                    "chat_id": chat_id,
                    "type": str(item.get("type", "chat")),
                    "title": str(item.get("title", "")).strip() or "Без названия",
                    "enabled": bool(item.get("enabled", True)),
                    "is_private": bool(item.get("is_private", False)),
                }
            )
        return normalized

    async def save_sources(self, sources: list[dict[str, Any]]) -> None:
        self.sources_path.parent.mkdir(parents=True, exist_ok=True)
        self.sources_path.write_text(
            json.dumps(sources, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def get_enabled_sources(self) -> list[dict[str, Any]]:
        sources = await self.load_sources()
        return [s for s in sources if s.get("enabled")]

    async def find_by_chat_id(self, chat_id: int) -> dict[str, Any] | None:
        for source in await self.load_sources():
            if source["chat_id"] == chat_id:
                return source
        return None

    async def add_source(
        self,
        *,
        username: str,
        chat_id: int,
        type_: str,
        title: str,
        is_private: bool,
        enabled: bool = True,
    ) -> tuple[dict[str, Any], bool]:
        """Возвращает (запись, was_added). was_added=False если источник уже был в списке."""
        sources = await self.load_sources()
        for existing in sources:
            if existing["chat_id"] == chat_id:
                return existing, False

        record = {
            "username": username,
            "chat_id": chat_id,
            "type": type_,
            "title": title,
            "enabled": enabled,
            "is_private": is_private,
        }
        sources.append(record)
        await self.save_sources(sources)
        return record, True

    async def remove_source(self, chat_id: int) -> bool:
        sources = await self.load_sources()
        new_sources = [s for s in sources if s["chat_id"] != chat_id]
        if len(new_sources) == len(sources):
            return False
        await self.save_sources(new_sources)
        return True

    async def toggle_source(self, chat_id: int) -> bool | None:
        """Переключает enabled. Возвращает новое значение или None если источник не найден."""
        sources = await self.load_sources()
        for source in sources:
            if source["chat_id"] == chat_id:
                source["enabled"] = not bool(source["enabled"])
                await self.save_sources(sources)
                return source["enabled"]
        return None


telegram_sources_manager = TelegramSourcesManager(Path(config.TELEGRAM_SOURCES_JSON_PATH))
