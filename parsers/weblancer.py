from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Tag


_PAGINATION_DELAY_SECONDS = 1


@dataclass(slots=True)
class WeblancerParser:
    base_url: str = "https://www.weblancer.net"
    projects_url: str = "https://www.weblancer.net/projects/"
    max_pages: int = 3
    headers: dict[str, str] = field(
        default_factory=lambda: {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        }
    )
    programming_keywords: tuple[str, ...] = (
        "программирован",
        "разработ",
        "python",
        "django",
        "flask",
        "java",
        "javascript",
        "typescript",
        "php",
        "react",
        "vue",
        "frontend",
        "backend",
        "web",
        "it",
    )
    card_selectors: tuple[str, ...] = (
        "[data-project-id]",
        "article",
        ".project-item",
        ".project",
        ".project-card",
        ".b-post",
        "li",
    )
    link_selectors: tuple[str, ...] = (
        'a[href*="/projects/"]',
        'a[href*="/projects/" i]',
        "a[href]",
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
        seen_links: set[str] = set()

        for card in self._collect_cards(soup):
            link_element = self._find_primary_link(card)
            if link_element is None:
                continue

            title = self._extract_title(card, link_element)
            link = self._absolute_link(link_element.get("href", ""))
            if not title or not link or link in seen_links:
                continue

            if not self._matches_programming(card, title, link):
                continue

            description = self._extract_description(card, title)
            budget = self._extract_budget(card)
            deadline = self._extract_deadline(card)
            responses_count = self._extract_responses_count(card)

            seen_links.add(link)
            projects.append(
                {
                    "title": title,
                    "description": description,
                    "price": budget,
                    "budget": budget,
                    "deadline": deadline,
                    "responses_count": responses_count,
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
        for anchor in soup.select('a[href*="/projects/"]'):
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
                href = anchor.get("href", "").strip()
                text = anchor.get_text(" ", strip=True)
                if href and text:
                    return anchor
        return None

    def _extract_title(self, card: Tag, link_element: Tag) -> str:
        for selector in (
            "h1",
            "h2",
            "h3",
            ".title",
            ".project-title",
            ".project__title",
            ".b-post__title",
        ):
            title_element = card.select_one(selector)
            if title_element is not None:
                title = title_element.get_text(" ", strip=True)
                if title:
                    return title
        return link_element.get_text(" ", strip=True)

    def _matches_programming(self, card: Any, title: str, link: str) -> bool:
        card_text = card.get_text(" ", strip=True) if hasattr(card, "get_text") else ""
        haystack = f"{title} {card_text} {link}".lower()
        return any(keyword in haystack for keyword in self.programming_keywords)

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

    def _extract_budget(self, card: Any) -> str:
        if hasattr(card, "select_one"):
            for selector in (
                ".price",
                ".project-price",
                ".budget",
                ".project-budget",
                "[class*='budget']",
                "[class*='price']",
            ):
                budget_block = card.select_one(selector)
                if budget_block is not None:
                    budget = budget_block.get_text(" ", strip=True)
                    if budget:
                        return budget

        if hasattr(card, "get_text"):
            text = card.get_text(" ", strip=True)
            match = re.search(r"(?:от\s*)?\d[\d\s\xa0]*\s*(?:₽|руб\.?|р\.)", text, flags=re.IGNORECASE)
            if match:
                return match.group(0).strip()
            if "договорная" in text.lower():
                return "Договорная"

        return ""

    def _extract_deadline(self, card: Any) -> str:
        if hasattr(card, "select_one"):
            for selector in (
                ".deadline",
                ".project-deadline",
                ".term",
                ".time",
                "[class*='deadline']",
                "[class*='term']",
            ):
                deadline_block = card.select_one(selector)
                if deadline_block is not None:
                    deadline = deadline_block.get_text(" ", strip=True)
                    if deadline:
                        return deadline

        if hasattr(card, "get_text"):
            text = card.get_text(" ", strip=True)
            match = re.search(
                r"(?:срок(?:\s*выполнения)?|дедлайн|за\s*\d+\s*(?:дн\.?|дня|дней|час\.?|часа|часов))[:\s-]*([^.\n]{3,80})",
                text,
                flags=re.IGNORECASE,
            )
            if match:
                return match.group(1).strip()

        return ""

    def _extract_responses_count(self, card: Any) -> str:
        if hasattr(card, "select_one"):
            for selector in (
                ".responses",
                ".project-responses",
                ".b-post__responses",
                "[class*='response']",
                "[class*='reply']",
            ):
                responses_block = card.select_one(selector)
                if responses_block is not None:
                    responses = self._normalize_responses_text(responses_block.get_text(" ", strip=True))
                    if responses:
                        return responses

        if hasattr(card, "get_text"):
            text = card.get_text(" ", strip=True)
            match = re.search(r"(\d+)\s*(?:отклик[а-я]*|bid[а-я]*|response[а-я]*)", text, flags=re.IGNORECASE)
            if match:
                return match.group(1)

        return ""

    def _normalize_responses_text(self, text: str) -> str:
        match = re.search(r"(\d+)", text)
        if match:
            return match.group(1)
        return text

    def _absolute_link(self, link: str) -> str:
        return urljoin(self.base_url, link.strip())
