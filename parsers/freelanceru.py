from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Tag


_PAGINATION_DELAY_SECONDS = 1


@dataclass(slots=True)
class FreelanceRuParser:
    base_url: str = "https://freelance.ru"
    projects_url: str = "https://freelance.ru/project/search"
    max_pages: int = 3
    headers: dict[str, str] = field(
        default_factory=lambda: {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
    )
    card_selectors: tuple[str, ...] = (
        "[data-project-id]",
        "article",
        "li",
        ".project-item",
        ".project-card",
        ".project",
        ".b-post",
        ".project-list__item",
    )
    link_selectors: tuple[str, ...] = (
        'a[href*="/projects/"]',
        'a[href*="/tender/view/"]',
    )
    category_keywords: tuple[str, ...] = (
        "ит и разработ",
        "веб-разработ",
        "программирован",
        "разработка",
        "frontend",
        "backend",
        "python",
        "javascript",
        "php",
        "wordpress",
        "bitrix",
        "web",
    )

    async def parse(self) -> list[dict[str, str]]:
        projects: list[dict[str, str]] = []
        seen_links: set[str] = set()

        try:
            for page in range(1, self.max_pages + 1):
                response = requests.get(
                    self._page_url(page),
                    headers=self.headers,
                    timeout=20,
                )
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                for item in self._extract_projects(soup):
                    link = item.get("link", "")
                    if not link or link in seen_links:
                        continue
                    seen_links.add(link)
                    projects.append(item)
                if page < self.max_pages:
                    await asyncio.sleep(_PAGINATION_DELAY_SECONDS)
        except Exception:
            return []

        return projects

    async def close(self) -> None:
        return None

    def _page_url(self, page: int) -> str:
        if page <= 1:
            return self.projects_url
        parsed = urlparse(self.projects_url)
        query = parse_qs(parsed.query)
        query["page"] = [str(page)]
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

    def _extract_projects(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        projects: list[dict[str, str]] = []

        for card in self._collect_cards(soup):
            link_element = self._find_primary_link(card)
            if link_element is None:
                continue

            title = link_element.get_text(" ", strip=True)
            link = self._absolute_link(link_element.get("href", ""))
            if not title or not link:
                continue

            if not self._matches_category(card, title, link):
                continue

            description = self._extract_description(card, title)
            price = self._extract_price(card)

            projects.append(
                {
                    "title": title,
                    "description": description,
                    "price": price,
                    "link": link,
                }
            )

        return projects

    def _collect_cards(self, soup: BeautifulSoup) -> list[Tag]:
        cards: list[Tag] = []
        seen_ids: set[int] = set()

        for selector in self.card_selectors:
            for card in soup.select(selector):
                if not isinstance(card, Tag):
                    continue
                card_id = id(card)
                if card_id in seen_ids:
                    continue
                seen_ids.add(card_id)
                cards.append(card)

        if cards:
            return cards

        fallback_cards: list[Tag] = []
        fallback_seen: set[int] = set()
        for anchor in soup.select('a[href*="/projects/"], a[href*="/tender/view/"]'):
            if not isinstance(anchor, Tag):
                continue
            card = anchor.find_parent(["article", "li", "div"]) or anchor.parent
            if not isinstance(card, Tag):
                continue
            card_id = id(card)
            if card_id in fallback_seen:
                continue
            fallback_seen.add(card_id)
            fallback_cards.append(card)

        return fallback_cards

    def _find_primary_link(self, card: Tag) -> Tag | None:
        for selector in self.link_selectors:
            for anchor in card.select(selector):
                if not isinstance(anchor, Tag):
                    continue
                if anchor.get_text(" ", strip=True):
                    return anchor
        return None

    def _matches_category(self, card: Any, title: str, link: str) -> bool:
        text = f"{title} {card.get_text(' ', strip=True) if hasattr(card, 'get_text') else ''} {link}".lower()
        if any(keyword in text for keyword in self.category_keywords):
            return True
        return "/projects/" in link or "/tender/view/" in link

    def _extract_description(self, card: Any, title: str) -> str:
        description_block = None
        if hasattr(card, "select_one"):
            description_block = card.select_one(
                ".description, .project-description, .project__description, .text, p, [class*='desc'], [class*='text']"
            )

        if description_block is not None:
            description = description_block.get_text(" ", strip=True)
            if description and description != title:
                return description

        if hasattr(card, "get_text"):
            text = card.get_text(" ", strip=True)
            if text and title in text:
                text = text.replace(title, "", 1).strip()
            return text

        return ""

    def _extract_price(self, card: Any) -> str:
        if hasattr(card, "select_one"):
            price_block = card.select_one(".price, .project-price, [class*='price'], [class*='budget']")
            if price_block is not None:
                price = price_block.get_text(" ", strip=True)
                if price:
                    return price

        if hasattr(card, "get_text"):
            text = card.get_text(" ", strip=True)
            if "договорная" in text.lower():
                return "Договорная"

        return ""

    def _absolute_link(self, link: str) -> str:
        return urljoin(self.base_url, link.strip())
