from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup


@dataclass(slots=True)
class PchelParser:
    base_url: str = "https://pchel.net"
    jobs_url: str = "https://pchel.net/jobs/"
    max_pages: int = 3
    headers: dict[str, str] = field(
        default_factory=lambda: {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )

    async def parse(self) -> list[dict[str, str]]:
        projects: list[dict[str, str]] = []
        seen_links: set[str] = set()

        for page in range(1, self.max_pages + 1):
            for attempt in range(3):
                try:
                    html = await self._fetch_html(page)
                    for item in self._extract_projects(html):
                        link = item.get("link", "")
                        if not link or link in seen_links:
                            continue
                        seen_links.add(link)
                        projects.append(item)
                    break
                except Exception:
                    if attempt == 2:
                        break
            if page < self.max_pages:
                await asyncio.sleep(1)

        return projects

    async def close(self) -> None:
        return None

    async def _fetch_html(self, page: int) -> str:
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout, headers=self.headers) as session:
            async with session.get(self._page_url(page)) as response:
                response.raise_for_status()
                return await response.text()

    def _page_url(self, page: int) -> str:
        if page <= 1:
            return self.jobs_url
        return urljoin(self.base_url, f"/jobs/page-{page}/")

    def _extract_projects(self, html: str) -> list[dict[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        cards = self._collect_cards(soup)

        projects: list[dict[str, str]] = []
        seen_links: set[str] = set()

        for card in cards:
            item = self._extract_card(card)
            link = item.get("link", "")
            title = item.get("title", "")
            price = item.get("price", "")
            description = item.get("description", "")
            if not link or not title:
                continue
            if not price and not description:
                continue
            if not self._is_real_project_link(link):
                continue
            if link in seen_links:
                continue
            seen_links.add(link)
            projects.append(item)

        return projects

    def _is_real_project_link(self, link: str) -> bool:
        parsed = urlparse(link)
        path = parsed.path.rstrip("/")
        if not path.startswith("/jobs/"):
            return False

        segments = [segment for segment in path.split("/") if segment]
        if len(segments) < 3:
            return False

        last_segment = segments[-1]
        if not re.search(r"\d", last_segment):
            return False

        return True

    def _collect_cards(self, soup: BeautifulSoup) -> list[Any]:
        selectors = [
            "div.project-item",
            "div[class*='project-item']",
            "div[class*='job-item']",
            "div[class*='project-card']",
            "div[class*='project']",
        ]

        cards: list[Any] = []
        seen: set[str] = set()

        for selector in selectors:
            for card in soup.select(selector):
                key = self._card_key(card)
                if not key or key in seen:
                    continue
                if not self._is_project_card(card):
                    continue
                seen.add(key)
                cards.append(card)

        return cards

    def _is_project_card(self, card: Any) -> bool:
        link = self._extract_link(card)
        title = self._extract_title(card)
        return bool(link and title)

    def _extract_card(self, card: Any) -> dict[str, str]:
        title = self._extract_title(card)
        price = self._extract_price(card)
        description = self._extract_description(card)
        link = self._extract_link(card)

        return {
            "title": title,
            "price": price,
            "description": description,
            "link": link,
            "source": "pchel",
        }

    def _extract_title(self, card: Any) -> str:
        title_selectors = [
            "h1",
            "h2",
            "h3",
            "h4",
            ".project-item__title",
            ".project-item__name",
            ".job-item__title",
            "a[href*='/jobs/']",
        ]
        for selector in title_selectors:
            element = card.select_one(selector)
            if element is None:
                continue
            text = self._clean_text(element.get_text(" ", strip=True))
            if text:
                return text
        return ""

    def _extract_price(self, card: Any) -> str:
        price_selectors = [
            ".project-item__price",
            ".job-item__price",
            "[class*='price']",
            "[class*='budget']",
            "[class*='cost']",
        ]
        for selector in price_selectors:
            element = card.select_one(selector)
            if element is None:
                continue
            text = self._clean_text(element.get_text(" ", strip=True))
            if text:
                return text
        return ""

    def _extract_description(self, card: Any) -> str:
        description_selectors = [
            ".project-item__description",
            ".job-item__description",
            ".project-item__text",
            "[class*='description']",
            "p",
        ]
        for selector in description_selectors:
            element = card.select_one(selector)
            if element is None:
                continue
            text = self._clean_text(element.get_text(" ", strip=True))
            if text:
                return text
        return ""

    def _extract_link(self, card: Any) -> str:
        for link_tag in card.select("a[href]"):
            href = self._clean_text(link_tag.get("href", ""))
            if "/jobs/" not in href:
                continue
            return urljoin(self.base_url, href)
        return ""

    def _card_key(self, card: Any) -> str:
        link = self._extract_link(card)
        if link:
            return link
        title = self._extract_title(card)
        description = self._extract_description(card)
        return f"{title}::{description}"

    def _clean_text(self, value: Any) -> str:
        return " ".join(str(value or "").split()).strip()
