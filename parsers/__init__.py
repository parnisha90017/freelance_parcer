"""Parser package."""

from .fl import FLParser
from .freelanceru import FreelanceRuParser
from .kwork import KworkParser
from .telegram_chats import TelegramChatsParser

__all__ = ["FLParser", "FreelanceRuParser", "KworkParser", "TelegramChatsParser"]
