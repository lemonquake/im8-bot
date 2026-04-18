"""
IM8 Bot — Logger Configuration
Colored console output with rotating file handler.
"""

import os
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from colorama import init as colorama_init, Fore, Style

import config

# Initialize colorama for Windows ANSI support
colorama_init(autoreset=True)


# ═══════════════════════════════════════════════
#  Custom Formatter — Colored Console Output
# ═══════════════════════════════════════════════

class ColoredFormatter(logging.Formatter):
    """Applies color to log messages based on severity level."""

    LEVEL_COLORS = {
        logging.DEBUG:    Fore.CYAN,
        logging.INFO:     Fore.GREEN,
        logging.WARNING:  Fore.YELLOW,
        logging.ERROR:    Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT,
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelno, Fore.WHITE)
        reset = Style.RESET_ALL

        # Colorize the level name
        record.levelname = f"{color}{record.levelname:<8}{reset}"

        # Colorize the timestamp
        record.asctime = f"{Fore.WHITE}{Style.DIM}{self.formatTime(record, self.datefmt)}{reset}"

        # Colorize the logger name
        record.name = f"{Fore.CYAN}{Style.DIM}{record.name}{reset}"

        return super().format(record)


# ═══════════════════════════════════════════════
#  Setup Function
# ═══════════════════════════════════════════════

def setup_logging() -> None:
    """Configures the root logger with console and file handlers."""

    log_level = getattr(logging, config.LOG_LEVEL, logging.INFO)

    # ── Root Logger ──────────────────────────────
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear any existing handlers to prevent duplicates on reload
    root_logger.handlers.clear()

    # ── Console Handler ──────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_format = ColoredFormatter(
        fmt="%(asctime)s  %(levelname)s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_format)
    root_logger.addHandler(console_handler)

    # ── File Handler ─────────────────────────────
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    file_handler = RotatingFileHandler(
        filename=logs_dir / "im8bot.log",
        maxBytes=5 * 1024 * 1024,   # 5 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)  # Capture everything to file
    file_format = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(name)-24s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_format)
    root_logger.addHandler(file_handler)

    # ── Suppress noisy third-party loggers ───────
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    logging.getLogger("im8bot").info("Logging initialized.")
