from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass(slots=True)
class KeywordsManager:
    keywords_path: Path

    async def load_keywords(self) -> list[str]:
        if not self.keywords_path.exists():
            return []

        content = self.keywords_path.read_text(encoding="utf-8")
        if not content.strip():
            return []

        try:
            data: Any = json.loads(content)
        except json.JSONDecodeError:
            logger.exception("Failed to load keywords JSON")
            return []

        if not isinstance(data, list):
            return []

        return self._normalize_keywords(str(item) for item in data)

    async def save_keywords(self, keywords: list[str]) -> None:
        normalized_keywords = self._normalize_keywords(keywords)
        self.keywords_path.write_text(
            json.dumps(normalized_keywords, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def add_keyword(self, word: str) -> list[str]:
        keywords = await self.load_keywords()
        normalized_word = self._normalize_word(word)
        if normalized_word and normalized_word not in keywords:
            keywords.append(normalized_word)
            await self.save_keywords(keywords)
        return keywords

    async def remove_keyword(self, word: str) -> list[str]:
        keywords = await self.load_keywords()
        normalized_word = self._normalize_word(word)
        if normalized_word in keywords:
            keywords = [keyword for keyword in keywords if keyword != normalized_word]
            await self.save_keywords(keywords)
        return keywords

    def _normalize_keywords(self, keywords: Any) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for keyword in keywords:
            normalized_word = self._normalize_word(str(keyword))
            if not normalized_word or normalized_word in seen:
                continue
            seen.add(normalized_word)
            normalized.append(normalized_word)
        return normalized

    def _normalize_word(self, word: str) -> str:
        return word.strip().lower()
