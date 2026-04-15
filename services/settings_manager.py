from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from config import config


@dataclass(slots=True)
class SettingsManager:
    settings_path: Path

    async def load_settings(self) -> dict[str, bool | int]:
        default_settings = self._default_settings()
        if not self.settings_path.exists():
            return default_settings

        try:
            content = self.settings_path.read_text(encoding="utf-8")
            if not content.strip():
                return default_settings
            data: Any = json.loads(content)
        except (OSError, json.JSONDecodeError):
            logger.exception("Failed to load settings JSON")
            return default_settings

        if not isinstance(data, dict):
            return default_settings

        settings = default_settings.copy()
        for key, default_value in settings.items():
            value = data.get(key)
            if isinstance(default_value, bool) and isinstance(value, bool):
                settings[key] = value
            if key == "min_price" and isinstance(value, int):
                settings[key] = value
        return settings

    async def save_settings(self, settings: dict[str, bool | int]) -> None:
        normalized = self._normalize_settings(settings)
        self.settings_path.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def toggle_platform(self, platform: str) -> bool:
        settings = await self.load_settings()
        key = self._platform_key(platform)
        settings[key] = not bool(settings[key])
        await self.save_settings(settings)
        return bool(settings[key])

    async def update_min_price(self, min_price: int) -> int:
        settings = await self.load_settings()
        settings["min_price"] = min_price
        await self.save_settings(settings)
        return min_price

    def _default_settings(self) -> dict[str, bool | int]:
        return {
            "kwork_enabled": config.KWORK_ENABLED,
            "fl_enabled": config.FL_ENABLED,
            "freelanceru_enabled": True,
            "weblancer_enabled": True,
            "youdo_enabled": True,
            "pchel_enabled": True,
            "freelancehunt_enabled": False,
            "min_price": config.MIN_PRICE,
        }

    def _normalize_settings(self, settings: dict[str, bool | int]) -> dict[str, bool | int]:
        defaults = self._default_settings()
        normalized = defaults.copy()
        for key, default_value in defaults.items():
            value = settings.get(key)
            if isinstance(default_value, bool) and isinstance(value, bool):
                normalized[key] = value
            if key == "min_price" and isinstance(value, int):
                normalized[key] = value
        return normalized

    def _platform_key(self, platform: str) -> str:
        normalized = platform.strip().lower()
        if normalized == "kwork":
            return "kwork_enabled"
        if normalized in {"fl", "fl.ru", "flru"}:
            return "fl_enabled"
        if normalized in {"freelance", "freelance.ru", "freelanceru"}:
            return "freelanceru_enabled"
        if normalized == "weblancer":
            return "weblancer_enabled"
        if normalized == "youdo":
            return "youdo_enabled"
        if normalized == "pchel":
            return "pchel_enabled"
        if normalized in {"freelancehunt", "freelance_hunt", "freelance-hunt"}:
            return "freelancehunt_enabled"
        raise ValueError(f"Unknown platform: {platform}")


settings_manager = SettingsManager(Path(config.SETTINGS_JSON_PATH))
