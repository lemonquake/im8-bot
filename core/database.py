"""
IM8 Bot — Async Database Layer
Lightweight async SQLite wrapper with connection lifecycle
and convenience query methods.
"""

import os
import logging
from pathlib import Path

import aiosqlite

from core import migrations

logger = logging.getLogger("im8bot.database")


class Database:
    """Async SQLite database wrapper for IM8 Bot."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._connection: aiosqlite.Connection | None = None

    # ═══════════════════════════════════════════════
    #  Lifecycle
    # ═══════════════════════════════════════════════

    async def connect(self) -> None:
        """Opens the database connection. Creates directories and file if needed."""
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row

        # Enable WAL mode for better concurrent read performance
        await self._connection.execute("PRAGMA journal_mode=WAL")
        await self._connection.execute("PRAGMA foreign_keys=ON")

        # Initialize base tables (baseline schema, version 1) ...
        await self._init_tables()
        await self._connection.commit()
        # ... then apply any incremental migrations on top.
        await self._run_migrations()

        logger.info(f"Database connected: {self.db_path}")

    async def close(self) -> None:
        """Closes the database connection gracefully."""
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.info("Database connection closed.")

    @property
    def is_connected(self) -> bool:
        """Returns True if the database connection is active."""
        return self._connection is not None

    @property
    def connection(self) -> aiosqlite.Connection | None:
        """The live aiosqlite connection (used by maintenance tasks like backups)."""
        return self._connection

    # ═══════════════════════════════════════════════
    #  Schema Initialization
    # ═══════════════════════════════════════════════

    async def _init_tables(self) -> None:
        """Creates initial tables if they do not exist."""
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id    INTEGER PRIMARY KEY,
                prefix      TEXT DEFAULT '!',
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)

        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id        INTEGER NOT NULL,
                target_channels TEXT NOT NULL,  -- JSON list of channel IDs
                task_type       TEXT NOT NULL,  -- e.g. 'embed_broadcast'
                payload         TEXT NOT NULL,  -- JSON EmbedScript state
                run_at          TEXT NOT NULL,  -- ISO-8601 timestamp
                created_by      INTEGER,
                created_at      TEXT DEFAULT (datetime('now')),
                status          TEXT DEFAULT 'pending', -- pending, sent, failed, cancelled
                last_error      TEXT
            )
        """)

        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS embed_templates (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                guild_id    INTEGER NOT NULL,
                created_by  INTEGER,
                payload     TEXT NOT NULL,   -- JSON EmbedScript state
                source_url  TEXT,            -- Optional URL for live sync
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)

        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS editor_sessions (
                message_id  INTEGER PRIMARY KEY,
                user_id     INTEGER NOT NULL,
                session_type TEXT NOT NULL,  -- 'embed' or 'hook'
                payload     TEXT NOT NULL,   -- JSON state
                updated_at  TEXT DEFAULT (datetime('now'))
            )
        """)

        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS hook_identities (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                preset_name TEXT NOT NULL,
                guild_id    INTEGER NOT NULL,
                hook_name   TEXT NOT NULL,
                avatar_url  TEXT,
                created_by  INTEGER,
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)

        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS hook_presets (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                guild_id    INTEGER NOT NULL,
                created_by  INTEGER,
                payload     TEXT NOT NULL,   -- Full JSON HookScript state (identity + content + embeds)
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)

        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id        INTEGER NOT NULL,
                channel_id      INTEGER NOT NULL,
                user_id         INTEGER NOT NULL,
                category        TEXT NOT NULL,
                status          TEXT DEFAULT 'open',
                created_at      TEXT DEFAULT (datetime('now'))
            )
        """)

        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS ticket_transcripts (
                ticket_id INTEGER PRIMARY KEY,
                json_path TEXT NOT NULL,
                closed_by INTEGER,
                closed_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(ticket_id) REFERENCES tickets(ticket_id)
            )
        """)

        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS ticket_config (
                guild_id INTEGER PRIMARY KEY,
                category_id INTEGER NOT NULL
            )
        """)

        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS onboarded_members (
                user_id     INTEGER NOT NULL,
                guild_id    INTEGER NOT NULL,
                onboarded_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, guild_id)
            )
        """)

        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS active_leaderboard (
                guild_id    INTEGER PRIMARY KEY,
                channel_id  INTEGER NOT NULL,
                message_id  INTEGER NOT NULL,
                timeframe   TEXT NOT NULL DEFAULT 'monthly',  -- 'weekly' or 'monthly'
                updated_at  TEXT DEFAULT (datetime('now'))
            )
        """)

        # Daily snapshot of a guild's membership, used to compute growth deltas
        # for the Member Report (today vs yesterday / vs 7 days ago).
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS member_snapshots (
                guild_id      INTEGER NOT NULL,
                snapshot_date TEXT NOT NULL,   -- 'YYYY-MM-DD' (UTC)
                total         INTEGER NOT NULL,
                humans        INTEGER NOT NULL,
                bots          INTEGER NOT NULL,
                online        INTEGER NOT NULL DEFAULT 0,
                admins        INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (guild_id, snapshot_date)
            )
        """)

        # New-member join counts. ``period`` is 'day' (one row per calendar
        # day) or 'week' (a stored weekly aggregate for completed/historical
        # weeks, keyed by the Monday week-start date). Daily rows are summed on
        # the fly for the current week; stored weekly rows cover history that
        # predates day-level tracking.
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS member_joins (
                guild_id    INTEGER NOT NULL,
                period      TEXT NOT NULL,    -- 'day' or 'week'
                period_date TEXT NOT NULL,    -- 'YYYY-MM-DD' (UTC; week = Monday)
                joins       INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, period, period_date)
            )
        """)

        # Public, auto-refreshing Member Report posts. One row per
        # (guild, timeframe) so a guild may run a daily AND a weekly report.
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS member_reports (
                guild_id    INTEGER NOT NULL,
                timeframe   TEXT NOT NULL,    -- 'daily' or 'weekly'
                channel_id  INTEGER NOT NULL,
                message_id  INTEGER NOT NULL,
                updated_at  TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (guild_id, timeframe)
            )
        """)

        # Target channel for a guild's Daily Growth Report. One row per guild.
        # ``last_message_id`` is the most recently posted report (used by the
        # hub status to link the latest report); a fresh message is posted each
        # day so the channel keeps a running history.
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS daily_growth_reports (
                guild_id        INTEGER PRIMARY KEY,
                channel_id      INTEGER NOT NULL,
                last_message_id INTEGER,
                updated_at      TEXT DEFAULT (datetime('now'))
            )
        """)

        # Per-day record of a guild's growth, written each time a Daily Growth
        # Report is generated. Read back on the next run to show the day-over-day
        # delta in new members. ``top_active`` stores the day's top movers as JSON.
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS daily_growth_log (
                guild_id     INTEGER NOT NULL,
                report_date  TEXT NOT NULL,    -- 'YYYY-MM-DD' (UTC)
                new_members  INTEGER NOT NULL DEFAULT 0,
                top_active   TEXT,             -- JSON list of {id, points, messages, reactions}
                recorded_at  TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (guild_id, report_date)
            )
        """)

        # Tracks every posted "Region Role" reaction message so the reaction
        # listener can recognise them. One row per posted message (a guild may
        # post the same prompt to several channels).
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS region_role_messages (
                message_id  INTEGER PRIMARY KEY,
                guild_id    INTEGER NOT NULL,
                channel_id  INTEGER NOT NULL,
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)

        logger.debug("Database tables verified.")

    # ═══════════════════════════════════════════════
    #  Migrations
    # ═══════════════════════════════════════════════

    async def _run_migrations(self) -> None:
        """Applies any incremental migrations not yet recorded for this database.

        The baseline schema (version 1) is created by ``_init_tables()`` above
        and is marked applied automatically the first time this runs, so an
        existing production database upgrades cleanly without re-creating
        anything. Each later migration in ``migrations.MIGRATIONS`` runs exactly
        once, in order, inside its own transaction.
        """
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    INTEGER PRIMARY KEY,
                name       TEXT NOT NULL,
                applied_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await self._connection.commit()

        cursor = await self._connection.execute("SELECT version FROM schema_migrations")
        applied = {row[0] for row in await cursor.fetchall()}

        # Record the baseline as applied (idempotent) since _init_tables() owns it.
        if migrations.BASELINE_VERSION not in applied:
            await self._connection.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (?, ?)",
                (migrations.BASELINE_VERSION, "baseline"),
            )
            await self._connection.commit()
            applied.add(migrations.BASELINE_VERSION)

        for version, name, statements in migrations.MIGRATIONS:
            if version in applied:
                continue
            try:
                for sql in statements:
                    await self._connection.execute(sql)
                await self._connection.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                    (version, name),
                )
                await self._connection.commit()
                logger.info(f"Applied migration v{version}: {name}")
            except Exception:
                await self._connection.rollback()
                logger.error(f"Migration v{version} ({name}) failed; rolled back.")
                raise

    # ═══════════════════════════════════════════════
    #  Query Helpers
    # ═══════════════════════════════════════════════

    async def execute(self, query: str, params: tuple = ()) -> aiosqlite.Cursor:
        """Executes a query and commits."""
        cursor = await self._connection.execute(query, params)
        await self._connection.commit()
        return cursor

    async def fetch_one(self, query: str, params: tuple = ()) -> dict | None:
        """Fetches a single row as a dict-like object."""
        cursor = await self._connection.execute(query, params)
        row = await cursor.fetchone()
        return row

    async def fetch_all(self, query: str, params: tuple = ()) -> list:
        """Fetches all matching rows."""
        cursor = await self._connection.execute(query, params)
        rows = await cursor.fetchall()
        return rows
