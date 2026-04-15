from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from kworker import KworkAPI


@dataclass(slots=True)
class KworkParser:
    login: str
    password: str
    phone_last: str | None = None
    categories_ids: list[int | str] | None = None
    api: KworkAPI = field(init=False, repr=False)
    base_url: str = field(default="https://kwork.ru", init=False, repr=False)
    default_categories_ids: list[int | str] = field(
        default_factory=lambda: [11, 37, 38, 41, 79, 80],
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        self.api = KworkAPI(
            login=self.login,
            password=self.password,
            phone_last=self.phone_last,
        )
        if not self.categories_ids:
            self.categories_ids = self.default_categories_ids.copy()

    async def parse(self) -> list[dict[str, str]]:
        await self._authorize()
        raw_projects = await self._get_projects()
        projects = self._extract_projects(raw_projects)

        results: list[dict[str, str]] = []
        seen_links: set[str] = set()

        for project in projects:
            item = self._normalize_project(project)
            if item is None:
                continue
            if item["link"] in seen_links:
                continue
            seen_links.add(item["link"])
            results.append(item)

        return results

    async def _get_projects(self) -> Any:
        for name in (
            "get_projects",
            "projects",
            "fetch_projects",
            "list_projects",
            "get_project_list",
            "get_open_projects",
        ):
            method = getattr(self.api, name, None)
            if not callable(method):
                continue

            raw_projects = await self._call(method, self.categories_ids or self.default_categories_ids)
            projects = self._extract_projects(raw_projects)
            if projects:
                return raw_projects
            break

        for name in ("projects", "data", "result"):
            value = getattr(self.api, name, None)
            if value is not None and not callable(value):
                return value

        return []

    async def _call(self, method: Any, *args: Any, **kwargs: Any) -> Any:
        if inspect.iscoroutinefunction(method):
            return await method(*args, **kwargs)

        result = await asyncio.to_thread(method, *args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _get_all_category_ids(self) -> list[int | str]:
        method = getattr(self.api, "get_categories", None)
        if not callable(method):
            return []

        try:
            categories = await self._call(method)
        except Exception:
            print("Ошибка получения категорий, использую заданные")
            return []

        category_ids: list[int | str] = []

        if isinstance(categories, list):
            for category in categories:
                category_id = getattr(category, "id", None)
                if category_id is not None:
                    category_ids.append(category_id)

                subcategories = getattr(category, "subcategories", None) or []
                for subcategory in subcategories:
                    subcategory_id = getattr(subcategory, "id", None)
                    if subcategory_id is not None:
                        category_ids.append(subcategory_id)

        unique_ids: list[int | str] = []
        seen_ids: set[str] = set()
        for category_id in category_ids:
            key = str(category_id)
            if key in seen_ids:
                continue
            seen_ids.add(key)
            unique_ids.append(category_id)

        return unique_ids

    def _extract_projects(self, raw_projects: Any) -> list[dict[str, Any]]:
        if raw_projects is None:
            return []

        if isinstance(raw_projects, list):
            return list(raw_projects)

        if isinstance(raw_projects, dict):
            for key in ("projects", "items", "data", "result", "rows"):
                value = raw_projects.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
                if isinstance(value, dict):
                    nested = self._extract_projects(value)
                    if nested:
                        return nested

        return []

    async def _authorize(self) -> None:
        for name in ("auth", "authorize", "login", "sign_in"):
            method = getattr(self.api, name, None)
            if callable(method):
                await self._call(method)
                return

    async def close(self) -> None:
        await self.api.close()

    async def _get_projects(self) -> Any:
        for name in (
            "get_projects",
            "projects",
            "fetch_projects",
            "list_projects",
            "get_project_list",
            "get_open_projects",
        ):
            method = getattr(self.api, name, None)
            if not callable(method):
                continue

            raw_projects = await self._call(method, self.categories_ids or self.default_categories_ids)
            projects = self._extract_projects(raw_projects)
            if projects:
                return raw_projects
            break

        for name in ("projects", "data", "result"):
            value = getattr(self.api, name, None)
            if value is not None and not callable(value):
                return value

        return []

    async def _call(self, method: Any, *args: Any, **kwargs: Any) -> Any:
        if inspect.iscoroutinefunction(method):
            return await method(*args, **kwargs)

        result = await asyncio.to_thread(method, *args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    def _extract_projects(self, raw_projects: Any) -> list[dict[str, Any]]:
        if raw_projects is None:
            return []

        if isinstance(raw_projects, list):
            return list(raw_projects)

        if isinstance(raw_projects, dict):
            for key in ("projects", "items", "data", "result", "rows"):
                value = raw_projects.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
                if isinstance(value, dict):
                    nested = self._extract_projects(value)
                    if nested:
                        return nested

        return []

    def _normalize_project(self, project: Any) -> dict[str, str] | None:
        title = self._first_text(project, ("title", "name", "project_name", "subject"))
        link = self._first_text(project, ("link", "url", "href", "project_url", "site_url"))
        description = self._first_text(project, ("description", "text", "snippet", "body", "short_description"))
        price = self._first_text(project, ("price", "budget", "cost", "amount", "sum"))

        if not title:
            return None

        if not link:
            project_id = self._first_text(project, ("id",))
            if project_id:
                link = f"/projects/{project_id}"

        if not link:
            return None

        return {
            "title": title,
            "price": price,
            "description": description,
            "link": self._absolute_link(link),
        }

    def _first_text(self, data: Any, keys: tuple[str, ...]) -> str:
        for key in keys:
            if isinstance(data, dict):
                value = data.get(key)
            else:
                value = getattr(data, key, None)
            if value is None:
                continue
            text = self._stringify(value)
            if text:
                return text
        return ""

    def _stringify(self, value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, dict):
            for key in ("value", "text", "title", "name", "url", "href"):
                nested = value.get(key)
                if nested is not None:
                    text = self._stringify(nested)
                    if text:
                        return text
        if hasattr(value, "__dict__"):
            for key in ("value", "text", "title", "name", "url", "href"):
                nested = getattr(value, key, None)
                if nested is not None:
                    text = self._stringify(nested)
                    if text:
                        return text
        return ""

    def _absolute_link(self, link: str) -> str:
        return urljoin(self.base_url, link)
