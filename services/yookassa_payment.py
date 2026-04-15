from __future__ import annotations

from typing import Any
from uuid import uuid4

import requests
from loguru import logger

from config import config

YOOKASSA_PAYMENTS_URL = "https://api.yookassa.ru/v3/payments"
PAYMENT_DESCRIPTION = "Подписка на Freelance Parser — 1 месяц"


def _auth() -> tuple[str, str]:
    return config.YOOKASSA_SHOP_ID.strip(), config.YOOKASSA_SECRET_KEY.strip()


def _return_url() -> str:
    bot_username = config.TELEGRAM_BOT_USERNAME.strip().lstrip("@")
    if not bot_username:
        logger.error("YooKassa config error: TELEGRAM_BOT_USERNAME is empty")
        raise RuntimeError("Telegram bot username is not configured")
    return f"https://t.me/{bot_username}"


def create_payment(user_id: int) -> tuple[str, str]:
    shop_id, secret_key = _auth()
    if not shop_id or not secret_key:
        logger.error(
            "YooKassa config error: empty credentials (shop_id_present={}, secret_key_present={})",
            bool(shop_id),
            bool(secret_key),
        )
        raise RuntimeError("YooKassa credentials are not configured")

    payload = {
        "amount": {
            "value": f"{config.SUBSCRIPTION_PRICE_RUB:.2f}",
            "currency": "RUB",
        },
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": _return_url(),
        },
        "description": PAYMENT_DESCRIPTION,
        "metadata": {
            "user_id": str(user_id),
        },
    }
    headers = {
        "Idempotence-Key": str(uuid4()),
        "Content-Type": "application/json",
    }

    response: requests.Response | None = None
    try:
        response = requests.post(
            YOOKASSA_PAYMENTS_URL,
            json=payload,
            headers=headers,
            auth=(shop_id, secret_key),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException:
        logger.exception("Failed to create YooKassa payment")
        if response is not None:
            logger.error(
                "YooKassa create payment failed: status_code={}, response_text={}",
                response.status_code,
                response.text,
            )
        raise RuntimeError("Failed to create YooKassa payment")

    payment_id = str(data.get("id", "")).strip()
    confirmation = data.get("confirmation")
    confirmation_url = ""
    if isinstance(confirmation, dict):
        confirmation_url = str(confirmation.get("confirmation_url", "")).strip()

    if not payment_id or not confirmation_url:
        logger.error("YooKassa create payment returned invalid payload: {}", data)
        raise RuntimeError("Invalid YooKassa payment response")

    return payment_id, confirmation_url


def check_payment(payment_id: str) -> bool:
    shop_id, secret_key = _auth()
    if not shop_id or not secret_key:
        logger.warning("YooKassa credentials are not configured")
        return False

    try:
        response = requests.get(
            f"{YOOKASSA_PAYMENTS_URL}/{payment_id}",
            auth=(shop_id, secret_key),
            timeout=30,
        )
        response.raise_for_status()
        data: dict[str, Any] = response.json()
    except requests.RequestException:
        logger.exception("Failed to check YooKassa payment")
        return False

    return str(data.get("status", "")).strip() == "succeeded"
