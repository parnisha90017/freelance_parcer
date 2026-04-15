from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import config
from services import KeywordsManager

WORD_RE = re.compile(r"[\wа-яё]+", re.IGNORECASE)


@dataclass(slots=True)
class KeywordFilter:
    keywords: list[str] | None = None
    keywords_path: str | None = None

    async def filter(self, projects: list[Any]) -> list[Any]:
        keywords = self.keywords
        if keywords is None:
            manager = KeywordsManager(Path(self.keywords_path or config.KEYWORDS_JSON_PATH))
            keywords = await manager.load_keywords()

        normalized_keywords = [keyword.lower() for keyword in keywords if keyword.strip()]
        if not normalized_keywords:
            return []

        filtered_projects: list[Any] = []
        for project in projects:
            title = self._get_text(project, "title")
            description = self._get_text(project, "description")
            haystack = f"{title} {description}".lower()
            words = set(WORD_RE.findall(haystack))

            if any(self._matches_keyword(keyword, haystack, words) for keyword in normalized_keywords):
                filtered_projects.append(project)

        return filtered_projects

    def _matches_keyword(self, keyword: str, haystack: str, words: set[str]) -> bool:
        if " " in keyword:
            return keyword in haystack
        return keyword in words

    def _get_text(self, project: Any, field_name: str) -> str:
        if isinstance(project, dict):
            value = project.get(field_name, "")
        else:
            value = getattr(project, field_name, "")

        if value is None:
            return ""
        return str(value)
