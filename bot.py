"""
IM8 Bot — Entry Point
Initializes logging, validates configuration, and starts the bot.
"""

import asyncio
import sys

import config
from utils.logger import setup_logging
from core.bot_class import IM8Bot


def main() -> None:
    """Main entry point for IM8 Bot."""

    # ── Force UTF-8 on Windows consoles ──────────
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    # ── Initialize Logging ───────────────────────
    setup_logging()

    # ── Validate Configuration ───────────────────
    try:
        config.validate()
    except EnvironmentError as e:
        print(f"\n  Configuration Error: {e}\n")
        sys.exit(1)

    # ── ASCII Banner ─────────────────────────────
    banner = f"""
  ╔══════════════════════════════════════════╗
  ║                                          ║
  ║     ██╗███╗   ███╗ █████╗                ║
  ║     ██║████╗ ████║██╔══██╗               ║
  ║     ██║██╔████╔██║╚█████╔╝               ║
  ║     ██║██║╚██╔╝██║██╔══██╗               ║
  ║     ██║██║ ╚═╝ ██║╚█████╔╝               ║
  ║     ╚═╝╚═╝     ╚═╝ ╚════╝                ║
  ║                                          ║
  ║     IM8 Bot  v{config.BOT_VERSION:<27}║
  ║     IM8 Health                           ║
  ║                                          ║
  ╚══════════════════════════════════════════╝
"""
    print(banner)

    # ── Start Bot ────────────────────────────────
    bot = IM8Bot()

    try:
        bot.run(
            config.DISCORD_TOKEN,
            log_handler=None,  # We handle logging ourselves
        )
    except KeyboardInterrupt:
        print("\n  ◈  Shutdown initiated via keyboard interrupt.")
    except Exception as e:
        print(f"\n  ❌  Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
