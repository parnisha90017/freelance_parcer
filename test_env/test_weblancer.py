from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from config import config
from filters import KeywordFilter, PriceFilter
from parsers.weblancer import WeblancerParser
from services.ai_helper import AIHelper
from services.settings_manager import settings_manager


def _format_telegram_preview(project: dict[str, str], evaluation: dict[str, object]) -> str:
    title = str(project.get("title", "")).strip()
    price = str(project.get("price") or project.get("budget") or "")
    description = str(project.get("description", "")).strip()
    link = str(project.get("link", "")).strip()
    score = int(evaluation.get("score", 0))
    difficulty = str(evaluation.get("difficulty", "средняя"))
    time_estimate = str(evaluation.get("time_estimate", "1-2 дня"))
    short_description = description[:300]

    return (
        "Новый заказ\n\n"
        f"Название: {title}\n"
        f"Бюджет: {price}\n"
        f"Подходит: {score}%\n"
        f"Сложность: {difficulty}\n"
        f"Время: {time_estimate}\n\n"
        f"{short_description}\n\n"
        f"Ссылка: {link}"
    )


async def main() -> None:
    parser = WeblancerParser(max_pages=2)
    projects = await parser.parse()
    print(f"Weblancer: found {len(projects)} projects")

    if not projects:
        print("Site unavailable or no projects found")
        return

    settings = await settings_manager.load_settings()
    keyword_filter = KeywordFilter(keywords_path=config.KEYWORDS_JSON_PATH)
    filtered_projects = await keyword_filter.filter(projects)
    price_filter = PriceFilter(min_price=int(settings["min_price"]))
    filtered_projects = await price_filter.filter(filtered_projects)

    print(f"After filters: {len(filtered_projects)} projects")

    if not filtered_projects:
        print("No projects left after filters")
        return

    ai_helper = AIHelper(api_key=config.OPENROUTER_API_KEY, model=config.AI_MODEL)
    sample_project = filtered_projects[0]
    evaluation = await ai_helper.evaluate_project(sample_project)

    print("\nTelegram preview example:\n")
    print(_format_telegram_preview(sample_project, evaluation))


if __name__ == "__main__":
    asyncio.run(main())
