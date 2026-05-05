from __future__ import annotations

import asyncio
import json
import re
import time
from html import escape as html_escape
from pathlib import Path
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from loguru import logger

from bot.rate_limiter import is_rate_limited
from bot.states import AddTelegramSource, BotStates
from config import config
from filters import KeywordFilter, PriceFilter
from notifications import TelegramNotifier
from notifications.telegram_bot import get_project
from parsers.fl import FLParser
from parsers.freelancehunt import FreelanceHuntParser
from parsers.freelanceru import FreelanceRuParser
from parsers.kwork import KworkParser
from parsers.weblancer import WeblancerParser
from parsers.pchel import PchelParser
from parsers.youdo import YouDoParser
from services import KeywordsManager, subscription_manager, telegram_sources_manager
from services.ai_helper import AIHelper
from services.scheduler import parser_scheduler
from services.settings_manager import settings_manager
from services.telegram_client import get_client, is_ready
from services.yookassa_payment import check_payment, create_payment
from database.db import get_project_stats
from notifications.telegram_bot import PLATFORM_LABELS

router = Router()
keywords_manager = KeywordsManager(Path(config.KEYWORDS_JSON_PATH))
notifier = TelegramNotifier(bot_token=config.TELEGRAM_BOT_TOKEN, user_id=config.TELEGRAM_USER_ID)
ai_helper = AIHelper(api_key=config.OPENROUTER_API_KEY, model=config.AI_MODEL)
DATA_DIR = Path(config.KEYWORDS_JSON_PATH).parent
USER_FILTERS_DIR = DATA_DIR / "users"
BLACKLIST_PATH = DATA_DIR / "blacklist.json"
PRIORITY_PATH = DATA_DIR / "priority.json"
DEFAULT_PRIORITY = {
    "words": ["telegram", "бот", "парсер", "python", "автоматизация", "скрипт", "api"],
    "min_price_red": 10000,
    "min_price_yellow": 3000,
}
pending_yookassa_payments: dict[int, tuple[str, float]] = {}
pending_responses: dict[str, str] = {}
PENDING_RESPONSES_LIMIT = 500
LIST_PAGE_SIZE = 20
SUBSCRIPTION_TEXT = (
    "🤖 Парсер фриланс-заказов\n\n"
    "Что делает бот:\n"
    "✅ Автопарсинг по кнопке владельца\n"
    "✅ Kwork, FL.ru, Freelance.ru, Weblancer, YouDo, Pchel\n"
    "✅ AI-оценка каждого заказа\n"
    "✅ Фильтры по твоим ключевым словам\n"
    "✅ Приоритизация по бюджету\n"
    "✅ Чёрный список стоп-слов\n\n"
    f"💰 Стоимость: {config.SUBSCRIPTION_PRICE_RUB}₽/мес\n\n"
    "Способы оплаты:"
)
SEARCH_LOCKED_TEXT = (
    "🔒 Эта функция доступна по подписке\n\n"
    f"Попробуй бесплатно {config.TRIAL_DAYS} дня через меню «💎 Подписка» или команду /subscribe"
)
TRIAL_WELCOME_TEXT = (
    "👋 Привет! Я парсю фриланс-заказы пока ты занимаешься делами.\n\n"
    f"🎁 Тебе активирован бесплатный триал на {config.TRIAL_DAYS} дня!\n\n"
    "Что умею:\n"
    "✅ Автопарсинг по кнопке владельца\n"
    "✅ Фильтрую мусор по ключевым словам\n"
    "✅ AI оценивает каждый заказ\n"
    "✅ Присылаю только релевантные\n\n"
    "Открой «💎 Подписка» или начни с настройки ключевых слов 👇"
)


def _auto_parsing_button_text() -> str:
    last_run = parser_scheduler.get_last_run_text()
    if parser_scheduler.is_auto_parsing_enabled():
        return f"⏹ Выключить автопарсинг · {last_run}"
    return f"▶️ Включить автопарсинг · {last_run}"


def _main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text="🔍 Запустить поиск", callback_data="search")],
        [InlineKeyboardButton(text="💎 Подписка", callback_data="subscribe_menu")],
    ]

    if _is_owner(user_id):
        keyboard.extend(
            [
                [InlineKeyboardButton(text=_auto_parsing_button_text(), callback_data="toggle_auto_parsing")],
                [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
                [InlineKeyboardButton(text="⚙️ Площадки", callback_data="platforms_menu")],
                [InlineKeyboardButton(text="📡 Telegram-источники", callback_data="tgsrc:menu")],
            ]
        )

    keyboard.extend(
        [
            [InlineKeyboardButton(text="📝 Ключевые слова", callback_data="keywords_menu")],
            [InlineKeyboardButton(text="⛔ Чёрный список", callback_data="blacklist_menu")],
            [InlineKeyboardButton(text="🎯 Приоритетные слова", callback_data="priority_menu")],
        ]
    )

    keyboard.append([InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _slice_items(items: list[str], page: int) -> list[str]:
    start = max(page, 0) * LIST_PAGE_SIZE
    end = start + LIST_PAGE_SIZE
    return items[start:end]


def _paged_list_footer(total: int, page: int, show_more_callback: str | None, back_callback: str) -> list[list[InlineKeyboardButton]]:
    keyboard: list[list[InlineKeyboardButton]] = []
    shown_count = min(total, (page + 1) * LIST_PAGE_SIZE)
    if show_more_callback and shown_count < total:
        keyboard.append([InlineKeyboardButton(text="Показать ещё", callback_data=show_more_callback)])
    keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data=back_callback)])
    return keyboard


def _keywords_menu_keyboard(keywords: list[str], page: int = 0) -> InlineKeyboardMarkup:
    visible_keywords = _slice_items(keywords, page)
    keyboard: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=f"{keyword} ❌", callback_data=f"remove_{keyword}")]
        for keyword in visible_keywords
    ]
    keyboard.append([InlineKeyboardButton(text="➕ Добавить слово", callback_data="add_keyword")])
    keyboard.extend(
        _paged_list_footer(
            len(keywords),
            page,
            f"keywords_more_{page + 1}" if len(keywords) > (page + 1) * LIST_PAGE_SIZE else None,
            "main_menu",
        )
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _delete_list_keyboard(items: list[str], prefix: str, back_callback: str, page: int = 0) -> InlineKeyboardMarkup:
    visible_items = _slice_items(items, page)
    keyboard: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=f"❌ {item}", callback_data=f"{prefix}{item}")]
        for item in visible_items
    ]
    more_callback = None
    if len(items) > (page + 1) * LIST_PAGE_SIZE:
        more_callback = f"{back_callback}_more_{page + 1}"
    keyboard.extend(_paged_list_footer(len(items), page, more_callback, back_callback))
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _priority_keyboard(priority: dict[str, Any], page: int = 0) -> InlineKeyboardMarkup:
    visible_words = _slice_items(priority["words"], page)
    keyboard: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=f"❌ {keyword}", callback_data=f"priority_remove_{keyword}")]
        for keyword in visible_words
    ]
    keyboard.append([InlineKeyboardButton(text="➕ Добавить слово", callback_data="priority_add_word")])
    keyboard.append([InlineKeyboardButton(text="❌ Удалить слово", callback_data="priority_delete_menu")])
    keyboard.append([InlineKeyboardButton(text="💰 Пороги цен", callback_data="priority_thresholds")])
    keyboard.extend(
        _paged_list_footer(
            len(priority["words"]),
            page,
            f"priority_more_{page + 1}" if len(priority["words"]) > (page + 1) * LIST_PAGE_SIZE else None,
            "main_menu",
        )
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _trial_welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Попробовать поиск", callback_data="search")],
            [InlineKeyboardButton(text="💎 Подписка", callback_data="subscribe_menu")],
        ]
    )


def _list_title(title: str, items: list[str], page: int = 0) -> str:
    visible_items = _slice_items(items, page)
    body = "\n".join(f"• {item}" for item in visible_items) if visible_items else "Список пуст."
    suffix = ""
    if len(items) > LIST_PAGE_SIZE and (page + 1) * LIST_PAGE_SIZE < len(items):
        suffix = "\n\nПоказаны первые элементы. Нажми «Показать ещё»."
    return f"{title}\n\nВсего слов: {len(items)}\n\n{body}{suffix}"


def _platforms_menu_keyboard(settings: dict[str, bool | int], platform_today_counts: dict[str, int] | None = None) -> InlineKeyboardMarkup:
    today_counts = platform_today_counts or {}

    def label(name: str, enabled: bool) -> str:
        today_count = today_counts.get(name, 0)
        return f"{'✅' if enabled else '❌'} {name} ({today_count} сегодня)"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label("Kwork", bool(settings["kwork_enabled"])), callback_data="toggle_platform_kwork")],
            [InlineKeyboardButton(text=label("FL.ru", bool(settings["fl_enabled"])), callback_data="toggle_platform_fl")],
            [InlineKeyboardButton(text=label("Freelance.ru", bool(settings["freelanceru_enabled"])), callback_data="toggle_platform_freelanceru")],
            [InlineKeyboardButton(text=label("Weblancer", bool(settings["weblancer_enabled"])), callback_data="toggle_platform_weblancer")],
            [InlineKeyboardButton(text=label("YouDo", bool(settings["youdo_enabled"])), callback_data="toggle_platform_youdo")],
            [InlineKeyboardButton(text=label("Pchel", bool(settings["pchel_enabled"])), callback_data="toggle_platform_pchel")],
            [InlineKeyboardButton(text=label("FreelanceHunt", bool(settings["freelancehunt_enabled"])), callback_data="toggle_platform_freelancehunt")],
            [InlineKeyboardButton(text=label("Telegram", bool(settings.get("telegram_chats_enabled", False))), callback_data="toggle_platform_telegram")],
            [InlineKeyboardButton(text=f"💰 Мин. цена: {int(settings['min_price'])}₽", callback_data="set_min_price")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")],
        ]
    )


def _subscription_keyboard(is_active: bool) -> InlineKeyboardMarkup:
    action_text = "🔄 Продлить" if is_active else "💳 Оформить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{action_text} за {config.SUBSCRIPTION_PRICE_STARS} Stars", callback_data="subscribe_stars")],
            [InlineKeyboardButton(text=f"{action_text} за {config.SUBSCRIPTION_PRICE_RUB}₽ — ЮКасса", callback_data="subscribe_yookassa")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")],
        ]
    )


def _yookassa_check_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔄 Проверить оплату", callback_data="yookassa_check_payment")]]
    )


def _yookassa_text(confirmation_url: str) -> str:
    return (
        "💳 Оплата через ЮКассу\n\n"
        f"Сумма: {config.SUBSCRIPTION_PRICE_RUB}₽\n"
        f"Ссылка на оплату: {confirmation_url}\n\n"
        "После оплаты нажми кнопку ниже 👇"
    )


def _yookassa_missing_payment_text() -> str:
    return "❌ Сначала создай новый платёж через кнопку ЮКасса."


def _yookassa_pending_text() -> str:
    return "⏳ Платёж ещё не завершён. После оплаты нажми кнопку проверки ещё раз."


def _yookassa_error_text() -> str:
    return "❌ Не удалось создать платёж. Попробуй позже."


def _priority_thresholds_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔴 Изменить верхний порог", callback_data="edit_priority_high")],
            [InlineKeyboardButton(text="🟡 Изменить средний порог", callback_data="edit_priority_medium")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="priority_menu")],
        ]
    )


async def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default

    try:
        content = path.read_text(encoding="utf-8")
        if not content.strip():
            return default
        return json.loads(content)
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to load JSON file: {}", path)
        return default


async def _save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_word(word: str) -> str:
    return word.strip().lower()


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    return value[:8] + "***"


def _validate_keyword(value: str) -> str | None:
    normalized = value.strip().lower()
    if not normalized or len(normalized) > 50:
        return None
    if not all(char.isalnum() or char in {" ", "-"} for char in normalized):
        return None
    return normalized


def _validate_min_price(value: str) -> int | None:
    normalized = value.strip()
    if not normalized.isdigit():
        return None
    parsed = int(normalized)
    if not 0 <= parsed <= 1_000_000:
        return None
    return parsed


def _is_owner(user_id: int) -> bool:
    return user_id == config.OWNER_ID


def _target_user_id(target: Message | CallbackQuery) -> int:
    return target.from_user.id


def _user_filters_dir(user_id: int) -> Path:
    return USER_FILTERS_DIR / str(user_id)


def _user_keywords_path(user_id: int) -> Path:
    return _user_filters_dir(user_id) / "keywords.json"


def _user_blacklist_path(user_id: int) -> Path:
    return _user_filters_dir(user_id) / "blacklist.json"


def _user_priority_path(user_id: int) -> Path:
    return _user_filters_dir(user_id) / "priority.json"


async def _ensure_user_filter_files(user_id: int) -> None:
    user_dir = _user_filters_dir(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)

    keywords_path = _user_keywords_path(user_id)
    if not keywords_path.exists() and Path(config.KEYWORDS_JSON_PATH).exists():
        keywords_path.write_text(Path(config.KEYWORDS_JSON_PATH).read_text(encoding="utf-8"), encoding="utf-8")

    blacklist_path = _user_blacklist_path(user_id)
    if not blacklist_path.exists() and BLACKLIST_PATH.exists():
        blacklist_path.write_text(BLACKLIST_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    priority_path = _user_priority_path(user_id)
    if not priority_path.exists() and PRIORITY_PATH.exists():
        priority_path.write_text(PRIORITY_PATH.read_text(encoding="utf-8"), encoding="utf-8")


async def _require_owner(target: Message | CallbackQuery) -> bool:
    if _is_owner(_target_user_id(target)):
        return True

    text = "⛔ Команда доступна только владельцу"
    if isinstance(target, CallbackQuery):
        await _safe_callback_answer(target, text, show_alert=True)
        return False

    await target.answer(text)
    return False


def _normalize_words(words: Any) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    if not isinstance(words, list):
        return normalized

    for item in words:
        normalized_word = _normalize_word(str(item))
        if not normalized_word or normalized_word in seen:
            continue
        seen.add(normalized_word)
        normalized.append(normalized_word)
    return normalized


async def _load_word_list(path: Path) -> list[str]:
    data = await _load_json(path, [])
    return _normalize_words(data)


async def _load_user_keywords(user_id: int) -> list[str]:
    await _ensure_user_filter_files(user_id)
    manager = KeywordsManager(_user_keywords_path(user_id))
    return await manager.load_keywords()


async def _add_user_keyword(user_id: int, word: str) -> list[str]:
    await _ensure_user_filter_files(user_id)
    manager = KeywordsManager(_user_keywords_path(user_id))
    return await manager.add_keyword(word)


async def _remove_user_keyword(user_id: int, word: str) -> list[str]:
    await _ensure_user_filter_files(user_id)
    manager = KeywordsManager(_user_keywords_path(user_id))
    return await manager.remove_keyword(word)


async def _load_user_blacklist(user_id: int) -> list[str]:
    await _ensure_user_filter_files(user_id)
    return await _load_word_list(_user_blacklist_path(user_id))


async def _add_user_blacklist_word(user_id: int, word: str) -> list[str]:
    await _ensure_user_filter_files(user_id)
    return await _add_word_to_list(_user_blacklist_path(user_id), word)


async def _remove_user_blacklist_word(user_id: int, word: str) -> list[str]:
    await _ensure_user_filter_files(user_id)
    return await _remove_word_from_list(_user_blacklist_path(user_id), word)


async def _load_user_priority(user_id: int) -> dict[str, Any]:
    await _ensure_user_filter_files(user_id)
    path = _user_priority_path(user_id)
    data = await _load_json(path, DEFAULT_PRIORITY.copy())
    if not isinstance(data, dict):
        return DEFAULT_PRIORITY.copy()

    words = _normalize_words(data.get("words", []))
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


async def _save_user_priority(user_id: int, priority: dict[str, Any]) -> dict[str, Any]:
    await _ensure_user_filter_files(user_id)
    normalized = {
        "words": _normalize_words(priority.get("words", [])),
        "min_price_red": int(priority.get("min_price_red", DEFAULT_PRIORITY["min_price_red"])),
        "min_price_yellow": int(priority.get("min_price_yellow", DEFAULT_PRIORITY["min_price_yellow"])),
    }
    await _save_json(_user_priority_path(user_id), normalized)
    return normalized


async def _save_word_list(path: Path, words: list[str]) -> list[str]:
    normalized = _normalize_words(words)
    await _save_json(path, normalized)
    return normalized


async def _add_word_to_list(path: Path, word: str) -> list[str]:
    words = await _load_word_list(path)
    normalized_word = _normalize_word(word)
    if normalized_word and normalized_word not in words:
        words.append(normalized_word)
        words = await _save_word_list(path, words)
    return words


async def _remove_word_from_list(path: Path, word: str) -> list[str]:
    words = await _load_word_list(path)
    normalized_word = _normalize_word(word)
    if normalized_word in words:
        words = [item for item in words if item != normalized_word]
        words = await _save_word_list(path, words)
    return words


async def _load_priority() -> dict[str, Any]:
    data = await _load_json(PRIORITY_PATH, DEFAULT_PRIORITY.copy())
    if not isinstance(data, dict):
        return DEFAULT_PRIORITY.copy()

    words = _normalize_words(data.get("words", []))
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


async def _save_priority(priority: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "words": _normalize_words(priority.get("words", [])),
        "min_price_red": int(priority.get("min_price_red", DEFAULT_PRIORITY["min_price_red"])),
        "min_price_yellow": int(priority.get("min_price_yellow", DEFAULT_PRIORITY["min_price_yellow"])),
    }
    await _save_json(PRIORITY_PATH, normalized)
    return normalized


async def _safe_callback_answer(callback: CallbackQuery, *args: Any, **kwargs: Any) -> None:
    try:
        await callback.answer(*args, **kwargs)
    except Exception:
        pass


async def _show_main_menu(target: Message | CallbackQuery) -> None:
    text = (
        "Главное меню\n\n"
        f"Автопарсинг: {'включён' if parser_scheduler.is_auto_parsing_enabled() else 'выключен'}\n"
        f"Последний запуск: {parser_scheduler.get_last_run_text()}\n\n"
        "Выберите действие кнопками ниже."
    )
    user_id = _target_user_id(target)
    if not _is_owner(user_id):
        text = "Главное меню\n\nВыберите действие кнопками ниже."
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=_main_menu_keyboard(user_id))
        await _safe_callback_answer(target)
        return

    await target.answer(text, reply_markup=_main_menu_keyboard(user_id))


async def _show_help(target: Message | CallbackQuery) -> None:
    text = (
        "Помощь\n\n"
        "• 🔍 Запустить поиск — запускает парсинг и фильтрацию\n"
        "• 📝 Ключевые слова — открывает список слов\n"
        "• ⛔ Чёрный список — управляет стоп-словами\n"
        "• 🎯 Приоритетные слова — управляет словами для красного статуса\n"
        "• ⚙️ Площадки — включает и выключает источники\n"
        "• ➕ Добавить слово — добавляет слово в текущий список\n"
        "• ❌ Удалить слово — показывает кнопки удаления"
    )
    user_id = _target_user_id(target)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=_main_menu_keyboard(user_id))
        await _safe_callback_answer(target)
        return

    await target.answer(text, reply_markup=_main_menu_keyboard(user_id))


async def _show_subscription(target: Message | CallbackQuery) -> None:
    user_id = _target_user_id(target)
    status_text = await subscription_manager.get_status_text(user_id)
    is_active = await subscription_manager.is_subscribed(user_id)
    text = (
        f"{SUBSCRIPTION_TEXT}\n\n"
        f"{status_text}\n\n"
        "Что входит:\n"
        "• Доступ к разовому поиску\n"
        "• Автоматическая рассылка заказов\n"
        "• AI-оценка заказов\n"
        "• Фильтры по ключевым словам и blacklist\n\n"
        "После оплаты нажми Проверить оплату."
    )

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=_subscription_keyboard(is_active))
        await _safe_callback_answer(target)
        return

    await target.answer(text, reply_markup=_subscription_keyboard(is_active))


async def _notify_owner_about_yoomoney_payment(user_id: int, username: str | None) -> None:
    username_text = f"@{username}" if username else "без username"
    await notifier.send_message(
        "💰 Новая оплата ЮMoney!\n"
        f"Пользователь: {username_text} ({user_id})\n"
        f"Сумма: {config.SUBSCRIPTION_PRICE_RUB}₽\n"
        f"Активировать: /activate {user_id}"
    )


async def _show_statistics(target: Message | CallbackQuery) -> None:
    stats = await get_project_stats(PLATFORM_LABELS)
    subscription_stats = await subscription_manager.get_stats()
    platform_counts = stats["platform_counts"]
    platform_lines = "\n".join(f"• {platform}: {count}" for platform, count in platform_counts.items())
    text = (
        "📊 Статистика\n\n"
        f"Всего заказов в базе: {stats['total_orders']}\n"
        f"За сегодня: {stats['today_orders']}\n"
        f"За последние 24 часа: {stats['last_24h_orders']}\n"
        f"Отправлено заказов за 24ч: {stats['last_24h_orders']}\n\n"
        f"Всего подписок: {subscription_stats['total']}\n"
        f"Активных: {subscription_stats['active']}\n"
        f"Триалы: {subscription_stats['trials']}\n"
        f"Платные: {subscription_stats['paid']}\n"
        f"Истёкшие: {subscription_stats['expired']}\n\n"
        "По площадкам:\n"
        f"{platform_lines}"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]]
    )
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=keyboard)
        await _safe_callback_answer(target)
        return

    await target.answer(text, reply_markup=keyboard)


def _safe_callback_value(callback: CallbackQuery) -> str:
    value = (callback.data or "").strip()
    return value[:80] if value else "<empty>"


def _log_masked_config_usage() -> None:
    for name, value in (
        ("TELEGRAM_BOT_TOKEN", config.TELEGRAM_BOT_TOKEN),
        ("OPENROUTER_API_KEY", config.OPENROUTER_API_KEY),
        ("GROQ_API_KEY", config.GROQ_API_KEY),
        ("YOOMONEY_TOKEN", config.YOOMONEY_TOKEN),
    ):
        if value:
            logger.debug("{} configured: {}", name, _mask_secret(value))


_log_masked_config_usage()


async def _ensure_subscription(user_id: int) -> bool:
    return await subscription_manager.is_subscribed(user_id)


async def _run_search(progress_message: Message | None = None) -> tuple[int, int, int, int, int, int, int, int, list[str]]:
    settings = await settings_manager.load_settings()
    projects: list[dict[str, str]] = []
    kwork_count = 0
    fl_count = 0
    freelanceru_count = 0
    weblancer_count = 0
    youdo_count = 0
    pchel_count = 0
    freelancehunt_count = 0
    status_parts: list[str] = []

    async def update_progress() -> None:
        if progress_message is None:
            return
        parts = []
        if settings["kwork_enabled"]:
            parts.append(f"✅ Kwork: {kwork_count}" if kwork_count else "⏳ Kwork...")
        if settings["fl_enabled"]:
            parts.append(f"✅ FL.ru: {fl_count}" if fl_count else "⏳ FL.ru...")
        if settings["freelanceru_enabled"]:
            parts.append(f"✅ Freelance.ru: {freelanceru_count}" if freelanceru_count else "⏳ Freelance.ru...")
        if settings["weblancer_enabled"]:
            parts.append(f"✅ Weblancer: {weblancer_count}" if weblancer_count else "⏳ Weblancer...")
        if settings["youdo_enabled"]:
            parts.append(f"✅ YouDo: {youdo_count}" if youdo_count else "⏳ YouDo...")
        if settings.get("pchel_enabled", False):
            parts.append(f"✅ Pchel: {pchel_count}" if pchel_count else "⏳ Pchel...")
        if settings.get("freelancehunt_enabled", False):
            parts.append(f"✅ FreelanceHunt: {freelancehunt_count}" if freelancehunt_count else "⏳ FreelanceHunt...")
        await progress_message.edit_text(" | ".join(parts) or "⏳ Парсинг запущен...")

    await update_progress()

    if settings["kwork_enabled"]:
        kwork_parser = KworkParser(
            login=config.KWORK_LOGIN,
            password=config.KWORK_PASSWORD,
            phone_last=config.KWORK_PHONE_LAST,
            categories_ids=config.KWORK_CATEGORIES_IDS,
        )
        try:
            kwork_projects = await kwork_parser.parse()
            kwork_count = len(kwork_projects)
            projects.extend(kwork_projects)
        except Exception:
            status_parts.append("⚠️ Kwork: ошибка")
        finally:
            await kwork_parser.close()
            await update_progress()

    if settings["fl_enabled"]:
        fl_parser = FLParser(categories=config.FL_CATEGORIES)
        try:
            fl_projects = await fl_parser.parse()
            fl_count = len(fl_projects)
            projects.extend(fl_projects)
        except Exception:
            status_parts.append("⚠️ FL.ru: ошибка")
        finally:
            await fl_parser.close()
            await update_progress()

    if settings["freelanceru_enabled"]:
        freelanceru_parser = FreelanceRuParser()
        try:
            freelanceru_projects = await freelanceru_parser.parse()
            freelanceru_count = len(freelanceru_projects)
            projects.extend(freelanceru_projects)
        except Exception:
            status_parts.append("⚠️ Freelance.ru: ошибка")
        finally:
            await freelanceru_parser.close()
            await update_progress()

    if settings["weblancer_enabled"]:
        weblancer_parser = WeblancerParser()
        try:
            weblancer_projects = await weblancer_parser.parse()
            weblancer_count = len(weblancer_projects)
            projects.extend(weblancer_projects)
        except Exception:
            status_parts.append("⚠️ Weblancer: ошибка")
        finally:
            await weblancer_parser.close()
            await update_progress()

    if settings["youdo_enabled"]:
        youdo_parser = YouDoParser()
        try:
            youdo_projects = await youdo_parser.parse()
            youdo_count = len(youdo_projects)
            projects.extend(youdo_projects)
        except Exception:
            status_parts.append("⚠️ YouDo: ошибка")
        finally:
            await youdo_parser.close()
            await update_progress()

    if settings.get("pchel_enabled", False):
        pchel_parser = PchelParser()
        try:
            pchel_projects = await pchel_parser.parse()
            pchel_count = len(pchel_projects)
            projects.extend(pchel_projects)
        except Exception:
            status_parts.append("⚠️ Pchel: ошибка")
        finally:
            await pchel_parser.close()
            await update_progress()

    if settings.get("freelancehunt_enabled", False):
        freelancehunt_parser = FreelanceHuntParser()
        try:
            freelancehunt_projects = await freelancehunt_parser.parse()
            freelancehunt_count = len(freelancehunt_projects)
            projects.extend(freelancehunt_projects)
        except Exception:
            status_parts.append("⚠️ FreelanceHunt: ошибка")
        finally:
            await freelancehunt_parser.close()
            await update_progress()

    keyword_filter = KeywordFilter(keywords_path=config.KEYWORDS_JSON_PATH)
    filtered_projects = await keyword_filter.filter(projects)
    price_filter = PriceFilter(min_price=int(settings["min_price"]))
    filtered_projects = await price_filter.filter(filtered_projects)

    blacklist = await _load_word_list(BLACKLIST_PATH)
    if blacklist:
        remaining_projects: list[dict[str, str]] = []
        for project in filtered_projects:
            title = str(project.get("title", "")).strip()
            description = str(project.get("description", "")).strip()
            haystack = f"{title} {description}".lower()
            if any(word in haystack for word in blacklist):
                logger.info("Чёрный список: пропущен {}", title or "Без названия")
                continue
            remaining_projects.append(project)
        filtered_projects = remaining_projects

    sent_count = 0
    max_new_orders = 30
    for project in filtered_projects:
        if sent_count >= max_new_orders:
            logger.info("Reached limit of {} new orders for this cycle", max_new_orders)
            break

        try:
            await notifier.send_project(project)
        except Exception:
            logger.exception("Notification error")
            continue

        sent_count += 1
        await asyncio.sleep(1)

    return kwork_count, fl_count, freelanceru_count, weblancer_count, youdo_count, pchel_count, freelancehunt_count, sent_count, status_parts


async def _send_ai_response(callback: CallbackQuery, project: dict[str, str]) -> None:
    response_text = await ai_helper.generate_response(project)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📋 Копировать", callback_data="copy_response")]]
    )
    await callback.message.answer(response_text, reply_markup=keyboard)
    await _safe_callback_answer(callback)


async def _show_blacklist_menu(target: Message | CallbackQuery, page: int = 0) -> None:
    user_id = _target_user_id(target)
    blacklist = await _load_user_blacklist(user_id)
    text = _list_title("⛔ Чёрный список", blacklist, page)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить слово", callback_data="blacklist_add_word")],
            [InlineKeyboardButton(text="❌ Удалить слово", callback_data="blacklist_delete_menu")],
            *(_paged_list_footer(len(blacklist), page, f"blacklist_more_{page + 1}" if len(blacklist) > (page + 1) * LIST_PAGE_SIZE else None, "main_menu")),
        ]
    )
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=keyboard)
        await _safe_callback_answer(target)
        return
    await target.answer(text, reply_markup=keyboard)


async def _show_blacklist_delete_menu(target: CallbackQuery, page: int = 0) -> None:
    blacklist = await _load_user_blacklist(target.from_user.id)
    await target.message.edit_text(
        _list_title("Удаление из чёрного списка", blacklist, page),
        reply_markup=_delete_list_keyboard(blacklist, "blacklist_remove_", "blacklist_menu", page),
    )
    await _safe_callback_answer(target)


async def _show_priority_menu(target: Message | CallbackQuery, page: int = 0) -> None:
    priority = await _load_user_priority(_target_user_id(target))
    words = priority["words"]
    text = (
        _list_title("🎯 Приоритетные слова", words, page)
        + f"\n\nПороги цен:\n• 🔴 выше {priority['min_price_red']}₽\n• 🟡 от {priority['min_price_yellow']}₽ до {priority['min_price_red']}₽\n• 🟢 ниже {priority['min_price_yellow']}₽"
    )
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=_priority_keyboard(priority, page))
        await target.answer()
        return
    await target.answer(text, reply_markup=_priority_keyboard(priority, page))


async def _show_priority_delete_menu(target: CallbackQuery, page: int = 0) -> None:
    priority = await _load_user_priority(target.from_user.id)
    await target.message.edit_text(
        _list_title("Удаление из приоритетных слов", priority["words"], page),
        reply_markup=_delete_list_keyboard(priority["words"], "priority_remove_", "priority_menu", page),
    )
    await _safe_callback_answer(target)


async def _show_priority_thresholds(target: CallbackQuery) -> None:
    priority = await _load_user_priority(target.from_user.id)
    text = (
        "💰 Пороги цен\n\n"
        f"🔴 выше {priority['min_price_red']}₽\n"
        f"🟡 от {priority['min_price_yellow']}₽ до {priority['min_price_red']}₽\n"
        f"🟢 ниже {priority['min_price_yellow']}₽"
    )
    await target.message.edit_text(text, reply_markup=_priority_thresholds_keyboard())
    await target.answer()


@router.callback_query(F.data == "copy_response")
async def copy_response_callback(callback: CallbackQuery) -> None:
    if callback.message and callback.message.text:
        await callback.message.answer(callback.message.text)
    await _safe_callback_answer(callback)


@router.callback_query(F.data.startswith("respond_"))
async def respond_callback(callback: CallbackQuery) -> None:
    project_id = callback.data.removeprefix("respond_").strip()
    project = get_project(project_id)
    if project is None:
        await _safe_callback_answer(callback, "Заказ не найден", show_alert=True)
        return

    await _safe_callback_answer(callback, "Генерирую отклик...")
    await _send_ai_response(callback, project)


# ---------------------------------------------------------------------------
# Generate / send AI response — двухэтапный полу-автоматический режим
# ---------------------------------------------------------------------------


def _store_pending_response(project_id: str, response_text: str) -> None:
    pending_responses[project_id] = response_text
    while len(pending_responses) > PENDING_RESPONSES_LIMIT:
        oldest = next(iter(pending_responses))
        if oldest == project_id:
            break
        del pending_responses[oldest]


def _response_action_keyboard(project_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📤 Отправить", callback_data=f"send_resp:{project_id}"),
                InlineKeyboardButton(text="🔄 Перегенерировать", callback_data=f"gen_resp:{project_id}"),
            ]
        ]
    )


def _final_response_keyboard(link: str) -> InlineKeyboardMarkup | None:
    if not link:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔗 Открыть заказ", url=link)]]
    )


async def _generate_ai_response(project: dict[str, Any]) -> str | None:
    """Возвращает текст отклика или None если AI недоступен (rate limit, ошибка, нет ключа)."""
    try:
        response_text = await ai_helper.generate_response(project)
    except Exception:
        logger.exception("Ошибка генерации отклика")
        return None

    if ai_helper.last_status in {"rate_limited", "error", "disabled"}:
        return None

    response_text = (response_text or "").strip()
    return response_text or None


@router.callback_query(F.data.startswith("gen_resp:"))
async def gen_resp_callback(callback: CallbackQuery) -> None:
    if not await _require_owner(callback):
        return

    if is_rate_limited(callback.from_user.id, "gen_resp", max_requests=1, period=5):
        await _safe_callback_answer(callback, "⏳ Подожди пару секунд", show_alert=False)
        return

    project_id = callback.data.removeprefix("gen_resp:").strip()
    project = get_project(project_id)
    if project is None:
        await _safe_callback_answer(callback, "Заказ не найден (мог истечь из памяти)", show_alert=True)
        return

    await _safe_callback_answer(callback, "Генерирую отклик...")
    response_text = await _generate_ai_response(project)
    if response_text is None:
        await callback.message.answer("⚠️ ИИ временно недоступен, попробуй позже")
        return

    _store_pending_response(project_id, response_text)
    await callback.message.answer(
        f"✍️ Отклик готов:\n\n{response_text}",
        reply_markup=_response_action_keyboard(project_id),
    )


@router.callback_query(F.data.startswith("send_resp:"))
async def send_resp_callback(callback: CallbackQuery) -> None:
    if not await _require_owner(callback):
        return

    project_id = callback.data.removeprefix("send_resp:").strip()
    project = get_project(project_id)
    if project is None:
        await _safe_callback_answer(callback, "Заказ не найден (мог истечь из памяти)", show_alert=True)
        return

    response_text = pending_responses.get(project_id)
    if not response_text:
        if is_rate_limited(callback.from_user.id, "gen_resp", max_requests=1, period=5):
            await _safe_callback_answer(callback, "⏳ Подожди пару секунд", show_alert=False)
            return

        await _safe_callback_answer(callback, "Генерирую отклик...")
        response_text = await _generate_ai_response(project)
        if response_text is None:
            await callback.message.answer("⚠️ ИИ временно недоступен, попробуй позже")
            return
        _store_pending_response(project_id, response_text)
    else:
        await _safe_callback_answer(callback)

    link = str(project.get("link", "")).strip()
    body = (
        "📋 Скопируй отклик и вставь на странице заказа:\n\n"
        f"<pre>{html_escape(response_text)}</pre>"
    )
    await callback.message.answer(
        body,
        reply_markup=_final_response_keyboard(link),
        parse_mode="HTML",
    )


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    trial = await subscription_manager.create_trial(message.from_user.id, message.from_user.username)
    if trial is not None:
        trial_text = TRIAL_WELCOME_TEXT
        if _is_owner(message.from_user.id):
            trial_text += "\n\nВключи автопарсинг через меню."
        await message.answer(trial_text, reply_markup=_trial_welcome_keyboard())
        return

    status_text = await subscription_manager.get_status_text(message.from_user.id)
    await message.answer(status_text)
    await _show_main_menu(message)


@router.message(Command("subscribe"))
async def subscribe_command_handler(message: Message) -> None:
    await _show_subscription(message)


@router.callback_query(F.data == "toggle_auto_parsing")
async def toggle_auto_parsing_callback(callback: CallbackQuery) -> None:
    if not await _require_owner(callback):
        return

    enabled = parser_scheduler.toggle_auto_parsing()
    status_text = "✅ Автопарсинг включён" if enabled else "⏹ Автопарсинг выключен"
    await callback.message.edit_text(
        f"Главное меню\n\n{status_text}\nПоследний запуск: {parser_scheduler.get_last_run_text()}",
        reply_markup=_main_menu_keyboard(callback.from_user.id),
    )
    await _safe_callback_answer(callback, "Сохранено")


@router.callback_query(F.data == "stats")
async def stats_callback(callback: CallbackQuery) -> None:
    if not await _require_owner(callback):
        return

    await _show_statistics(callback)


@router.message(Command("stats"))
async def stats_command_handler(message: Message) -> None:
    if not await _require_owner(message):
        return

    await _show_statistics(message)


@router.message(Command("mystatus"))
async def my_status_command_handler(message: Message) -> None:
    status_text = await subscription_manager.get_status_text(message.from_user.id)
    await message.answer(status_text)


@router.message(Command("users"))
async def users_command_handler(message: Message) -> None:
    if not await _require_owner(message):
        return

    summary = await subscription_manager.get_users_summary()
    await message.answer(summary)


@router.message(Command("activate"))
async def activate_command_handler(message: Message, command: CommandObject) -> None:
    if not await _require_owner(message):
        return

    user_id_text = (command.args or "").strip()
    if not user_id_text.isdigit():
        await message.answer("Используй формат: /activate user_id")
        return

    user_id = int(user_id_text)
    subscription = await subscription_manager.activate_subscription(
        user_id=user_id,
        username=None,
        method="manual",
        days=config.SUBSCRIPTION_DAYS,
        notes="Активировал вручную",
    )
    date_text = subscription.expires_at.strftime("%d.%m.%Y") if subscription.expires_at else "—"

    owner_bot = TelegramNotifier(bot_token=config.TELEGRAM_BOT_TOKEN, user_id=user_id)
    await owner_bot.send_message(
        "✅ Подписка активирована!\n"
        f"Активна до: {date_text}\n"
        "Приятного использования! 🚀"
    )
    await message.answer(f"✅ Подписка активирована для {user_id}")


@router.message(Command("help"))
async def help_command_handler(message: Message) -> None:
    await _show_help(message)


@router.callback_query(F.data == "help")
async def help_callback(callback: CallbackQuery) -> None:
    await _show_help(callback)


@router.callback_query(F.data == "subscribe_stars")
async def subscribe_stars_callback(callback: CallbackQuery) -> None:
    await callback.message.answer_invoice(
        title="Подписка на парсер — 1 месяц",
        description="Доступ к боту-парсеру фриланс заказов на 30 дней",
        payload="subscription_30days",
        currency="XTR",
        prices=[LabeledPrice(label="1 месяц", amount=config.SUBSCRIPTION_PRICE_STARS)],
    )
    await _safe_callback_answer(callback)


@router.callback_query(F.data == "subscribe_yookassa")
async def subscribe_yookassa_callback(callback: CallbackQuery) -> None:
    try:
        payment_id, confirmation_url = await asyncio.to_thread(create_payment, callback.from_user.id)
    except RuntimeError:
        await callback.message.answer(_yookassa_error_text())
        await _safe_callback_answer(callback)
        return

    pending_yookassa_payments[callback.from_user.id] = (payment_id, time.time())
    await callback.message.edit_text(
        _yookassa_text(confirmation_url),
        reply_markup=_yookassa_check_keyboard(),
    )
    await _safe_callback_answer(callback)


@router.callback_query(F.data == "yookassa_check_payment")
async def yookassa_check_payment_callback(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    username = callback.from_user.username

    expired_user_ids = [
        stored_user_id
        for stored_user_id, (_, created_at) in pending_yookassa_payments.items()
        if time.time() - created_at > 3600
    ]
    for expired_user_id in expired_user_ids:
        pending_yookassa_payments.pop(expired_user_id, None)

    if is_rate_limited(user_id, "yookassa_check_payment"):
        await callback.message.answer("⏳ Слишком частые запросы. Подожди минуту.")
        await _safe_callback_answer(callback)
        return

    pending_payment = pending_yookassa_payments.get(user_id)
    if not pending_payment:
        await callback.message.answer(_yookassa_missing_payment_text())
        await _safe_callback_answer(callback)
        return

    payment_id, _ = pending_payment
    payment_succeeded = await asyncio.to_thread(check_payment, payment_id)
    if not payment_succeeded:
        await callback.message.answer(
            _yookassa_pending_text(),
            reply_markup=_yookassa_check_keyboard(),
        )
        await _safe_callback_answer(callback)
        return

    subscription = await subscription_manager.activate_subscription(
        user_id=user_id,
        username=username,
        method="yookassa",
        days=config.SUBSCRIPTION_DAYS,
    )
    pending_yookassa_payments.pop(user_id, None)
    date_text = subscription.expires_at.strftime("%d.%m.%Y") if subscription.expires_at else "—"
    await callback.message.answer(
        "✅ Подписка активирована!\n"
        f"Активна до: {date_text}"
    )
    await _safe_callback_answer(callback)


@router.pre_checkout_query()
async def pre_checkout_query_handler(pre_checkout_query: PreCheckoutQuery) -> None:
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment_handler(message: Message) -> None:
    payment = message.successful_payment
    if payment.currency != "XTR" or payment.invoice_payload != "subscription_30days":
        return

    subscription = await subscription_manager.activate_subscription(
        user_id=message.from_user.id,
        username=message.from_user.username,
        method="stars",
        days=config.SUBSCRIPTION_DAYS,
    )
    date_text = subscription.expires_at.strftime("%d.%m.%Y") if subscription.expires_at else "—"
    await message.answer(
        "✅ Подписка активирована!\n"
        f"Активна до: {date_text}\n"
        "Приятного использования! 🚀"
    )


@router.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: CallbackQuery) -> None:
    await _show_main_menu(callback)


@router.message(Command("keywords"))
async def keywords_command_handler(message: Message) -> None:
    if not await _ensure_subscription(message.from_user.id):
        await message.answer(SEARCH_LOCKED_TEXT, reply_markup=_subscription_keyboard(False))
        return

    keywords = await _load_user_keywords(message.from_user.id)
    await message.answer(_list_title("📝 Ключевые слова", keywords), reply_markup=_keywords_menu_keyboard(keywords))


@router.callback_query(F.data == "keywords_menu")
async def keywords_menu_callback(callback: CallbackQuery) -> None:
    if not await _ensure_subscription(callback.from_user.id):
        await callback.message.edit_text(SEARCH_LOCKED_TEXT, reply_markup=_subscription_keyboard(False))
        await _safe_callback_answer(callback)
        return

    keywords = await _load_user_keywords(callback.from_user.id)
    await callback.message.edit_text(_list_title("📝 Ключевые слова", keywords), reply_markup=_keywords_menu_keyboard(keywords))
    await _safe_callback_answer(callback)


@router.callback_query(F.data.startswith("keywords_more_"))
async def keywords_more_callback(callback: CallbackQuery) -> None:
    if not await _ensure_subscription(callback.from_user.id):
        await callback.message.edit_text(SEARCH_LOCKED_TEXT, reply_markup=_subscription_keyboard(False))
        await _safe_callback_answer(callback)
        return

    page_text = callback.data.removeprefix("keywords_more_").strip()
    page = int(page_text) if page_text.isdigit() else 0
    keywords = await _load_user_keywords(callback.from_user.id)
    await callback.message.edit_text(
        _list_title("📝 Ключевые слова", keywords, page),
        reply_markup=_keywords_menu_keyboard(keywords, page),
    )
    await _safe_callback_answer(callback)


@router.callback_query(F.data.startswith("blacklist_menu_more_"))
async def blacklist_more_callback(callback: CallbackQuery) -> None:
    if not await _ensure_subscription(callback.from_user.id):
        await callback.message.edit_text(SEARCH_LOCKED_TEXT, reply_markup=_subscription_keyboard(False))
        await _safe_callback_answer(callback)
        return

    page_text = callback.data.removeprefix("blacklist_menu_more_").strip()
    page = int(page_text) if page_text.isdigit() else 0
    await _show_blacklist_delete_menu(callback, page)


@router.callback_query(F.data.startswith("priority_menu_more_"))
async def priority_more_callback(callback: CallbackQuery) -> None:
    if not await _ensure_subscription(callback.from_user.id):
        await callback.message.edit_text(SEARCH_LOCKED_TEXT, reply_markup=_subscription_keyboard(False))
        await _safe_callback_answer(callback)
        return

    page_text = callback.data.removeprefix("priority_menu_more_").strip()
    page = int(page_text) if page_text.isdigit() else 0
    await _show_priority_delete_menu(callback, page)


@router.callback_query(F.data.startswith("priority_more_"))
async def priority_menu_more_callback(callback: CallbackQuery) -> None:
    if not await _ensure_subscription(callback.from_user.id):
        await callback.message.edit_text(SEARCH_LOCKED_TEXT, reply_markup=_subscription_keyboard(False))
        await _safe_callback_answer(callback)
        return

    page_text = callback.data.removeprefix("priority_more_").strip()
    page = int(page_text) if page_text.isdigit() else 0
    await _show_priority_menu(callback, page)
    await _safe_callback_answer(callback)


@router.callback_query(F.data.startswith("blacklist_more_"))
async def blacklist_menu_more_callback(callback: CallbackQuery) -> None:
    if not await _ensure_subscription(callback.from_user.id):
        await callback.message.edit_text(SEARCH_LOCKED_TEXT, reply_markup=_subscription_keyboard(False))
        await _safe_callback_answer(callback)
        return

    page_text = callback.data.removeprefix("blacklist_more_").strip()
    page = int(page_text) if page_text.isdigit() else 0
    await _show_blacklist_menu(callback, page)
    await _safe_callback_answer(callback)


@router.callback_query(F.data == "blacklist_menu")
async def blacklist_menu_callback(callback: CallbackQuery) -> None:
    if not await _ensure_subscription(callback.from_user.id):
        await callback.message.edit_text(SEARCH_LOCKED_TEXT, reply_markup=_subscription_keyboard(False))
        await _safe_callback_answer(callback)
        return

    await _show_blacklist_menu(callback)


@router.callback_query(F.data == "blacklist_add_word")
async def blacklist_add_word_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_subscription(callback.from_user.id):
        await callback.message.edit_text(SEARCH_LOCKED_TEXT, reply_markup=_subscription_keyboard(False))
        await _safe_callback_answer(callback)
        return

    await state.set_state(BotStates.waiting_for_keyword)
    await state.update_data(target_collection="blacklist")
    await callback.message.edit_text("Введите слово для чёрного списка:")
    await _safe_callback_answer(callback)


@router.callback_query(F.data == "blacklist_delete_menu")
async def blacklist_delete_menu_callback(callback: CallbackQuery) -> None:
    if not await _ensure_subscription(callback.from_user.id):
        await callback.message.edit_text(SEARCH_LOCKED_TEXT, reply_markup=_subscription_keyboard(False))
        await _safe_callback_answer(callback)
        return

    await _show_blacklist_delete_menu(callback)


@router.callback_query(F.data.startswith("blacklist_remove_"))
async def blacklist_remove_callback(callback: CallbackQuery) -> None:
    if not await _ensure_subscription(callback.from_user.id):
        await callback.message.edit_text(SEARCH_LOCKED_TEXT, reply_markup=_subscription_keyboard(False))
        await _safe_callback_answer(callback)
        return

    word = callback.data.removeprefix("blacklist_remove_").strip()
    if not word:
        await _safe_callback_answer(callback)
        return

    words = await _remove_user_blacklist_word(callback.from_user.id, word)
    await callback.message.edit_text(
        _list_title("⛔ Чёрный список", words),
        reply_markup=_delete_list_keyboard(words, "blacklist_remove_", "blacklist_menu"),
    )
    await _safe_callback_answer(callback)


@router.callback_query(F.data == "priority_menu")
async def priority_menu_callback(callback: CallbackQuery) -> None:
    if not await _ensure_subscription(callback.from_user.id):
        await callback.message.edit_text(SEARCH_LOCKED_TEXT, reply_markup=_subscription_keyboard(False))
        await _safe_callback_answer(callback)
        return

    await _show_priority_menu(callback)


@router.callback_query(F.data == "priority_add_word")
async def priority_add_word_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_subscription(callback.from_user.id):
        await callback.message.edit_text(SEARCH_LOCKED_TEXT, reply_markup=_subscription_keyboard(False))
        await _safe_callback_answer(callback)
        return

    await state.set_state(BotStates.waiting_for_keyword)
    await state.update_data(target_collection="priority")
    await callback.message.edit_text("Введите слово для приоритетов:")
    await _safe_callback_answer(callback)


@router.callback_query(F.data == "priority_delete_menu")
async def priority_delete_menu_callback(callback: CallbackQuery) -> None:
    if not await _ensure_subscription(callback.from_user.id):
        await callback.message.edit_text(SEARCH_LOCKED_TEXT, reply_markup=_subscription_keyboard(False))
        await _safe_callback_answer(callback)
        return

    await _show_priority_delete_menu(callback)


@router.callback_query(F.data.startswith("priority_remove_"))
async def priority_remove_callback(callback: CallbackQuery) -> None:
    if not await _ensure_subscription(callback.from_user.id):
        await callback.message.edit_text(SEARCH_LOCKED_TEXT, reply_markup=_subscription_keyboard(False))
        await _safe_callback_answer(callback)
        return

    word = callback.data.removeprefix("priority_remove_").strip()
    if not word:
        await _safe_callback_answer(callback)
        return

    priority = await _load_user_priority(callback.from_user.id)
    normalized_word = _normalize_word(word)
    priority["words"] = [item for item in priority["words"] if item != normalized_word]
    priority = await _save_user_priority(callback.from_user.id, priority)
    await callback.message.edit_text(
        _list_title("🎯 Приоритетные слова", priority["words"]),
        reply_markup=_delete_list_keyboard(priority["words"], "priority_remove_", "priority_menu"),
    )
    await _safe_callback_answer(callback)


@router.callback_query(F.data == "priority_thresholds")
async def priority_thresholds_callback(callback: CallbackQuery) -> None:
    if not await _require_owner(callback):
        return

    await _show_priority_thresholds(callback)


@router.callback_query(F.data == "edit_priority_high")
async def edit_priority_high_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_owner(callback):
        return

    await state.set_state(BotStates.waiting_for_min_price)
    await state.update_data(target_collection="priority", target_field="min_price_red")
    await callback.message.edit_text("Введите новый верхний порог для 🔴:")
    await _safe_callback_answer(callback)


@router.callback_query(F.data == "edit_priority_medium")
async def edit_priority_medium_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_owner(callback):
        return

    await state.set_state(BotStates.waiting_for_min_price)
    await state.update_data(target_collection="priority", target_field="min_price_yellow")
    await callback.message.edit_text("Введите новый средний порог для 🟡:")
    await _safe_callback_answer(callback)


@router.callback_query(F.data == "platforms_menu")
async def platforms_menu_callback(callback: CallbackQuery) -> None:
    if not await _require_owner(callback):
        return

    settings = await settings_manager.load_settings()
    stats = await get_project_stats(PLATFORM_LABELS)
    await callback.message.edit_text("Площадки", reply_markup=_platforms_menu_keyboard(settings, stats["platform_today_counts"]))
    await _safe_callback_answer(callback)


@router.callback_query(F.data.startswith("toggle_platform_"))
async def toggle_platform_callback(callback: CallbackQuery) -> None:
    if not await _require_owner(callback):
        return

    platform = callback.data.removeprefix("toggle_platform_").strip()
    if not platform:
        await _safe_callback_answer(callback)
        return

    await settings_manager.toggle_platform(platform)
    settings = await settings_manager.load_settings()
    stats = await get_project_stats(PLATFORM_LABELS)
    await callback.message.edit_text("Площадки", reply_markup=_platforms_menu_keyboard(settings, stats["platform_today_counts"]))
    await _safe_callback_answer(callback, "Сохранено")


@router.callback_query(F.data == "set_min_price")
async def set_min_price_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_owner(callback):
        return

    await state.set_state(BotStates.waiting_for_min_price)
    await state.update_data(target_collection="settings", target_field="min_price")
    await callback.message.edit_text("Введите новую минимальную цену:")
    await _safe_callback_answer(callback)


@router.callback_query(F.data == "add_keyword")
async def add_keyword_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_subscription(callback.from_user.id):
        await callback.message.edit_text(SEARCH_LOCKED_TEXT, reply_markup=_subscription_keyboard(False))
        await _safe_callback_answer(callback)
        return

    await state.set_state(BotStates.waiting_for_keyword)
    await state.update_data(target_collection="keywords")
    await callback.message.edit_text("Введите ключевое слово:")
    await _safe_callback_answer(callback)


@router.message(StateFilter(BotStates.waiting_for_keyword))
async def add_keyword_state_handler(message: Message, state: FSMContext) -> None:
    if not await _ensure_subscription(message.from_user.id):
        await state.clear()
        await message.answer(SEARCH_LOCKED_TEXT, reply_markup=_subscription_keyboard(False))
        return

    keyword = (message.text or "").strip()
    validated_keyword = _validate_keyword(keyword)
    if validated_keyword is None:
        await message.answer("Введите слово до 50 символов: буквы, цифры, пробелы и дефисы.")
        return

    data = await state.get_data()
    target_collection = str(data.get("target_collection", "keywords"))
    user_id = message.from_user.id

    if target_collection == "blacklist":
        words = await _add_user_blacklist_word(user_id, validated_keyword)
        await state.clear()
        await message.answer(
            f"✅ Слово '{validated_keyword}' добавлено в чёрный список",
            reply_markup=_delete_list_keyboard(words, "blacklist_remove_", "blacklist_menu"),
        )
        return

    if target_collection == "priority":
        priority = await _load_user_priority(user_id)
        if validated_keyword not in priority["words"]:
            priority["words"].append(validated_keyword)
        priority = await _save_user_priority(user_id, priority)
        await state.clear()
        await message.answer(
            f"✅ Слово '{validated_keyword}' добавлено в приоритетные",
            reply_markup=_priority_keyboard(priority),
        )
        return

    keywords = await _add_user_keyword(user_id, validated_keyword)
    await state.clear()
    await message.answer(
        f"✅ Слово '{validated_keyword}' добавлено",
        reply_markup=_keywords_menu_keyboard(keywords),
    )


@router.message(StateFilter(BotStates.waiting_for_min_price))
async def set_min_price_state_handler(message: Message, state: FSMContext) -> None:
    if not await _require_owner(message):
        await state.clear()
        return

    raw_value = (message.text or "").strip()
    value = _validate_min_price(raw_value)
    if value is None:
        await message.answer("Введите число от 0 до 1000000.")
        return

    data = await state.get_data()
    target_collection = str(data.get("target_collection", "settings"))
    target_field = str(data.get("target_field", "min_price"))

    if target_collection == "priority":
        priority = await _load_priority()
        priority[target_field] = value
        priority = await _save_priority(priority)
        await state.clear()
        await message.answer(
            f"✅ Порог обновлён: {value}₽",
            reply_markup=_priority_thresholds_keyboard(),
        )
        return

    await settings_manager.update_min_price(value)
    await state.clear()

    settings = await settings_manager.load_settings()
    await message.answer(
        f"✅ Мин. цена установлена: {int(settings['min_price'])}₽",
        reply_markup=_platforms_menu_keyboard(settings),
    )


@router.callback_query(F.data.startswith("remove_"))
async def remove_keyword_callback(callback: CallbackQuery) -> None:
    if not await _ensure_subscription(callback.from_user.id):
        await callback.message.edit_text(SEARCH_LOCKED_TEXT, reply_markup=_subscription_keyboard(False))
        await _safe_callback_answer(callback)
        return

    keyword = callback.data.removeprefix("remove_").strip()
    if not keyword:
        await _safe_callback_answer(callback)
        return

    keywords = await _remove_user_keyword(callback.from_user.id, keyword)
    await callback.message.edit_text(
        f"✅ Слово '{keyword}' удалено",
        reply_markup=_keywords_menu_keyboard(keywords),
    )
    await _safe_callback_answer(callback)


@router.callback_query(F.data == "search")
async def search_callback(callback: CallbackQuery) -> None:
    if is_rate_limited(callback.from_user.id, "search"):
        await callback.message.answer("⏳ Слишком частые запросы. Подожди минуту.")
        await _safe_callback_answer(callback)
        return

    if not await _ensure_subscription(callback.from_user.id):
        await callback.message.edit_text(
            SEARCH_LOCKED_TEXT,
            reply_markup=_subscription_keyboard(False),
        )
        await _safe_callback_answer(callback)
        return

    await callback.message.edit_text("⏳ Парсинг запущен...")
    kwork_count, fl_count, freelanceru_count, weblancer_count, youdo_count, pchel_count, freelancehunt_count, sent_count, status_parts = await _run_search(callback.message)
    status_line = " | ".join(status_parts)
    result_text = (
        f"✅ Kwork: {kwork_count} | FL.ru: {fl_count} | Freelance.ru: {freelanceru_count} | Weblancer: {weblancer_count} | YouDo: {youdo_count} | Pchel: {pchel_count} | FreelanceHunt: {freelancehunt_count} | Отправлено: {sent_count}"
    )
    if status_line:
        result_text = f"{status_line}\n{result_text}"
    await callback.message.edit_text(result_text)
    await _safe_callback_answer(callback)


@router.message(Command("search"))
async def search_command_handler(message: Message) -> None:
    if is_rate_limited(message.from_user.id, "search"):
        await message.answer("⏳ Слишком частые запросы. Подожди минуту.")
        return

    if not await _ensure_subscription(message.from_user.id):
        await message.answer(SEARCH_LOCKED_TEXT, reply_markup=_subscription_keyboard(False))
        return

    progress_message = await message.answer("⏳ Парсинг запущен...")
    kwork_count, fl_count, freelanceru_count, weblancer_count, youdo_count, pchel_count, freelancehunt_count, sent_count, status_parts = await _run_search(progress_message)
    status_line = " | ".join(status_parts)
    result_text = (
        f"✅ Kwork: {kwork_count} | FL.ru: {fl_count} | Freelance.ru: {freelanceru_count} | Weblancer: {weblancer_count} | YouDo: {youdo_count} | Pchel: {pchel_count} | FreelanceHunt: {freelancehunt_count} | Отправлено: {sent_count}"
    )
    if status_line:
        result_text = f"{status_line}\n{result_text}"
    await message.answer(result_text)


@router.callback_query(F.data == "subscribe_menu")
async def subscribe_menu_callback(callback: CallbackQuery) -> None:
    await _show_subscription(callback)


# ============================================================================
# Telegram-источники (управление списком чатов/каналов для парсинга)
# ============================================================================

_TG_LINK_RE = re.compile(
    r"^(?:https?://)?t\.me/(?:joinchat/|\+)?(?P<rest>[A-Za-z0-9_+\-]+)/?$"
)


def _parse_source_input(raw: str) -> tuple[str, str | None]:
    """Возвращает ('public', username) или ('invite', hash) или ('invalid', None)."""
    text = raw.strip()
    if not text:
        return "invalid", None

    if text.startswith("@"):
        return "public", text[1:]

    match = _TG_LINK_RE.match(text)
    if match:
        rest = match.group("rest")
        # invite: t.me/+XXX или t.me/joinchat/XXX
        if "joinchat/" in text or "/+" in text or text.startswith(("+", "https://t.me/+")):
            return "invite", rest.lstrip("+")
        return "public", rest

    if re.fullmatch(r"[A-Za-z0-9_]{4,32}", text):
        return "public", text

    return "invalid", None


def _detect_entity_type(entity: Any) -> str:
    if getattr(entity, "broadcast", False):
        return "channel"
    return "chat"


def _tgsrc_format_row(source: dict[str, Any]) -> str:
    mark = "✅" if source.get("enabled") else "❌"
    handle = source.get("username") or f"id={source.get('chat_id')}"
    return f"{mark} {source.get('title', '?')} ({source.get('type', '?')}) — {handle}"


def _tgsrc_list_keyboard(sources: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    for source in sources:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_tgsrc_format_row(source),
                    callback_data=f"tgsrc:src:{source['chat_id']}",
                )
            ]
        )
    keyboard.append([InlineKeyboardButton(text="➕ Добавить источник", callback_data="tgsrc:add")])
    keyboard.append([InlineKeyboardButton(text="🔄 Обновить список", callback_data="tgsrc:menu")])
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _tgsrc_source_keyboard(source: dict[str, Any]) -> InlineKeyboardMarkup:
    toggle_text = "❌ Выключить" if source.get("enabled") else "🔛 Включить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data=f"tgsrc:toggle:{source['chat_id']}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"tgsrc:askdel:{source['chat_id']}")],
            [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="tgsrc:menu")],
        ]
    )


def _tgsrc_confirm_delete_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data=f"tgsrc:del:{chat_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"tgsrc:src:{chat_id}"),
            ]
        ]
    )


async def _show_tgsrc_menu(target: Message | CallbackQuery) -> None:
    sources = await telegram_sources_manager.load_sources()
    if not sources:
        text = (
            "📡 Telegram-источники\n\n"
            "Список пуст. Добавь чат или канал, в который ты уже вступил с аккаунта-парсера."
        )
    else:
        text = f"📡 Telegram-источники\n\nВсего: {len(sources)}"

    keyboard = _tgsrc_list_keyboard(sources)
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=keyboard)
        except Exception:
            await target.message.answer(text, reply_markup=keyboard)
        await _safe_callback_answer(target)
        return
    await target.answer(text, reply_markup=keyboard)


def _safe_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@router.callback_query(F.data == "tgsrc:menu")
async def tgsrc_menu_callback(callback: CallbackQuery) -> None:
    if not await _require_owner(callback):
        return
    await _show_tgsrc_menu(callback)


@router.callback_query(F.data.startswith("tgsrc:src:"))
async def tgsrc_show_source_callback(callback: CallbackQuery) -> None:
    if not await _require_owner(callback):
        return

    chat_id = _safe_int(callback.data.removeprefix("tgsrc:src:"))
    source = await telegram_sources_manager.find_by_chat_id(chat_id) if chat_id is not None else None
    if not source:
        await _safe_callback_answer(callback, "Источник не найден", show_alert=True)
        await _show_tgsrc_menu(callback)
        return

    text = (
        f"📡 {source['title']}\n\n"
        f"Тип: {source['type']}\n"
        f"ID: {source['chat_id']}\n"
        f"Username: {source.get('username') or '—'}\n"
        f"Статус: {'✅ включён' if source['enabled'] else '❌ выключен'}\n"
        f"Приватный: {'да' if source.get('is_private') else 'нет'}"
    )
    await callback.message.edit_text(text, reply_markup=_tgsrc_source_keyboard(source))
    await _safe_callback_answer(callback)


@router.callback_query(F.data.startswith("tgsrc:toggle:"))
async def tgsrc_toggle_callback(callback: CallbackQuery) -> None:
    if not await _require_owner(callback):
        return

    chat_id = _safe_int(callback.data.removeprefix("tgsrc:toggle:"))
    if chat_id is None:
        await _safe_callback_answer(callback)
        return

    new_state = await telegram_sources_manager.toggle_source(chat_id)
    if new_state is None:
        await _safe_callback_answer(callback, "Источник не найден", show_alert=True)
    else:
        await _safe_callback_answer(callback, "Включён" if new_state else "Выключен")
    await _show_tgsrc_menu(callback)


@router.callback_query(F.data.startswith("tgsrc:askdel:"))
async def tgsrc_ask_delete_callback(callback: CallbackQuery) -> None:
    if not await _require_owner(callback):
        return

    chat_id = _safe_int(callback.data.removeprefix("tgsrc:askdel:"))
    source = await telegram_sources_manager.find_by_chat_id(chat_id) if chat_id is not None else None
    if not source:
        await _safe_callback_answer(callback, "Источник не найден", show_alert=True)
        return

    text = (
        f"Удалить источник?\n\n"
        f"📡 {source['title']} ({source.get('username') or source['chat_id']})\n\n"
        f"⚠️ Из чата автоматически НЕ выйду — выйди вручную, если нужно."
    )
    await callback.message.edit_text(text, reply_markup=_tgsrc_confirm_delete_keyboard(chat_id))
    await _safe_callback_answer(callback)


@router.callback_query(F.data.startswith("tgsrc:del:"))
async def tgsrc_delete_callback(callback: CallbackQuery) -> None:
    if not await _require_owner(callback):
        return

    chat_id = _safe_int(callback.data.removeprefix("tgsrc:del:"))
    if chat_id is None:
        await _safe_callback_answer(callback)
        return

    removed = await telegram_sources_manager.remove_source(chat_id)
    await _safe_callback_answer(callback, "Удалён" if removed else "Не найден")
    await _show_tgsrc_menu(callback)


@router.callback_query(F.data == "tgsrc:add")
async def tgsrc_add_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_owner(callback):
        return

    await state.set_state(AddTelegramSource.waiting_for_link)
    await callback.message.edit_text(
        "Пришли ссылку или username чата/канала.\n\n"
        "Примеры:\n"
        "• @chatname\n"
        "• https://t.me/chatname\n"
        "• https://t.me/+xxxxxxxxxxx (для приватных)\n\n"
        "⚠️ Ты должен быть уже вступившим в этот чат с аккаунта-парсера.\n"
        "Бот сам никуда не вступает.",
    )
    await _safe_callback_answer(callback)


@router.message(StateFilter(AddTelegramSource.waiting_for_link))
async def tgsrc_add_state_handler(message: Message, state: FSMContext) -> None:
    if not await _require_owner(message):
        await state.clear()
        return

    raw = (message.text or "").strip()
    kind, value = _parse_source_input(raw)
    if kind == "invalid" or not value:
        await message.answer("❌ Не понял формат. Пришли @username, t.me/... или invite-ссылку.")
        return

    if not await is_ready():
        await state.clear()
        await message.answer(
            "❌ Telethon-клиент не готов. Запусти `python auth_telethon.py` "
            "на сервере и проверь TG_PARSER_* в .env."
        )
        return

    client = get_client()
    entity = None
    is_private = False
    username_for_storage = ""

    try:
        if kind == "public":
            entity = await client.get_entity(value)
            username_for_storage = f"@{getattr(entity, 'username', None) or value}"
        else:  # invite
            from telethon.tl.functions.messages import CheckChatInviteRequest
            from telethon.tl.types import ChatInviteAlready

            invite_info = await client(CheckChatInviteRequest(value))
            if isinstance(invite_info, ChatInviteAlready):
                entity = invite_info.chat
                username_for_storage = raw  # сохраняем как пользователь прислал
                is_private = True
            else:
                await state.clear()
                await message.answer(
                    "❌ Ты ещё не вступил в этот приватный чат с аккаунта-парсера.\n"
                    "Сначала вступи вручную, потом добавь сюда."
                )
                return
    except Exception as exc:
        from telethon.errors import (
            ChannelPrivateError,
            FloodWaitError,
            InviteHashExpiredError,
            InviteHashInvalidError,
            UsernameNotOccupiedError,
        )

        await state.clear()
        if isinstance(exc, FloodWaitError):
            await message.answer(
                f"⏳ Telegram временно ограничил запросы, попробуй через {exc.seconds} сек."
            )
        elif isinstance(exc, (ChannelPrivateError, UsernameNotOccupiedError, ValueError)):
            await message.answer(
                "❌ Не могу получить доступ к этому чату.\n"
                "Сначала вступи в него вручную с аккаунта-парсера, потом добавь сюда."
            )
        elif isinstance(exc, (InviteHashExpiredError, InviteHashInvalidError)):
            await message.answer("❌ Invite-ссылка протухла или невалидна. Попроси новую.")
        else:
            logger.exception("Ошибка при добавлении Telegram-источника")
            await message.answer(f"❌ Не удалось добавить: {exc}")
        return

    chat_id = getattr(entity, "id", None)
    if chat_id is None:
        await state.clear()
        await message.answer("❌ Не удалось определить ID чата")
        return

    # Telethon отдаёт positive id для каналов/мегагрупп; нормализуем к -100... для совместимости
    if chat_id > 0 and (getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False)):
        chat_id = int(f"-100{chat_id}")

    type_ = _detect_entity_type(entity)
    title = getattr(entity, "title", None) or username_for_storage or "Источник"
    if not username_for_storage:
        username_for_storage = f"private:{chat_id}"

    record, was_added = await telegram_sources_manager.add_source(
        username=username_for_storage,
        chat_id=chat_id,
        type_=type_,
        title=title,
        is_private=is_private,
        enabled=True,
    )
    await state.clear()

    if not was_added:
        status = "включён" if record["enabled"] else "выключен"
        await message.answer(
            f"ℹ️ Источник уже добавлен: {record['title']}\n"
            f"Текущий статус: {status}"
        )
    else:
        await message.answer(
            f"✅ Источник добавлен: {record['title']} ({record['type']})"
        )
    await _show_tgsrc_menu(message)


# ============================================================================
# конец Telegram-источников
# ============================================================================


@router.callback_query()
async def unknown_callback_handler(callback: CallbackQuery) -> None:
    logger.warning("Unknown callback_data from user {}: {}", callback.from_user.id, _safe_callback_value(callback))
    await _safe_callback_answer(callback)


@router.errors()
async def errors_handler(event: ErrorEvent) -> bool:
    logger.exception("Unhandled router error", exc_info=event.exception)

    update = event.update
    callback = getattr(update, "callback_query", None)
    if callback and callback.message:
        await callback.message.answer("⚠️ Произошла ошибка. Попробуй позже.")
        await _safe_callback_answer(callback)
        return True

    message = getattr(update, "message", None)
    if message:
        await message.answer("⚠️ Произошла ошибка. Попробуй позже.")
        return True

    return True
