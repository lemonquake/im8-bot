"""
IM8 Bot — Custom Bot Class
Central bot subclass with lifecycle management, auto-cog loading,
health tracking, and graceful shutdown.
"""

import os
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord.ext import commands

import config
from core.database import Database
from core.scheduler import Scheduler

logger = logging.getLogger("im8bot.core")


class IM8Bot(commands.Bot):
    """Custom bot class for IM8 Health."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            application_id=config.APPLICATION_ID,
            help_command=None,
        )

        # ── Lifecycle State ──────────────────────────
        self.boot_time: datetime = datetime.now(timezone.utc)
        self.version: str = config.BOT_VERSION
        self.is_fully_ready: bool = False

        # ── Subsystems ───────────────────────────────
        self.database: Database = Database(config.DB_PATH)
        self.scheduler: Scheduler = Scheduler()

        # ── Health Tracking ──────────────────────────
        self.health: dict[str, str] = {
            "gateway": "Pending",
            "database": "Pending",
            "scheduler": "Pending",
            "commands": "Pending",
            "events": "Pending",
        }

    # ═══════════════════════════════════════════════
    #  Startup Sequence
    # ═══════════════════════════════════════════════

    async def setup_hook(self) -> None:
        """Called after login but before connecting to the gateway.
        Initializes all subsystems in order."""

        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info("  IM8 Bot — Initialization Sequence")
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        # 1. Database
        try:
            await self.database.connect()
            self.health["database"] = "Connected"
            logger.info("  ✅  Database .............. Connected")
        except Exception as e:
            self.health["database"] = "Failed"
            logger.error(f"  ❌  Database .............. Failed: {e}")

        # 2. Scheduler
        try:
            self.scheduler.start()
            self.health["scheduler"] = "Active"
            logger.info("  ✅  Scheduler ............. Active")
        except Exception as e:
            self.health["scheduler"] = "Failed"
            logger.error(f"  ❌  Scheduler ............. Failed: {e}")

        # 3. Load Cogs
        await self._load_all_cogs()

        # 4. Sync Slash Commands
        try:
            synced = await self.tree.sync()
            self.health["commands"] = "Synced"
            logger.info(f"  ✅  Commands .............. Synced ({len(synced)} commands)")
        except Exception as e:
            self.health["commands"] = "Sync Failed"
            logger.error(f"  ❌  Commands .............. Sync Failed: {e}")

        # 5. Background Tasks
        self.scheduler.add_interval_job(
            self._cleanup_sessions,
            job_id="session_cleanup",
            hours=12  # Run every 12 hours
        )

        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    async def _load_all_cogs(self) -> None:
        """Dynamically discovers and loads all cogs from the cogs/ directory."""
        cogs_dir = Path(__file__).parent.parent / "cogs"

        if not cogs_dir.exists():
            logger.warning("  ⚠️  Cogs directory not found, skipping cog loading.")
            return

        loaded = 0
        for file in sorted(cogs_dir.glob("*.py")):
            if file.name.startswith("_"):
                continue

            cog_path = f"cogs.{file.stem}"
            try:
                await self.load_extension(cog_path)
                loaded += 1
                logger.info(f"  ◈  Loaded cog: {file.stem}")
            except Exception as e:
                logger.error(f"  ❌  Failed to load cog '{file.stem}': {e}")
                traceback.print_exc()

        self.health["events"] = "Bound"
        logger.info(f"  ✅  Cogs .................. {loaded} loaded")

    # ═══════════════════════════════════════════════
    #  Event Overrides
    # ═══════════════════════════════════════════════

    async def on_ready(self) -> None:
        """Fires when the bot is fully connected to Discord."""
        self.health["gateway"] = "Connected"
        self.is_fully_ready = True

        activity = discord.Activity(
            type=discord.ActivityType.watching,
            name="IM8 Health",
        )
        await self.change_presence(
            status=discord.Status.online,
            activity=activity,
        )

        logger.info("")
        logger.info("  ╔══════════════════════════════════════════╗")
        logger.info(f"  ║  {config.BOT_NAME} v{self.version}  —  Online & Operational  ║")
        logger.info("  ╚══════════════════════════════════════════╝")
        logger.info(f"  ◈  User:    {self.user} (ID: {self.user.id})")
        logger.info(f"  ◈  Guilds:  {len(self.guilds)}")
        logger.info(f"  ◈  Latency: {round(self.latency * 1000)}ms")
        logger.info("")

    # ═══════════════════════════════════════════════
    #  Shutdown
    # ═══════════════════════════════════════════════

    async def close(self) -> None:
        """Graceful shutdown — stops subsystems before disconnecting."""
        logger.info("")
        logger.info("  ◈  Initiating graceful shutdown...")

        # Stop scheduler
        try:
            self.scheduler.shutdown()
            logger.info("  ✅  Scheduler stopped")
        except Exception as e:
            logger.error(f"  ❌  Scheduler shutdown error: {e}")

        # Close database
        try:
            await self.database.close()
            logger.info("  ✅  Database closed")
        except Exception as e:
            logger.error(f"  ❌  Database close error: {e}")

        logger.info("  ◈  Disconnecting from Discord...")
        await super().close()
        logger.info("  ✅  Shutdown complete.")

    async def _cleanup_sessions(self) -> None:
        """Removes editor sessions older than 24 hours."""
        logger.info("  ◈  Running session cleanup...")
        try:
            # SQLite datetime('now', '-1 day') matches our TEXT timestamps
            result = await self.database.execute(
                "DELETE FROM editor_sessions WHERE updated_at < datetime('now', '-24 hours')"
            )
            logger.info(f"  ✅  Cleaned up {result.rowcount} orphaned sessions.")
        except Exception as e:
            logger.error(f"  ❌  Session cleanup failed: {e}")

    # ═══════════════════════════════════════════════
    #  Health Utilities
    # ═══════════════════════════════════════════════

    def get_health_summary(self) -> dict[str, str]:
        """Returns current health state of all subsystems."""
        # Refresh gateway status based on real-time state
        if self.is_closed():
            self.health["gateway"] = "Disconnected"
        elif self.is_fully_ready:
            self.health["gateway"] = "Connected"
        else:
            self.health["gateway"] = "Connecting"

        return self.health.copy()

    @property
    def total_members(self) -> int:
        """Total unique members across all guilds."""
        return sum(g.member_count or 0 for g in self.guilds)

    @property
    def total_commands(self) -> int:
        """Total slash commands registered."""
        return len(self.tree.get_commands())
