from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class PriceFilter:
    min_price: int = 3000

    async def filter(self, projects: list[Any]) -> list[Any]:
        filtered_projects: list[Any] = []
        for project in projects:
            price_text = self._get_text(project, "price")
            price_value = self._extract_price(price_text)
            if price_value is None or price_value >= self.min_price:
                filtered_projects.append(project)
        return filtered_projects

    def _get_text(self, project: Any, field_name: str) -> str:
        if isinstance(project, dict):
            value = project.get(field_name, "")
        else:
            value = getattr(project, field_name, "")

        if value is None:
            return ""
        return str(value)

    def _extract_price(self, price_text: str) -> int | None:
        if not price_text.strip():
            return None

        match = re.search(r"\d[\d\s]*", price_text.replace("\xa0", " "))
        if not match:
            return None

        digits = re.sub(r"\D", "", match.group(0))
        if not digits:
            return None

        return int(digits)
