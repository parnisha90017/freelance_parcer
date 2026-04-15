from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup


@dataclass(slots=True)
class TelegramChannelsParser:
    channels_path: Path = Path("data/telegram_channels.json")
    base_url: str = "https://t.me"
    headers: dict[str, str] = field(
        default_factory=lambda: {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": "https://t.me/",
        }
    )

    async def parse(self) -> list[dict[str, str]]:
        channels = self._load_channels()
        if not channels:
            return []

        projects: list[dict[str, str]] = []
        seen_links: set[str] = set()

        for channel in channels:
            channel = channel.strip().lstrip("@").strip()
            if not channel:
                continue

            channel_projects = await self._parse_channel(channel)
            for project in channel_projects:
                link = project.get("link", "")
                if not link or link in seen_links:
                    continue
                seen_links.add(link)
                projects.append(project)

        return projects

    async def close(self) -> None:
        return None

    def _load_channels(self) -> list[str]:
        try:
            content = self.channels_path.read_text(encoding="utf-8")
        except OSError:
            return []

        try:
            data: Any = json.loads(content)
        except json.JSONDecodeError:
            return []

        if not isinstance(data, dict):
            return []

        channels = data.get("channels")
        if not isinstance(channels, list):
            return []

        return [str(channel).strip() for channel in channels if str(channel).strip()]

    async def _parse_channel(self, channel: str) -> list[dict[str, str]]:
        url = f"{self.base_url}/s/{channel}"

        for attempt in range(3):
            try:
                status, html = await self._fetch_html(url)
                if status != 200:
                    if attempt == 2:
                        return []
                    await asyncio.sleep(1)
                    continue

                projects = self._extract_projects(html, channel)
                if projects:
                    return projects
            except Exception:
                if attempt == 2:
                    return []
                await asyncio.sleep(1)
                continue

        return []

    async def _fetch_html(self, url: str) -> tuple[int, str]:
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout, headers=self.headers) as session:
            async with session.get(url) as response:
                html = await response.text()
                return response.status, html

    def _extract_projects(self, html: str, channel: str) -> list[dict[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        projects: list[dict[str, str]] = []
        seen_links: set[str] = set()

        for wrap in soup.select("div.tgme_widget_message_wrap"):
            bubble = wrap.select_one("div.tgme_widget_message_bubble") or wrap
            text = self._clean_text(bubble.get_text(" ", strip=True))
            if not text:
                continue

            parent = wrap.find_parent(attrs={"data-post": True}) or wrap
            data_post = str(parent.get("data-post", "")).strip()
            link = self._build_link(channel, data_post)
            if not link or link in seen_links:
                continue

            seen_links.add(link)
            projects.append(
                {
                    "title": text[:100],
                    "description": text[:500],
                    "link": link,
                    "price": "",
                    "source": "telegram",
                }
            )

        return projects

    def _build_link(self, channel: str, data_post: str) -> str:
        if not data_post or ":" not in data_post:
            return ""

        channel_name, post_id = data_post.split(":", 1)
        channel_name = channel_name.strip().lstrip("@").strip()
        post_id = post_id.strip()

        if not channel_name or not post_id:
            return ""

        if channel_name != channel:
            channel_name = channel

        return urljoin(self.base_url, f"/{channel_name}/{post_id}")

    def _clean_text(self, value: Any) -> str:
        return " ".join(str(value or "").split()).strip()


