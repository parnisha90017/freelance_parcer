from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import aiohttp


@dataclass(slots=True)
class YouDoParser:
    base_url: str = "https://youdo.com"
    api_url: str = "https://youdo.com/api/tasks/tasks/"
    referer: str = "https://youdo.com/tasks-all-opened-all"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    async def parse(self) -> list[dict[str, str]]:
        for attempt in range(3):
            try:
                data = await self._fetch_data()
                tasks = self._extract_tasks(data)
                if tasks:
                    return tasks
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(30)
                    continue
                return []

            if attempt < 2:
                await asyncio.sleep(30)
                continue
            return []

        return []

    async def close(self) -> None:
        return None

    async def _fetch_data(self) -> dict[str, Any]:
        headers = {
            "x-requested-with": "XMLHttpRequest",
            "x-featuresetid": "827",
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "referer": self.referer,
            "user-agent": self.user_agent,
        }

        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.post(self.api_url, json={}) as response:
                response.raise_for_status()
                return await response.json(content_type=None)

    def _extract_tasks(self, data: Any) -> list[dict[str, str]]:
        if not isinstance(data, dict):
            return []

        result_object = data.get("ResultObject")
        if not isinstance(result_object, dict):
            return []

        items = result_object.get("Items")
        if not isinstance(items, list):
            return []

        tasks: list[dict[str, str]] = []
        seen_links: set[str] = set()

        for item in items:
            if not isinstance(item, dict):
                continue

            title = str(item.get("Name") or "").strip()
            price = str(item.get("BudgetDescription") or "").strip()
            link = self._absolute_link(str(item.get("Url") or "").strip())
            description = self._build_description(item)

            if not title or not link or link in seen_links:
                continue

            seen_links.add(link)
            tasks.append(
                {
                    "title": title,
                    "price": price,
                    "description": description,
                    "link": link,
                    "source": "youdo",
                }
            )

        return tasks

    def _build_description(self, item: dict[str, Any]) -> str:
        parts: list[str] = []
        address = item.get("Address")
        date_time = item.get("DateTimeString")

        if address:
            address_text = str(address).strip()
            if address_text:
                parts.append(address_text)

        if date_time:
            date_text = str(date_time).strip()
            if date_text:
                parts.append(date_text)

        return " | ".join(parts)

    def _absolute_link(self, link: str) -> str:
        if not link:
            return ""
        return urljoin(self.base_url, link)
