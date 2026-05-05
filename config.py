from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(slots=True)
class Config:
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_USER_ID: int = int(os.getenv("TELEGRAM_USER_ID", "0"))
    OWNER_ID: int = int(os.getenv("OWNER_ID", "0"))
    KEYWORDS_JSON_PATH: str = str(Path(__file__).resolve().parent / "data" / "keywords.json")
    SETTINGS_JSON_PATH: str = str(Path(__file__).resolve().parent / "data" / "settings.json")
    TELEGRAM_SOURCES_JSON_PATH: str = str(Path(__file__).resolve().parent / "data" / "telegram_sources.json")
    TELETHON_SESSION_PATH: str = str(Path(__file__).resolve().parent / "sessions" / "parser")
    DATABASE_URL: str = "sqlite+aiosqlite:///data/projects.db"
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    AI_MODEL: str = os.getenv("AI_MODEL", "deepseek/deepseek-v4-flash")
    OPENROUTER_HTTP_REFERER: str = os.getenv("OPENROUTER_HTTP_REFERER", "https://github.com/freelance-parser")
    OPENROUTER_APP_TITLE: str = os.getenv("OPENROUTER_APP_TITLE", "Freelance Parser")
    KWORK_LOGIN: str = os.getenv("KWORK_LOGIN", "")
    KWORK_PASSWORD: str = os.getenv("KWORK_PASSWORD", "")
    KWORK_PHONE_LAST: str = os.getenv("KWORK_PHONE_LAST", "")
    KWORK_ENABLED: bool = True
    FL_ENABLED: bool = True
    MIN_PRICE: int = 3000
    TRIAL_DAYS: int = 3
    SUBSCRIPTION_PRICE_RUB: int = 990
    SUBSCRIPTION_PRICE_STARS: int = 250
    YOOMONEY_WALLET: str = os.getenv("YOOMONEY_WALLET", "")
    YOOMONEY_LINK: str = os.getenv("YOOMONEY_LINK", "")
    YOOMONEY_TOKEN: str = os.getenv("YOOMONEY_TOKEN", "")
    YOOKASSA_SHOP_ID: str = os.getenv("YOOKASSA_SHOP_ID", "")
    YOOKASSA_SECRET_KEY: str = os.getenv("YOOKASSA_SECRET_KEY", "")
    TELEGRAM_BOT_USERNAME: str = os.getenv("TELEGRAM_BOT_USERNAME", "")
    TG_PARSER_API_ID: str = os.getenv("TG_PARSER_API_ID", "")
    TG_PARSER_API_HASH: str = os.getenv("TG_PARSER_API_HASH", "")
    TG_PARSER_PHONE: str = os.getenv("TG_PARSER_PHONE", "")
    SUBSCRIPTION_DAYS: int = 30
    KWORK_CATEGORIES_IDS: list[int | str] = None
    FL_CATEGORIES: list[str] = None

    def __post_init__(self) -> None:
        if self.KWORK_CATEGORIES_IDS is None:
            self.KWORK_CATEGORIES_IDS = [11, 37, 38, 41, 79, 80]
        if self.FL_CATEGORIES is None:
            self.FL_CATEGORIES = ["5", "4"]


config = Config()
