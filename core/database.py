"""
IM8 Bot — Async Database Layer
Lightweight async SQLite wrapper with connection lifecycle
and convenience query methods.
"""

import os
import logging
from pathlib import Path

import aiosqlite

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

        # Initialize base tables
        await self._init_tables()
        await self._connection.commit()

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

        logger.debug("Database tables verified.")

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
