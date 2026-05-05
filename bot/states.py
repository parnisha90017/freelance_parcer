from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class BotStates(StatesGroup):
    waiting_for_keyword = State()
    waiting_for_min_price = State()


class AddTelegramSource(StatesGroup):
    waiting_for_link = State()
