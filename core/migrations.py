"""
IM8 Bot — Schema Migrations
Versioned, incremental schema changes applied on top of the baseline schema
created by ``Database._init_tables()``.

How it works
────────────
``_init_tables()`` (in database.py) defines the **baseline** (version 1) and is
idempotent (``CREATE TABLE IF NOT EXISTS``). It always runs and is safe on both
fresh and existing databases. Anything that changes the schema *after* the
baseline is added here as a new migration with the next integer version.

Each migration is a ``(version, name, statements)`` tuple where ``statements``
is a list of SQL strings executed in order inside a single transaction. The
applied version is recorded in ``schema_migrations`` so each migration runs
exactly once. Migrations must be append-only and forward-only — never edit or
renumber a migration that has shipped; add a new one instead.
"""

# ═══════════════════════════════════════════════
#  Migration v2 — Retention & Cohort analytics
# ═══════════════════════════════════════════════
# Adds the per-member tables that power weekly join-cohort retention
# (% of a cohort still active after 1 / 4 / 12 weeks) plus the deployed-report
# bookkeeping and a small key/value store for analytics metadata.

_V2_RETENTION_COHORTS = """
    CREATE TABLE IF NOT EXISTS member_cohorts (
        guild_id    INTEGER NOT NULL,
        user_id     INTEGER NOT NULL,
        joined_at   TEXT NOT NULL,    -- ISO-8601 UTC datetime of the join
        cohort_week TEXT NOT NULL,    -- 'YYYY-MM-DD' Monday of the join week (UTC)
        PRIMARY KEY (guild_id, user_id)
    )
"""

_V2_COHORT_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_cohorts_week
        ON member_cohorts (guild_id, cohort_week)
"""

_V2_ACTIVITY_WEEKS = """
    CREATE TABLE IF NOT EXISTS member_activity_weeks (
        guild_id      INTEGER NOT NULL,
        user_id       INTEGER NOT NULL,
        activity_week TEXT NOT NULL,  -- 'YYYY-MM-DD' Monday of the activity week (UTC)
        messages      INTEGER NOT NULL DEFAULT 0,
        last_active   TEXT,           -- ISO-8601 UTC datetime of last activity that week
        PRIMARY KEY (guild_id, user_id, activity_week)
    )
"""

_V2_ACTIVITY_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_activity_week
        ON member_activity_weeks (guild_id, activity_week)
"""

_V2_RETENTION_REPORTS = """
    CREATE TABLE IF NOT EXISTS retention_reports (
        guild_id   INTEGER PRIMARY KEY,
        channel_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        updated_at TEXT DEFAULT (datetime('now'))
    )
"""

# Small per-guild key/value store for analytics bookkeeping
# (e.g. the date cohort tracking began, last backfill timestamp).
_V2_ANALYTICS_META = """
    CREATE TABLE IF NOT EXISTS analytics_meta (
        guild_id INTEGER NOT NULL,
        key      TEXT NOT NULL,
        value    TEXT,
        PRIMARY KEY (guild_id, key)
    )
"""


# ═══════════════════════════════════════════════
#  Migration v3 — Live engagement tracking
# ═══════════════════════════════════════════════
# Replaces the API-scan engagement model (re-downloading channel history and
# enumerating every reaction on every refresh — hours of rate-limited calls)
# with a local per-day aggregate that is written live from gateway events and
# read back instantly. ``channel_id`` is always the *tracked root* channel
# (thread activity rolls up to its parent), so queries can filter to the
# configured tracked-channel set.

_V3_ENGAGEMENT_DAILY = """
    CREATE TABLE IF NOT EXISTS engagement_daily (
        guild_id   INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,  -- tracked root channel (threads roll up)
        user_id    INTEGER NOT NULL,
        day        TEXT NOT NULL,     -- 'YYYY-MM-DD' (UTC)
        messages   INTEGER NOT NULL DEFAULT 0,  -- meaningful messages only
        reactions  INTEGER NOT NULL DEFAULT 0,  -- reactions GIVEN by the user
        PRIMARY KEY (guild_id, channel_id, user_id, day)
    )
"""

_V3_ENGAGEMENT_DAY_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_engagement_guild_day
        ON engagement_daily (guild_id, day)
"""

_V3_ENGAGEMENT_USER_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_engagement_guild_user
        ON engagement_daily (guild_id, user_id)
"""

# A guild may now run several public leaderboards at once (one per timeframe),
# matching how member_reports already works. Existing single-board configs are
# carried over from the legacy ``active_leaderboard`` table.
_V3_ACTIVE_LEADERBOARDS = """
    CREATE TABLE IF NOT EXISTS active_leaderboards (
        guild_id   INTEGER NOT NULL,
        timeframe  TEXT NOT NULL,    -- 'daily' / 'weekly' / 'monthly' / 'alltime'
        channel_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        updated_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (guild_id, timeframe)
    )
"""

_V3_MIGRATE_LEGACY_BOARDS = """
    INSERT OR IGNORE INTO active_leaderboards
        (guild_id, timeframe, channel_id, message_id, updated_at)
    SELECT guild_id, timeframe, channel_id, message_id, updated_at
    FROM active_leaderboard
"""


# ═══════════════════════════════════════════════
#  Migration v4 — Custom date-range leaderboards
# ═══════════════════════════════════════════════
# A public Most Active board may now cover an arbitrary, fixed date range
# (timeframe = 'custom'). The inclusive [range_start, range_end] UTC days are
# stored alongside the board so the auto-refresh recomputes the same window
# instead of silently falling back to the 30-day 'monthly' window. Both columns
# are NULL for the rolling daily/weekly/monthly/alltime boards.

_V4_RANGE_START = "ALTER TABLE active_leaderboards ADD COLUMN range_start TEXT"
_V4_RANGE_END = "ALTER TABLE active_leaderboards ADD COLUMN range_end TEXT"


# ═══════════════════════════════════════════════
#  Registry
# ═══════════════════════════════════════════════
# Ordered list of (version, name, [sql, ...]). Append new migrations here.
MIGRATIONS: list[tuple[int, str, list[str]]] = [
    (
        2,
        "retention_cohorts",
        [
            _V2_RETENTION_COHORTS,
            _V2_COHORT_INDEX,
            _V2_ACTIVITY_WEEKS,
            _V2_ACTIVITY_INDEX,
            _V2_RETENTION_REPORTS,
            _V2_ANALYTICS_META,
        ],
    ),
    (
        3,
        "live_engagement_tracking",
        [
            _V3_ENGAGEMENT_DAILY,
            _V3_ENGAGEMENT_DAY_INDEX,
            _V3_ENGAGEMENT_USER_INDEX,
            _V3_ACTIVE_LEADERBOARDS,
            _V3_MIGRATE_LEGACY_BOARDS,
        ],
    ),
    (
        4,
        "active_leaderboard_custom_range",
        [
            _V4_RANGE_START,
            _V4_RANGE_END,
        ],
    ),
]

# The baseline schema created by ``_init_tables()``.
BASELINE_VERSION = 1
