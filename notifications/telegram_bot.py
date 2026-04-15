from __future__ import annotations

import asyncio
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from loguru import logger

from config import config
from services.ai_helper import AIHelper

_project_registry: dict[str, dict[str, Any]] = {}
_project_counter = 0
PRIORITY_PATH = Path(config.KEYWORDS_JSON_PATH).parent / "priority.json"
DEFAULT_PRIORITY = {
    "words": ["telegram", "бот", "парсер", "python", "автоматизация", "скрипт", "api"],
    "min_price_red": 10000,
    "min_price_yellow": 3000,
}
PLATFORM_LABELS = {
    "kwork.ru": "Kwork",
    "fl.ru": "FL.ru",
    "freelance.ru": "Freelance.ru",
    "weblancer.net": "Weblancer",
    "youdo.com": "YouDo",
    "pchel.net": "Pchel",
    "freelancehunt.com": "FreelanceHunt",
}




def get_project(project_id: str) -> dict[str, Any] | None:
    return _project_registry.get(project_id)


@dataclass(slots=True)
class TelegramNotifier:
    bot_token: str
    user_id: int
    ai_helper: AIHelper = field(
        default_factory=lambda: AIHelper(api_key=config.GROQ_API_KEY, model=config.GROQ_MODEL)
    )

    async def send_message(self, text: str) -> None:
        if not self.bot_token or not self.user_id:
            return

        bot = Bot(token=self.bot_token)
        try:
            await bot.send_message(chat_id=self.user_id, text=text)
        finally:
            await bot.session.close()

    async def send_project(self, project: dict[str, Any]) -> None:
        if not self.bot_token or not self.user_id:
            return

        enriched_project = project
        if not self._has_ai_fields(project):
            evaluation = await self.ai_helper.evaluate_project(project)
            enriched_project = {**project, **evaluation}

        global _project_counter
        project_id = str(_project_counter)
        _project_registry[project_id] = enriched_project
        _project_counter += 1

        if len(_project_registry) > 1000:
            oldest_key = next(iter(_project_registry))
            del _project_registry[oldest_key]

        _project_registry[project_id] = enriched_project

        title = self._clean_text(str(enriched_project.get("title", "")))
        price = str(enriched_project.get("price", ""))
        description = self._clean_text(str(enriched_project.get("description", "")))
        link = str(enriched_project.get("link", ""))
        score = int(enriched_project.get("score", 0))
        difficulty = str(enriched_project.get("difficulty", "средняя"))
        time_estimate = str(enriched_project.get("time_estimate", "1-2 дня"))
        explanation = self._clean_text(str(enriched_project.get("explanation", ""))).strip()
        short_description = description[:300]
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔗 Открыть заказ", url=link),
                    InlineKeyboardButton(text="✍️ Сгенерировать отклик", callback_data=f"respond_{project_id}"),
                ]
            ]
        )

        prefix = self._priority_prefix(enriched_project)
        platform = self._platform_label(link)
        title_line = f"{prefix} {platform} | Новый заказ!" if platform else f"{prefix} Новый заказ!"
        explanation_line = ""
        if explanation:
            explanation_line = f"💡 Почему подходит: {explanation}\n"

        message = (
            f"{title_line}\n\n"
            f"📋 {title}\n"
            f"💰 {price}\n"
            f"⭐ Подходит: {score}%\n"
            f"📊 Сложность: {difficulty}\n"
            f"⏱️ Время: {time_estimate}\n"
            f"{explanation_line}\n"
            f"{short_description}\n\n"
            "[🔗 Открыть заказ] [✍️ Сгенерировать отклик]"
        )

        for attempt in range(1, 4):
            bot = Bot(token=self.bot_token)
            try:
                await bot.send_message(chat_id=self.user_id, text=message, reply_markup=keyboard)
                break
            except TelegramRetryAfter as error:
                logger.warning("Telegram flood control: ждём {} секунд", error.retry_after)
                await asyncio.sleep(error.retry_after)
                continue
            except TelegramNetworkError as error:
                logger.error("Сетевая ошибка при отправке заказа, повтор отключён чтобы не дублировать сообщение: {}", error)
                break
            finally:
                await bot.session.close()

    def _has_ai_fields(self, project: dict[str, Any]) -> bool:
        return all(field in project for field in ("score", "difficulty", "time_estimate", "explanation"))

    async def _load_priority(self) -> dict[str, Any]:
        if not PRIORITY_PATH.exists():
            return DEFAULT_PRIORITY.copy()

        try:
            content = PRIORITY_PATH.read_text(encoding="utf-8")
            if not content.strip():
                return DEFAULT_PRIORITY.copy()
            data = json.loads(content)
        except (OSError, json.JSONDecodeError):
            logger.exception("Не удалось загрузить приоритеты")
            return DEFAULT_PRIORITY.copy()

        if not isinstance(data, dict):
            return DEFAULT_PRIORITY.copy()

        words = self._normalize_words(data.get("words", []))
        try:
            min_price_red = int(data.get("min_price_red", DEFAULT_PRIORITY["min_price_red"]))
        except (TypeError, ValueError):
            min_price_red = DEFAULT_PRIORITY["min_price_red"]
        try:
            min_price_yellow = int(data.get("min_price_yellow", DEFAULT_PRIORITY["min_price_yellow"]))
        except (TypeError, ValueError):
            min_price_yellow = DEFAULT_PRIORITY["min_price_yellow"]

        return {
            "words": words or DEFAULT_PRIORITY["words"].copy(),
            "min_price_red": min_price_red,
            "min_price_yellow": min_price_yellow,
        }

    def _priority_prefix(self, project: dict[str, Any]) -> str:
        priority = asyncio.run(self._load_priority()) if False else None
        return self._priority_prefix_sync(project)

    def _platform_label(self, link: str) -> str:
        parsed = urlparse(link.strip())
        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        for domain, label in PLATFORM_LABELS.items():
            if host == domain or host.endswith(f".{domain}"):
                return label
        return ""

    def _priority_prefix_sync(self, project: dict[str, Any]) -> str:
        try:
            priority = self._load_priority_sync()
        except Exception:
            priority = DEFAULT_PRIORITY.copy()

        haystack = f"{self._clean_text(str(project.get('title', '')))} {self._clean_text(str(project.get('description', '')))}".lower()
        words = set(re.findall(r"[\wа-яё]+", haystack, flags=re.IGNORECASE))
        if any(keyword in words for keyword in priority["words"]):
            return "🔴"

        price_value = self._extract_price(str(project.get("price", "")))
        if price_value is None:
            return "🟢"
        if price_value > priority["min_price_red"]:
            return "🔴"
        if priority["min_price_yellow"] <= price_value <= priority["min_price_red"]:
            return "🟡"
        return "🟢"

    def _load_priority_sync(self) -> dict[str, Any]:
        if not PRIORITY_PATH.exists():
            return DEFAULT_PRIORITY.copy()

        content = PRIORITY_PATH.read_text(encoding="utf-8")
        if not content.strip():
            return DEFAULT_PRIORITY.copy()

        data = json.loads(content)
        if not isinstance(data, dict):
            return DEFAULT_PRIORITY.copy()

        words = self._normalize_words(data.get("words", []))
        try:
            min_price_red = int(data.get("min_price_red", DEFAULT_PRIORITY["min_price_red"]))
        except (TypeError, ValueError):
            min_price_red = DEFAULT_PRIORITY["min_price_red"]
        try:
            min_price_yellow = int(data.get("min_price_yellow", DEFAULT_PRIORITY["min_price_yellow"]))
        except (TypeError, ValueError):
            min_price_yellow = DEFAULT_PRIORITY["min_price_yellow"]

        return {
            "words": words or DEFAULT_PRIORITY["words"].copy(),
            "min_price_red": min_price_red,
            "min_price_yellow": min_price_yellow,
        }

    def _normalize_words(self, words: Any) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        if not isinstance(words, list):
            return normalized

        for item in words:
            word = str(item).strip().lower()
            if not word or word in seen:
                continue
            seen.add(word)
            normalized.append(word)
        return normalized

    def _extract_price(self, text: str) -> int | None:
        if not text.strip():
            return None

        match = re.search(r"\d[\d\s]*", text.replace("\xa0", " "))
        if not match:
            return None

        digits = re.sub(r"\D", "", match.group(0))
        if not digits:
            return None

        return int(digits)

    def _clean_text(self, text: str) -> str:
        cleaned_text = html.unescape(text)
        cleaned_text = cleaned_text.replace("<br>", "\n")
        cleaned_text = cleaned_text.replace("<br/>", "\n")
        cleaned_text = cleaned_text.replace("<br />", "\n")
        cleaned_text = re.sub(r"<[^>]+>", "", cleaned_text)
        return cleaned_text
