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
#  Migration v5 — The Arena (competitive gamification)
# ═══════════════════════════════════════════════
# Builds a competitive game layer on top of the live ``engagement_daily``
# aggregate (no new event listeners — everything is computed at READ time from
# the same per-day counts the leaderboards already use). Adds time-boxed
# Seasons, multiplier Power-Up events, collectible Badges, a Hall-of-Fame
# snapshot of finalized seasons, and one deployed public Arena board per guild.

# Time-boxed competition windows. Exactly one row per guild is ``active``;
# finished seasons are marked ``ended`` and snapshotted into arena_champions.
_V5_ARENA_SEASONS = """
    CREATE TABLE IF NOT EXISTS arena_seasons (
        guild_id     INTEGER NOT NULL,
        season_no    INTEGER NOT NULL,
        name         TEXT NOT NULL,
        start_day    TEXT NOT NULL,             -- 'YYYY-MM-DD' UTC, inclusive
        end_day      TEXT NOT NULL,             -- 'YYYY-MM-DD' UTC, inclusive (last competing day)
        status       TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'ended'
        champion_id  INTEGER,                   -- set when finalized
        finalized_at TEXT,
        created_at   TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (guild_id, season_no)
    )
"""

_V5_ARENA_SEASON_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_arena_seasons_status
        ON arena_seasons (guild_id, status)
"""

# Staff-launched scoring multipliers over a UTC day window. Engagement on a day
# covered by an event is worth ``multiplier`` × its normal points in the Arena.
_V5_ARENA_EVENTS = """
    CREATE TABLE IF NOT EXISTS arena_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id    INTEGER NOT NULL,
        name        TEXT NOT NULL,
        emoji       TEXT,
        multiplier  REAL NOT NULL DEFAULT 2.0,
        start_day   TEXT NOT NULL,              -- 'YYYY-MM-DD' UTC, inclusive
        end_day     TEXT NOT NULL,              -- 'YYYY-MM-DD' UTC, inclusive
        created_by  INTEGER,
        created_at  TEXT DEFAULT (datetime('now'))
    )
"""

_V5_ARENA_EVENT_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_arena_events_guild
        ON arena_events (guild_id, start_day, end_day)
"""

# Collectible achievements. ``season_no`` is 0 for lifetime/global badges
# (e.g. streaks) and the season number for season-scoped badges (champion,
# podium, …). Awards are idempotent via the composite primary key.
_V5_ARENA_BADGES = """
    CREATE TABLE IF NOT EXISTS arena_badges (
        guild_id  INTEGER NOT NULL,
        user_id   INTEGER NOT NULL,
        badge_key TEXT NOT NULL,
        season_no INTEGER NOT NULL DEFAULT 0,
        earned_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (guild_id, user_id, badge_key, season_no)
    )
"""

_V5_ARENA_BADGE_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_arena_badges_user
        ON arena_badges (guild_id, user_id)
"""

# Permanent record of each finalized season (the Hall of Fame). ``standings``
# is a JSON snapshot of the season's top finishers so the board renders without
# recomputing historical windows.
_V5_ARENA_CHAMPIONS = """
    CREATE TABLE IF NOT EXISTS arena_champions (
        guild_id     INTEGER NOT NULL,
        season_no    INTEGER NOT NULL,
        name         TEXT NOT NULL,
        start_day    TEXT NOT NULL,
        end_day      TEXT NOT NULL,
        champion_id  INTEGER,
        standings    TEXT,                      -- JSON [{user_id, points, messages, reactions}]
        finalized_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (guild_id, season_no)
    )
"""

# One deployed public Arena board per guild (the gamified centerpiece). Mirrors
# the active_leaderboards bookkeeping pattern.
_V5_ARENA_BOARDS = """
    CREATE TABLE IF NOT EXISTS arena_boards (
        guild_id   INTEGER PRIMARY KEY,
        channel_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        updated_at TEXT DEFAULT (datetime('now'))
    )
"""


# ═══════════════════════════════════════════════
#  Migration v6 — Link Sync (links → Google Sheet)
# ═══════════════════════════════════════════════
# The bot watches a links channel and mirrors every shared URL into a Google
# Sheet (handle | display name | link). This table is the **dedup source of
# truth**: a URL is recorded here only after it has been successfully written
# to the sheet, so the live listener, the 10-minute sweep, and the manual
# "Sweep Links" button can never write the same link twice. De-dup is per
# normalized URL (``url_key``) per guild. Rows seeded from links already present
# in the sheet use ``message_id = 0`` (no originating Discord message).

_V6_LINK_SYNC_LOG = """
    CREATE TABLE IF NOT EXISTS link_sync_log (
        guild_id    INTEGER NOT NULL,
        channel_id  INTEGER NOT NULL,
        message_id  INTEGER NOT NULL DEFAULT 0,  -- 0 = seeded from existing sheet rows
        user_id     INTEGER NOT NULL DEFAULT 0,
        url         TEXT NOT NULL,               -- the link as written to the sheet
        url_key     TEXT NOT NULL,               -- normalized form used for dedup
        handle      TEXT,                        -- author username (sheet column A)
        display     TEXT,                        -- author display name (sheet column B)
        synced_at   TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (guild_id, url_key)
    )
"""

_V6_LINK_SYNC_MSG_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_link_sync_msg
        ON link_sync_log (guild_id, message_id)
"""


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
    (
        5,
        "arena_gamification",
        [
            _V5_ARENA_SEASONS,
            _V5_ARENA_SEASON_INDEX,
            _V5_ARENA_EVENTS,
            _V5_ARENA_EVENT_INDEX,
            _V5_ARENA_BADGES,
            _V5_ARENA_BADGE_INDEX,
            _V5_ARENA_CHAMPIONS,
            _V5_ARENA_BOARDS,
        ],
    ),
    (
        6,
        "link_sync",
        [
            _V6_LINK_SYNC_LOG,
            _V6_LINK_SYNC_MSG_INDEX,
        ],
    ),
]

# The baseline schema created by ``_init_tables()``.
BASELINE_VERSION = 1
