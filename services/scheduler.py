from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from config import config
from database.db import init_db, is_duplicate, save_project
from filters import KeywordFilter, PriceFilter
from notifications import TelegramNotifier
from parsers.fl import FLParser
from parsers.freelancehunt import FreelanceHuntParser
from parsers.freelanceru import FreelanceRuParser
from parsers.kwork import KworkParser
from parsers.telegram_chats import TelegramChatsParser
from parsers.weblancer import WeblancerParser
from parsers.pchel import PchelParser
from parsers.youdo import YouDoParser
from services.ai_helper import AIHelper
from services.settings_manager import settings_manager
from services.subscription_manager import subscription_manager

BLACKLIST_PATH = Path(config.KEYWORDS_JSON_PATH).parent / "blacklist.json"


@dataclass(slots=True)
class ParserScheduler:
    scheduler: AsyncIOScheduler = field(default_factory=AsyncIOScheduler)
    ai_helper: AIHelper = field(
        default_factory=lambda: AIHelper(api_key=config.OPENROUTER_API_KEY, model=config.AI_MODEL)
    )
    auto_parsing_enabled: bool = False
    last_run_at: datetime | None = None

    def __post_init__(self) -> None:
        self.scheduler.add_job(
            self.parse_and_notify,
            "interval",
            minutes=10,
            id="parse_and_notify",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )

    async def start(self) -> None:
        await init_db()
        self.scheduler.start()

    async def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)

    def enable_auto_parsing(self) -> None:
        self.auto_parsing_enabled = True

    def disable_auto_parsing(self) -> None:
        self.auto_parsing_enabled = False

    def toggle_auto_parsing(self) -> bool:
        self.auto_parsing_enabled = not self.auto_parsing_enabled
        return self.auto_parsing_enabled

    def is_auto_parsing_enabled(self) -> bool:
        return self.auto_parsing_enabled

    def get_last_run_text(self) -> str:
        if self.last_run_at is None:
            return "—"
        return self.last_run_at.strftime("%H:%M")

    async def parse_and_notify(self) -> None:
        if not self.auto_parsing_enabled:
            logger.info("Автопарсинг выключен, цикл пропущен")
            return

        self.last_run_at = datetime.utcnow()
        logger.info("Парсинг запущен")
        deactivated_count = await subscription_manager.check_and_deactivate_expired()
        if deactivated_count:
            logger.info("Деактивировано истёкших подписок: {}", deactivated_count)

        subscriber_ids = await subscription_manager.get_active_subscriber_ids()
        if config.OWNER_ID not in subscriber_ids:
            subscriber_ids.append(config.OWNER_ID)

        settings = await settings_manager.load_settings()
        projects: list[dict[str, str]] = []
        kwork_count = 0
        fl_count = 0
        freelanceru_count = 0
        weblancer_count = 0
        youdo_count = 0
        pchel_count = 0
        freelancehunt_count = 0
        telegram_count = 0

        self.ai_helper.skip_ai_for_cycle = False
        self.ai_helper.reset_cycle_state()

        async def log_parser_stats(source: str, source_projects: list[dict[str, str]]) -> None:
            raw_count = len(source_projects)
            new_count = 0
            seen_links: set[str] = set()

            for project in source_projects:
                link = str(project.get("link", "")).strip()
                if not link or link in seen_links:
                    continue
                seen_links.add(link)
                if not await is_duplicate(link):
                    new_count += 1

            logger.info("{}: найдено {} сырых, {} новых (после дедупликации)", source, raw_count, new_count)

        if settings["kwork_enabled"]:
            kwork_parser = KworkParser(
                login=config.KWORK_LOGIN,
                password=config.KWORK_PASSWORD,
                phone_last=config.KWORK_PHONE_LAST,
            )
            try:
                kwork_projects = await kwork_parser.parse()
                kwork_count = len(kwork_projects)
                await log_parser_stats("Kwork", kwork_projects)
                projects.extend(kwork_projects)
            except Exception as e:
                logger.warning("Kwork: ошибка, пропускаем: {}", e)
            finally:
                await kwork_parser.close()

        if settings["fl_enabled"]:
            fl_parser = FLParser(categories=config.FL_CATEGORIES)
            try:
                fl_projects = await fl_parser.parse()
                fl_count = len(fl_projects)
                await log_parser_stats("FL.ru", fl_projects)
                projects.extend(fl_projects)
            except Exception as e:
                logger.warning("FL.ru: ошибка, пропускаем: {}", e)
            finally:
                await fl_parser.close()

        if settings["freelanceru_enabled"]:
            freelanceru_parser = FreelanceRuParser()
            try:
                freelanceru_projects = await freelanceru_parser.parse()
                freelanceru_count = len(freelanceru_projects)
                await log_parser_stats("Freelance.ru", freelanceru_projects)
                projects.extend(freelanceru_projects)
            except Exception as e:
                logger.warning("Freelance.ru: ошибка, пропускаем: {}", e)
            finally:
                await freelanceru_parser.close()

        if settings["weblancer_enabled"]:
            weblancer_parser = WeblancerParser()
            try:
                weblancer_projects = await weblancer_parser.parse()
                weblancer_count = len(weblancer_projects)
                await log_parser_stats("Weblancer", weblancer_projects)
                projects.extend(weblancer_projects)
            except Exception as e:
                logger.warning("Weblancer: ошибка, пропускаем: {}", e)
            finally:
                await weblancer_parser.close()

        if settings["youdo_enabled"]:
            youdo_parser = YouDoParser()
            try:
                youdo_projects = await youdo_parser.parse()
                youdo_count = len(youdo_projects)
                await log_parser_stats("YouDo", youdo_projects)
                projects.extend(youdo_projects)
            except Exception as e:
                logger.warning("YouDo: ошибка, пропускаем: {}", e)
            finally:
                await youdo_parser.close()

        if settings.get("pchel_enabled", False):
            pchel_parser = PchelParser()
            try:
                pchel_projects = await pchel_parser.parse()
                pchel_count = len(pchel_projects)
                await log_parser_stats("Pchel", pchel_projects)
                projects.extend(pchel_projects)
            except Exception as e:
                logger.warning("Pchel: ошибка, пропускаем: {}", e)
            finally:
                await pchel_parser.close()

        if settings.get("freelancehunt_enabled", False):
            freelancehunt_parser = FreelanceHuntParser()
            try:
                freelancehunt_projects = await freelancehunt_parser.parse()
                freelancehunt_count = len(freelancehunt_projects)
                await log_parser_stats("FreelanceHunt", freelancehunt_projects)
                projects.extend(freelancehunt_projects)
            except Exception as e:
                logger.warning("FreelanceHunt: ошибка, пропускаем: {}", e)
            finally:
                await freelancehunt_parser.close()

        if settings.get("telegram_chats_enabled", False):
            telegram_parser = TelegramChatsParser()
            try:
                telegram_projects = await telegram_parser.parse()
                telegram_count = len(telegram_projects)
                await log_parser_stats("Telegram", telegram_projects)
                projects.extend(telegram_projects)
            except Exception as e:
                logger.warning("Telegram: ошибка, пропускаем: {}", e)
            finally:
                await telegram_parser.close()

        keyword_filter = KeywordFilter(keywords_path=config.KEYWORDS_JSON_PATH)
        filtered_projects = await keyword_filter.filter(projects)
        price_filter = PriceFilter(min_price=int(settings["min_price"]))
        filtered_projects = await price_filter.filter(filtered_projects)

        blacklist = await self._load_blacklist()
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

        logger.info(
            "Kwork: {} | FL.ru: {} | Freelance.ru: {} | Weblancer: {} | YouDo: {} | Pchel: {} | FreelanceHunt: {} | Telegram: {} | После фильтра: {}",
            kwork_count,
            fl_count,
            freelanceru_count,
            weblancer_count,
            youdo_count,
            pchel_count,
            freelancehunt_count,
            telegram_count,
            len(filtered_projects),
        )

        if not projects:
            logger.info("Нет проектов для отчёта")
            return

        sent_count = 0
        duplicate_count = 0
        failed_deliveries = 0
        delivered_messages = 0
        max_new_orders = 30

        try:
            for project in filtered_projects:
                if sent_count >= max_new_orders:
                    logger.info("Достигнут лимит {} новых заказов за цикл", max_new_orders)
                    break

                link = str(project.get("link", "")).strip()
                if not link:
                    continue

                if await is_duplicate(link):
                    duplicate_count += 1
                    logger.info("Дубликат пропущен: {}", link)
                    continue

                if self.ai_helper.skip_ai_for_cycle:
                    logger.info("AI пропущен для оставшихся заказов в этом цикле")
                    enriched_project = {**project, **self.ai_helper._fallback_result()}
                else:
                    try:
                        evaluation = await self.ai_helper.evaluate_project(project)
                        enriched_project = {**project, **evaluation}
                    except Exception:
                        logger.exception("Ошибка AI-оценки")
                        enriched_project = {**project, **self.ai_helper._fallback_result()}

                delivered = False
                for user_id in subscriber_ids:
                    notifier = TelegramNotifier(bot_token=config.TELEGRAM_BOT_TOKEN, user_id=user_id)
                    try:
                        await notifier.send_project(enriched_project)
                        delivered = True
                        delivered_messages += 1
                    except Exception:
                        failed_deliveries += 1
                        logger.exception("Ошибка отправки уведомления пользователю {}", user_id)
                    await asyncio.sleep(1)

                if not delivered:
                    logger.warning("Заказ не доставлен ни одному пользователю: {}", link)
                    continue

                await save_project(link)
                sent_count += 1
                logger.info("Новый заказ отправлен подписчикам: {}", link)
        except Exception:
            logger.exception("Ошибка при автоматическом парсинге")

        total_parsed = (
            kwork_count
            + fl_count
            + freelanceru_count
            + weblancer_count
            + youdo_count
            + pchel_count
            + freelancehunt_count
            + telegram_count
        )
        filtered_count = max(total_parsed - sent_count - duplicate_count, 0)

        logger.info("Рассылка завершена: доставлено {}, ошибок {}", delivered_messages, failed_deliveries)

        report_text = (
            "📊 Парсинг завершён\n\n"
            f"Kwork: {kwork_count} | FL.ru: {fl_count} | Freelance.ru: {freelanceru_count} | "
            f"Weblancer: {weblancer_count} | YouDo: {youdo_count} | Pchel: {pchel_count} | "
            f"FreelanceHunt: {freelancehunt_count} | Telegram: {telegram_count}\n"
            f"👥 Получателей: {len(subscriber_ids)}\n"
            f"📤 Новых отправлено: {sent_count}\n"
            f"✅ Доставлено сообщений: {delivered_messages}\n"
            f"❌ Ошибок доставки: {failed_deliveries}\n"
            f"🚫 Отфильтровано: {filtered_count}\n"
            f"🔄 Дубликатов: {duplicate_count}"
        )
        try:
            owner_notifier = TelegramNotifier(bot_token=config.TELEGRAM_BOT_TOKEN, user_id=config.OWNER_ID)
            await owner_notifier.send_message(report_text)
        except Exception:
            logger.exception("Ошибка отправки отчёта")

    async def _load_blacklist(self) -> list[str]:
        if not BLACKLIST_PATH.exists():
            return []

        try:
            content = BLACKLIST_PATH.read_text(encoding="utf-8")
            if not content.strip():
                return []
            data = json.loads(content)
        except (OSError, json.JSONDecodeError):
            logger.exception("Не удалось загрузить чёрный список")
            return []

        if not isinstance(data, list):
            return []

        normalized: list[str] = []
        seen: set[str] = set()
        for item in data:
            word = str(item).strip().lower()
            if not word or word in seen:
                continue
            seen.add(word)
            normalized.append(word)
        return normalized


parser_scheduler = ParserScheduler()
