# Freelance Parser Bot

Telegram-бот для автоматического парсинга заказов с фриланс-площадок с AI-фильтрацией.

## Возможности
- Парсинг 6 площадок (Kwork, FL.ru, Freelance.ru, Weblancer, YouDo, Pchel)
- AI-оценка релевантности через Groq
- Фильтрация по ключевым словам и чёрному списку
- Система подписки (Telegram Stars + ЮКасса)
- Автопарсинг каждые 10 минут
- Уведомления в Telegram

## Стек
- Python 3.10+
- aiogram 3
- SQLAlchemy (async)
- APScheduler
- aiohttp, BeautifulSoup4
- Groq AI API
- YooKassa API

## Установка
1. Клонировать репо
2. pip install -r requirements.txt
3. Скопировать .env.example в .env и заполнить
4. python main.py

## Структура
- parsers/ — парсеры площадок
- bot/ — Telegram бот
- services/ — бизнес-логика
- database/ — модели БД
- filters/ — фильтрация
- notifications/ — рассылка
