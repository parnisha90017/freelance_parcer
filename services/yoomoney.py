from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import aiohttp
from loguru import logger

from config import config

YOOMONEY_HISTORY_URL = "https://yoomoney.ru/api/operation-history"


async def check_payment(user_id: int, amount: int = 990) -> bool:
    token = config.YOOMONEY_TOKEN.strip()
    if not token:
        return False

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "type": "deposition",
        "records": "10",
    }
    timeout = aiohttp.ClientTimeout(total=30)

    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.post(YOOMONEY_HISTORY_URL, data=data) as response:
                if response.status != 200:
                    logger.warning("YooMoney API returned status {}", response.status)
                    return False
                payload = await response.json(content_type=None)
    except Exception:
        logger.exception("Failed to check YooMoney payment")
        return False

    operations = payload.get("operations", [])
    if not isinstance(operations, list):
        return False

    expected_amount = Decimal(str(amount))
    user_id_text = str(user_id)

    for operation in operations:
        if not isinstance(operation, dict):
            continue

        try:
            operation_amount = Decimal(str(operation.get("amount", "0")))
        except (InvalidOperation, TypeError, ValueError):
            continue

        comment = str(operation.get("comment", ""))
        status = str(operation.get("status", ""))
        direction = str(operation.get("direction", "in"))

        if status and status != "success":
            continue
        if direction and direction != "in":
            continue
        if operation_amount != expected_amount:
            continue
        if user_id_text not in comment:
            continue

        return True

    return False
