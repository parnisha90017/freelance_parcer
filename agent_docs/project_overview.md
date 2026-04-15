# Project Overview

## Текущий статус
- День 1 выполнен.
- День 2 выполнен.
- День 3 выполнен.
- День 4 выполнен.
- Проект работает 24/7 на VPS.
- Есть отдельная тестовая среда `test_env/test_bot.py`.
- Telegram channels parser в процессе доводки.

## Рабочие парсеры
- Kwork — aiohttp + BeautifulSoup.
- FL.ru — aiohttp + BeautifulSoup.
- Freelance.ru — aiohttp + BeautifulSoup.
- Weblancer — aiohttp + BeautifulSoup.
- YouDo — через JSON API, без браузера.
- Pchel.net — aiohttp + BeautifulSoup.
- Freelancehunt — aiohttp + BeautifulSoup.

## Что уже реализовано
- Парсер Kwork через `kworker` API.
- Парсер FL.ru.
- Парсер Freelance.ru.
- Парсер Weblancer.
- Парсер YouDo через JSON API.
- Парсер Pchel.net.
- Парсер Freelancehunt.
- Фильтрация по ключевым словам.
- Фильтр по минимальной цене.
- JSON-хранилище настроек и keywords.
- Telegram-бот с inline-кнопками и FSM.
- Автозапуск парсинга по расписанию каждые 10 минут.
- SQLite для хранения проектов и антидубликатов.
- Retry при сетевых ошибках Telegram.
- Управление площадками из бота.
- Groq AI-оценка заказов и генерация откликов.
- Отчёт парсинга после каждого автоматического цикла.

## Что сейчас важно
- Scheduler должен изолировать ошибки всех источников.
- Если один источник падает, остальные продолжают работать.
- Тестовая среда должна оставаться отдельной от основного бота.
- Telegram channels parser остаётся отдельной доработкой.
- Основной запуск идёт на VPS.

## Как работает проект
1. Scheduler запускает парсинг по расписанию.
2. Три парсера собирают объявления отдельно.
3. Фильтры отбирают релевантные проекты.
4. Дубли отсеиваются через БД.
5. AI оценивает заказ и помогает с откликом.
6. Новые заказы уходят в Telegram.

## Компоненты и статус
- `bot/` — готов, содержит меню и обработчики.
- `services/` — настройки, AI, scheduler.
- `parsers/` — Kwork, FL.ru, Freelance.ru.
- `filters/` — keywords и price.
- `notifications/` — отправка в Telegram и кнопки.
- `database/` — SQLite и антидубликаты.
- `data/` — keywords и settings.
- `test_env/` — отдельный тестовый бот.
- `logs/` — основной и тестовый логи.

## Технологии
- Python 3.11
- aiogram 3.x
- requests
- BeautifulSoup4
- loguru
- SQLite
- SQLAlchemy async
- APScheduler
- kworker
- Groq API

## Следующий шаг
- Поддерживать стабильный 24/7 режим.
- Следить за логами и ошибками источников.
- Расширять парсеры только без нарушения текущего потока.
