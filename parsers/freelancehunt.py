from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin
import re

import aiohttp
from bs4 import BeautifulSoup
from loguru import logger


@dataclass(slots=True)
class FreelanceHuntParser:
    base_url: str = "https://freelancehunt.com"
    projects_url: str = "https://freelancehunt.com/projects"
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
        for attempt in range(3):
            try:
                html = await self._fetch_html()
                logger.debug("FreelanceHunt: получен HTML, длина {} символов", len(html))
                projects = self._extract_projects(html)
                logger.debug("FreelanceHunt: итоговое количество проектов {}", len(projects))
                if projects:
                    return projects
            except Exception:
                if attempt == 2:
                    return []
        return []

    async def close(self) -> None:
        return None

    async def _fetch_html(self) -> str:
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout, headers=self.headers) as session:
            async with session.get(self.projects_url) as response:
                logger.debug("FreelanceHunt: HTTP status {}", response.status)
                response.raise_for_status()
                html = await response.text()
                logger.debug("FreelanceHunt: первые 500 символов HTML: {}", html[:500])
                return html

    def _extract_projects(self, html: str) -> list[dict[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        cards = self._collect_cards(soup)
        logger.debug("FreelanceHunt: найдено карточек после сбора {}", len(cards))

        projects: list[dict[str, str]] = []
        seen_links: set[str] = set()
        valid_cards = 0

        for card in cards:
            item = self._extract_card(card)
            link = item.get("link", "")
            title = item.get("title", "")
            if not link or not title:
                continue
            valid_cards += 1
            logger.debug("FreelanceHunt: карточка прошла базовую проверку: {}", link)
            if not self._is_real_project_link(link):
                continue
            if link in seen_links:
                continue
            seen_links.add(link)
            projects.append(item)

        logger.debug("FreelanceHunt: валидных карточек {}, после дедупликации {}", valid_cards, len(projects))
        return projects

    def _collect_cards(self, soup: BeautifulSoup) -> list[Any]:
        selectors = [
            "article",
            "div[class*='project']",
            "tr",
            "li",
        ]

        cards: list[Any] = []
        seen: set[str] = set()

        for selector in selectors:
            selector_cards = soup.select(selector)
            logger.debug("FreelanceHunt: селектор {} нашёл {} элементов", selector, len(selector_cards))
            for card in selector_cards:
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
        if not link or not title:
            return False
        if "/projects/skill/" in link:
            return False
        if "/project/add" in link:
            return False
        return True

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
            "source": "freelancehunt",
        }

    def _extract_title(self, card: Any) -> str:
        title_selectors = [
            "h1",
            "h2",
            "h3",
            "h4",
            ".project-item__title",
            ".project-item__name",
            ".project-title",
            "a[href*='/project/']",
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
            ".project-price",
            ".budget",
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
            ".project-description",
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
            if not href:
                continue
            if "/projects/skill/" in href:
                continue
            if "/project/add" in href:
                continue
            if not self._is_real_project_link(href):
                continue
            return urljoin(self.base_url, href)
        return ""

    def _is_real_project_link(self, link: str) -> bool:
        href = link.strip()
        if not href:
            return False
        if "/projects/skill/" in href:
            return False
        if "/project/add" in href:
            return False

        path = href if href.startswith("/") else urljoin(self.base_url, href).replace(self.base_url, "")
        path = path.split("?", 1)[0].rstrip("/")
        match = re.fullmatch(r"/project/.+?/\d+\.html", path)
        return bool(match)

    def _card_key(self, card: Any) -> str:
        link = self._extract_link(card)
        if link:
            return link
        title = self._extract_title(card)
        description = self._extract_description(card)
        return f"{title}::{description}"

    def _clean_text(self, value: Any) -> str:
        return " ".join(str(value or "").split()).strip()
