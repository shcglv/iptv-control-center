"""Telegram integration: adapter, bot loop and notifications."""

from .bot import COMMANDS, TelegramBot
from .client import TelegramClient, TelegramError
from .notifications import Problem, collect_problems, run_notification_scan

__all__ = [
    "COMMANDS",
    "Problem",
    "TelegramBot",
    "TelegramClient",
    "TelegramError",
    "collect_problems",
    "run_notification_scan",
]
