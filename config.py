"""
IM8 Bot — Centralized Configuration
Loads environment variables and exposes typed constants.
"""

import os
from dotenv import load_dotenv

load_dotenv()


# ═══════════════════════════════════════════════
#  Discord Credentials
# ═══════════════════════════════════════════════
DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
APPLICATION_ID: int = int(os.getenv("APPLICATION_ID", "0"))

# ═══════════════════════════════════════════════
#  Bot Settings
# ═══════════════════════════════════════════════
BOT_VERSION: str = os.getenv("BOT_VERSION", "1.0.0")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

# ═══════════════════════════════════════════════
#  Database
# ═══════════════════════════════════════════════
DB_PATH: str = os.getenv("DB_PATH", "data/im8bot.db")

# ═══════════════════════════════════════════════
#  Backups
# ═══════════════════════════════════════════════
# Where nightly database snapshots are written, and how many to retain.
BACKUP_DIR: str = os.getenv("BACKUP_DIR", "data/backups")
BACKUP_KEEP: int = int(os.getenv("BACKUP_KEEP", "14"))
# Cron schedule (UTC) for the nightly backup job.
BACKUP_CRON: str = os.getenv("BACKUP_CRON", "30 4 * * *")

# ═══════════════════════════════════════════════
#  Channels
# ═══════════════════════════════════════════════
# The #onboarding hub where new members find everything they need.
ONBOARDING_CHANNEL_ID: int = int(os.getenv("ONBOARDING_CHANNEL_ID", "1509253397336031452"))

# The #introductions channel where members say hello and share their socials.
INTRODUCTIONS_CHANNEL_ID: int = int(os.getenv("INTRODUCTIONS_CHANNEL_ID", "1512410695109578814"))

# ═══════════════════════════════════════════════
#  Roles
# ═══════════════════════════════════════════════
# Staff/Admin role surfaced in the Member Report live feed.
ADMIN_ROLE_ID: int = int(os.getenv("ADMIN_ROLE_ID", "1493905370975047790"))

# Channel where the bot posts unhandled-error reports for the maintainers.
# Set to 0 to disable Discord error reporting (logs still capture everything).
ERROR_CHANNEL_ID: int = int(os.getenv("ERROR_CHANNEL_ID", "1513985910385934397"))

# ═══════════════════════════════════════════════
#  Branding
# ═══════════════════════════════════════════════
BOT_NAME: str = "IM8 Bot"
BOT_TAGLINE: str = "IM8 Health"

# Color Palette (IM8 Health Brand)
COLOR_BRAND: int = 0x00C9A7      # Teal — primary brand
COLOR_SUCCESS: int = 0x00D26A    # Emerald — success states
COLOR_ERROR: int = 0xFF6B6B      # Coral — errors
COLOR_WARNING: int = 0xFFA726    # Amber — warnings
COLOR_INFO: int = 0x29B6F6       # Sky blue — informational


def validate() -> None:
    """Validates that critical config values are present."""
    missing = []
    if not DISCORD_TOKEN:
        missing.append("DISCORD_TOKEN")
    if not APPLICATION_ID:
        missing.append("APPLICATION_ID")
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}"
        )
