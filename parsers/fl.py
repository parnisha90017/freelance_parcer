from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup


_PAGINATION_DELAY_SECONDS = 1


@dataclass(slots=True)
class FLParser:
    base_url: str = "https://www.fl.ru"
    categories: list[str] = field(default_factory=lambda: ["5", "4"])
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

    async def parse(self) -> list[dict[str, str]]:
        projects: list[dict[str, str]] = []
        seen_links: set[str] = set()

        for category in self.categories:
            for page in range(1, self.max_pages + 1):
                try:
                    response = requests.get(
                        self._page_url(category, page),
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
                except Exception:
                    continue
                if page < self.max_pages:
                    await asyncio.sleep(_PAGINATION_DELAY_SECONDS)

        return projects

    async def close(self) -> None:
        return None

    def _page_url(self, category: str, page: int) -> str:
        url = f"{self.base_url}/projects/?cat={category}"
        if page <= 1:
            return url
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        query["page"] = [str(page)]
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

    def _extract_projects(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        projects: list[dict[str, str]] = []
        for card in soup.select("[data-project-id], .b-post, .project-item, .b-post__content"):
            title_element = card.select_one("a, .b-post__title a, .project-item__title a")
            if title_element is None:
                continue

            title = title_element.get_text(" ", strip=True)
            link = self._absolute_link(title_element.get("href", ""))
            if not title or not link:
                continue

            description_element = card.select_one(
                ".b-post__txt, .project-item__description, .b-post__text, p"
            )
            price_element = card.select_one(
                ".b-post__price, .project-item__price, .price, [class*='price']"
            )

            projects.append(
                {
                    "title": title,
                    "description": description_element.get_text(" ", strip=True) if description_element else "",
                    "price": price_element.get_text(" ", strip=True) if price_element else "",
                    "link": link,
                }
            )

        return projects

    def _absolute_link(self, link: str) -> str:
        return urljoin(self.base_url, link.strip())
