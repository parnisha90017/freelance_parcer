from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from loguru import logger

from config import config


_EVALUATE_SYSTEM_PROMPT = (
    "Ты оцениваешь заказы для фрилансера.\n"
    "Контекст: я Python-разработчик, специализируюсь на Telegram-ботах, парсерах, автоматизации, AI-интеграциях.\n"
    "Оцени заказ по критериям: релевантность (telegram/python/parser/bot), сложность, бюджет, срочность.\n"
    "Высокий балл получают заказы, которые хорошо подходят под эту специализацию, имеют адекватный бюджет и реалистичные сроки.\n"
    "Низкий балл получают нерелевантные заказы, слишком срочные при низком бюджете или задачи вне специализации.\n\n"
    "Верни строго JSON:\n"
    "{\n"
    '  "score": число от 0 до 100,\n'
    '  "difficulty": "лёгкая/средняя/сложная",\n'
    '  "time_estimate": "1-2 часа / 3-5 часов / 1-2 дня",\n'
    '  "explanation": "1-2 предложения, почему заказ подходит или не подходит"\n'
    "}"
)

_GENERATE_SYSTEM_PROMPT = (
    "Напиши короткий отклик на заказ от имени фрилансера.\n"
    "Навыки: Telegram боты, парсеры, автоматизация, Python.\n"
    "Стиль: профессиональный, дружелюбный, без воды.\n"
    "Длина: 3-5 предложений.\n"
    "Упомяни опыт и сроки."
)


@dataclass(slots=True)
class AIHelper:
    api_key: str
    model: str
    consecutive_429_count: int = field(init=False, repr=False)
    skip_ai_for_cycle: bool = field(init=False, repr=False)
    last_status: str = field(init=False, repr=False)

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self.consecutive_429_count = 0
        self.skip_ai_for_cycle = False
        self.last_status = "idle"

    def reset_cycle_state(self) -> None:
        self.consecutive_429_count = 0
        self.skip_ai_for_cycle = False
        self.last_status = "idle"

    async def evaluate_project(self, project: dict[str, Any]) -> dict[str, Any]:
        cached_result = self._project_evaluation(project)
        if cached_result is not None:
            self.last_status = "cached"
            return cached_result

        if self.skip_ai_for_cycle:
            self.last_status = "skipped"
            return self._fallback_result()

        raw_text = await self._chat_completion(
            system_prompt=_EVALUATE_SYSTEM_PROMPT,
            user_prompt=self._project_payload(project),
            response_format={"type": "json_object"},
            max_tokens=300,
            temperature=0.2,
        )
        if not raw_text:
            return self._fallback_result()

        parsed = self._extract_json(raw_text)
        if not parsed:
            logger.warning("AIHelper: не удалось разобрать JSON оценки")
            self.last_status = "parse_error"
            return self._fallback_result()

        self.last_status = "ok"
        return {
            "score": self._normalize_score(parsed.get("score")),
            "difficulty": self._normalize_text(parsed.get("difficulty"), default="средняя"),
            "time_estimate": self._normalize_text(parsed.get("time_estimate"), default="1-2 дня"),
            "explanation": self._normalize_text(parsed.get("explanation"), default="ИИ временно недоступен"),
        }

    async def generate_response(self, project: dict[str, Any]) -> str:
        default_response = (
            "Здравствуйте! Меня заинтересовал ваш заказ. "
            "У меня есть опыт в Telegram-ботах, парсерах и Python-автоматизации, "
            "готов быстро оценить задачу и приступить к работе."
        )
        raw_text = await self._chat_completion(
            system_prompt=_GENERATE_SYSTEM_PROMPT,
            user_prompt=self._project_payload(project),
            max_tokens=350,
            temperature=0.5,
        )
        response_text = (raw_text or "").strip()
        return response_text or default_response

    async def _chat_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        response_format: dict[str, str] | None = None,
    ) -> str:
        if not self.api_key:
            self.last_status = "disabled"
            return ""

        if self.skip_ai_for_cycle:
            self.last_status = "skipped"
            return ""

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": config.OPENROUTER_HTTP_REFERER,
            "X-Title": config.OPENROUTER_APP_TITLE,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )
                if response.status_code == 429:
                    self.consecutive_429_count += 1
                    self.last_status = "rate_limited"
                    self.skip_ai_for_cycle = True
                    logger.warning("AIHelper: OpenRouter 429, skipping AI evaluation")
                    return ""

                response.raise_for_status()
                data = response.json()
                self.consecutive_429_count = 0
                self.last_status = "ok"
                return str(data["choices"][0]["message"]["content"])

        except Exception as exc:
            self.consecutive_429_count = 0
            self.last_status = "error"
            logger.warning("AIHelper: ошибка OpenRouter API: {}", exc)
            return ""
        finally:
            await asyncio.sleep(2)

    def _fallback_result(self) -> dict[str, Any]:
        self.last_status = self.last_status or "error"
        unavailable_text = "ИИ временно недоступен"
        return {
            "score": 0,
            "difficulty": unavailable_text,
            "time_estimate": unavailable_text,
            "explanation": unavailable_text,
        }

    def _project_payload(self, project: dict[str, Any]) -> str:
        try:
            return json.dumps(project, ensure_ascii=False, indent=2)
        except Exception:
            return str(project)

    def _project_evaluation(self, project: dict[str, Any]) -> dict[str, Any] | None:
        if "score" not in project and "difficulty" not in project and "time_estimate" not in project and "explanation" not in project:
            return None

        return {
            "score": self._normalize_score(project.get("score")),
            "difficulty": self._normalize_text(project.get("difficulty"), default="ИИ временно недоступен"),
            "time_estimate": self._normalize_text(project.get("time_estimate"), default="ИИ временно недоступен"),
            "explanation": self._normalize_text(project.get("explanation"), default="ИИ временно недоступен"),
        }

    def _extract_json(self, text: str) -> dict[str, Any] | None:
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

        candidates = [cleaned]
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(cleaned[start : end + 1])

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    def _normalize_score(self, value: Any) -> int:
        try:
            score = int(float(value))
        except (TypeError, ValueError):
            return 0
        return max(0, min(100, score))

    def _normalize_text(self, value: Any, *, default: str) -> str:
        text = str(value or "").strip()
        return text or default
