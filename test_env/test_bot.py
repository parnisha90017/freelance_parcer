from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.config.SETTINGS_JSON_PATH = "data/test_settings.json"
config.config.DATABASE_URL = "sqlite+aiosqlite:///data/test_projects.db"

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from bot.states import BotStates
from database.db import init_db, is_duplicate, save_project
from filters import KeywordFilter, PriceFilter
from notifications import TelegramNotifier
from notifications.telegram_bot import get_project
from parsers.weblancer import WeblancerParser
from services import KeywordsManager
from services.ai_helper import AIHelper
from services.settings_manager import settings_manager

LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"

router = Router()
keywords_manager = KeywordsManager(Path(config.config.KEYWORDS_JSON_PATH))
notifier = TelegramNotifier(bot_token=config.config.TELEGRAM_BOT_TOKEN, user_id=config.config.TELEGRAM_USER_ID)
ai_helper = AIHelper(api_key=config.config.GROQ_API_KEY, model=config.config.GROQ_MODEL)


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Запустить поиск", callback_data="search")],
            [InlineKeyboardButton(text="📝 Ключевые слова", callback_data="keywords_menu")],
            [InlineKeyboardButton(text="⚙️ Площадки", callback_data="platforms_menu")],
            [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")],
        ]
    )


def _keywords_menu_keyboard(keywords: list[str]) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=f"{keyword} ❌", callback_data=f"remove_{keyword}")]
        for keyword in keywords
    ]
    keyboard.append([InlineKeyboardButton(text="➕ Добавить слово", callback_data="add_keyword")])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _platforms_menu_keyboard(settings: dict[str, bool | int]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=("✅ Kwork" if settings["kwork_enabled"] else "❌ Kwork"),
                    callback_data="toggle_platform_kwork",
                )
            ],
            [
                InlineKeyboardButton(
                    text=("✅ FL.ru" if settings["fl_enabled"] else "❌ FL.ru"),
                    callback_data="toggle_platform_fl",
                )
            ],
            [
                InlineKeyboardButton(
                    text=("✅ Freelance.ru" if settings["freelanceru_enabled"] else "❌ Freelance.ru"),
                    callback_data="toggle_platform_freelanceru",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"💰 Мин. цена: {int(settings['min_price'])}₽",
                    callback_data="set_min_price",
                )
            ],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")],
        ]
    )


async def _show_main_menu(target: Message | CallbackQuery) -> None:
    text = "Главное меню\n\nВыберите действие кнопками ниже."
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=_main_menu_keyboard())
        await target.answer()
        return

    await target.answer(text, reply_markup=_main_menu_keyboard())


async def _show_help(target: Message | CallbackQuery) -> None:
    text = (
        "Помощь\n\n"
        "• 🔍 Запустить поиск — запускает парсинг и фильтрацию\n"
        "• 📝 Ключевые слова — открывает список слов\n"
        "• ⚙️ Площадки — включает и выключает источники\n"
        "• ➕ Добавить слово — добавляет новое ключевое слово\n"
        "• Кнопка ❌ удаляет слово из списка"
    )
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=_main_menu_keyboard())
        await target.answer()
        return

    await target.answer(text, reply_markup=_main_menu_keyboard())


async def _run_search() -> tuple[int, int, int, int, list[str]]:
    settings = await settings_manager.load_settings()
    projects: list[dict[str, str]] = []
    weblancer_count = 0
    status_parts: list[str] = []

    parser = WeblancerParser()
    try:
        weblancer_projects = await parser.parse()
        weblancer_count = len(weblancer_projects)
        projects.extend(weblancer_projects)
    except Exception:
        status_parts.append("⚠️ Weblancer: ошибка")
    finally:
        await parser.close()

    keyword_filter = KeywordFilter(keywords_path=config.config.KEYWORDS_JSON_PATH)
    filtered_projects = await keyword_filter.filter(projects)
    price_filter = PriceFilter(min_price=int(settings["min_price"]))
    filtered_projects = await price_filter.filter(filtered_projects)

    sent_count = 0
    duplicate_count = 0
    for project in filtered_projects:
        link = str(project.get("link", "")).strip()
        if not link:
            continue

        if await is_duplicate(link):
            duplicate_count += 1
            logger.info("Duplicate skipped: {}", link)
            continue

        try:
            await notifier.send_project(project)
        except Exception:
            logger.exception("Notification error")
            continue

        await save_project(link)
        sent_count += 1
        logger.info("New order sent: {}", link)

    return weblancer_count, len(filtered_projects), sent_count, duplicate_count, status_parts


async def _send_ai_response(callback: CallbackQuery, project: dict[str, str]) -> None:
    response_text = await ai_helper.generate_response(project)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📋 Копировать", callback_data="copy_response")]]
    )
    await callback.message.answer(response_text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "copy_response")
async def copy_response_callback(callback: CallbackQuery) -> None:
    if callback.message and callback.message.text:
        await callback.message.answer(callback.message.text)
    await callback.answer()


@router.callback_query(F.data.startswith("respond_"))
async def respond_callback(callback: CallbackQuery) -> None:
    project_id = callback.data.removeprefix("respond_").strip()
    project = get_project(project_id)
    if project is None:
        await callback.answer("Заказ не найден", show_alert=True)
        return

    await callback.answer("Генерирую отклик...")
    await _send_ai_response(callback, project)


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    await _show_main_menu(message)


@router.message(Command("help"))
async def help_command_handler(message: Message) -> None:
    await _show_help(message)


@router.callback_query(F.data == "help")
async def help_callback(callback: CallbackQuery) -> None:
    await _show_help(callback)


@router.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: CallbackQuery) -> None:
    await _show_main_menu(callback)


@router.message(Command("keywords"))
async def keywords_command_handler(message: Message) -> None:
    keywords = await keywords_manager.load_keywords()
    await message.answer("Ключевые слова", reply_markup=_keywords_menu_keyboard(keywords))


@router.callback_query(F.data == "keywords_menu")
async def keywords_menu_callback(callback: CallbackQuery) -> None:
    keywords = await keywords_manager.load_keywords()
    await callback.message.edit_text("Ключевые слова", reply_markup=_keywords_menu_keyboard(keywords))
    await callback.answer()


@router.callback_query(F.data == "platforms_menu")
async def platforms_menu_callback(callback: CallbackQuery) -> None:
    settings = await settings_manager.load_settings()
    await callback.message.edit_text("Площадки", reply_markup=_platforms_menu_keyboard(settings))
    await callback.answer()


@router.callback_query(F.data.startswith("toggle_platform_"))
async def toggle_platform_callback(callback: CallbackQuery) -> None:
    platform = callback.data.removeprefix("toggle_platform_").strip()
    if not platform:
        await callback.answer()
        return

    await settings_manager.toggle_platform(platform)
    settings = await settings_manager.load_settings()
    await callback.message.edit_text("Площадки", reply_markup=_platforms_menu_keyboard(settings))
    await callback.answer("Сохранено")


@router.callback_query(F.data == "set_min_price")
async def set_min_price_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BotStates.waiting_for_min_price)
    await callback.message.edit_text("Введите новую минимальную цену:")
    await callback.answer()


@router.callback_query(F.data == "add_keyword")
async def add_keyword_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BotStates.waiting_for_keyword)
    await callback.message.edit_text("Введите ключевое слово:")
    await callback.answer()


@router.message(StateFilter(BotStates.waiting_for_keyword))
async def add_keyword_state_handler(message: Message, state: FSMContext) -> None:
    keyword = (message.text or "").strip()
    if not keyword:
        await message.answer("Введите ключевое слово:")
        return

    await keywords_manager.add_keyword(keyword)
    await state.clear()

    keywords = await keywords_manager.load_keywords()
    await message.answer(
        f"✅ Слово '{keyword.lower()}' добавлено",
        reply_markup=_keywords_menu_keyboard(keywords),
    )


@router.message(StateFilter(BotStates.waiting_for_min_price))
async def set_min_price_state_handler(message: Message, state: FSMContext) -> None:
    raw_value = (message.text or "").strip()
    try:
        min_price = int(raw_value)
    except ValueError:
        await message.answer("Введите число:")
        return

    await settings_manager.update_min_price(min_price)
    await state.clear()

    settings = await settings_manager.load_settings()
    await message.answer(
        f"✅ Мин. цена установлена: {int(settings['min_price'])}₽",
        reply_markup=_platforms_menu_keyboard(settings),
    )


@router.callback_query(F.data.startswith("remove_"))
async def remove_keyword_callback(callback: CallbackQuery) -> None:
    keyword = callback.data.removeprefix("remove_").strip()
    if not keyword:
        await callback.answer()
        return

    keywords = await keywords_manager.remove_keyword(keyword)
    await callback.message.edit_text(
        f"✅ Слово '{keyword}' удалено",
        reply_markup=_keywords_menu_keyboard(keywords),
    )
    await callback.answer()


@router.callback_query(F.data == "search")
async def search_callback(callback: CallbackQuery) -> None:
    await callback.message.edit_text("⏳ Ищу заказы...")
    weblancer_count, filtered_count, sent_count, duplicate_count, status_parts = await _run_search()
    status_line = " | ".join(status_parts)
    result_text = (
        f"✅ Weblancer: {weblancer_count} | После фильтра: {filtered_count} | "
        f"Отправлено: {sent_count} | Дубликатов: {duplicate_count}"
    )
    if status_line:
        result_text = f"{status_line}\n{result_text}"
    await callback.message.edit_text(result_text)
    await callback.answer()


@router.message(Command("search"))
async def search_command_handler(message: Message) -> None:
    weblancer_count, filtered_count, sent_count, duplicate_count, status_parts = await _run_search()
    status_line = " | ".join(status_parts)
    result_text = (
        f"✅ Weblancer: {weblancer_count} | После фильтра: {filtered_count} | "
        f"Отправлено: {sent_count} | Дубликатов: {duplicate_count}"
    )
    if status_line:
        result_text = f"{status_line}\n{result_text}"
    await message.answer(result_text)


@dataclass(slots=True)
class WeblancerScheduler:
    scheduler: AsyncIOScheduler = field(default_factory=AsyncIOScheduler)
    notifier: TelegramNotifier = field(
        default_factory=lambda: TelegramNotifier(
            bot_token=config.config.TELEGRAM_BOT_TOKEN,
            user_id=config.config.TELEGRAM_USER_ID,
        )
    )

    def __post_init__(self) -> None:
        self.scheduler.add_job(self.parse_and_notify, "interval", minutes=10, id="parse_and_notify")

    async def start(self) -> None:
        await init_db()
        self.scheduler.start()

    async def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)

    async def parse_and_notify(self) -> None:
        logger.info("Weblancer parse started")
        settings = await settings_manager.load_settings()
        parser = WeblancerParser()

        try:
            projects = await parser.parse()
        except Exception as error:
            logger.warning("Weblancer: error, skipping: {}", error)
            projects = []
        finally:
            await parser.close()

        keyword_filter = KeywordFilter(keywords_path=config.config.KEYWORDS_JSON_PATH)
        filtered_projects = await keyword_filter.filter(projects)
        price_filter = PriceFilter(min_price=int(settings["min_price"]))
        filtered_projects = await price_filter.filter(filtered_projects)

        logger.info(
            "Weblancer: {} projects | after filter: {}",
            len(projects),
            len(filtered_projects),
        )

        if not projects:
            logger.info("No projects for report")
            return

        sent_count = 0
        duplicate_count = 0

        try:
            for project in filtered_projects:
                link = str(project.get("link", "")).strip()
                if not link:
                    continue

                if await is_duplicate(link):
                    duplicate_count += 1
                    logger.info("Duplicate skipped: {}", link)
                    continue

                try:
                    await self.notifier.send_project(project)
                except Exception:
                    logger.exception("Notification error")
                    continue

                await save_project(link)
                sent_count += 1
                logger.info("New order sent: {}", link)
        except Exception:
            logger.exception("Error during Weblancer parsing")

        report_text = (
            "📊 Парсинг завершён\n\n"
            f"Weblancer: {len(projects)} проектов\n\n"
            f"После фильтра: {len(filtered_projects)}\n"
            f"Новых отправлено: {sent_count}\n"
            f"Дубликатов: {duplicate_count}"
        )
        try:
            await self.notifier.send_message(report_text)
        except Exception:
            logger.exception("Report sending error")


def setup_logging() -> None:
    logs_dir = Path(__file__).resolve().parent.parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, format=LOG_FORMAT)
    logger.add(logs_dir / "test_parser.log", format=LOG_FORMAT, encoding="utf-8")


async def run_app() -> None:
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)

    scheduler = WeblancerScheduler()
    await scheduler.start()
    logger.info("Test bot started")

    bot = Bot(token=config.config.TELEGRAM_BOT_TOKEN)
    try:
        while True:
            try:
                await dispatcher.start_polling(bot)
                break
            except TelegramNetworkError as error:
                logger.warning("Failed to connect bot: {}", error)
                await asyncio.sleep(10)
    finally:
        await bot.session.close()
        await scheduler.shutdown()


def main() -> None:
    setup_logging()
    asyncio.run(run_app())


if __name__ == "__main__":
    main()
