"""
IM8 Bot — Most Active Module
Detects the most engaged members across the community channels using a
weighted point system, and maintains public, auto-refreshing leaderboards.

How engagement is tracked (v2 — live tracking)
──────────────────────────────────────────────
Engagement is recorded **live** from gateway events into the per-day
``engagement_daily`` aggregate (meaningful messages via ``on_message``,
reactions given via the raw reaction events; thread activity rolls up to the
tracked parent channel). Every leaderboard/report is then a sub-second SQL
query instead of the old model of re-downloading channel history and
enumerating every reaction through the rate-limited REST API on each refresh
(which took hours).

The REST scan still exists in two narrow forms:
• **Backfill History** — a one-time deep scan that seeds the last
  ``BACKFILL_DAYS`` days (messages + reactions, attributed to the message's
  day) so leaderboards have history immediately.
• **Startup catch-up** — a cheap messages-only scan of the recent gap window
  after downtime, merged idempotently (reactions made while offline are not
  recoverable per-day and are skipped; the one-time backfill covers them).

Staff exclusions are applied at *read* time, so changing the excluded-role set
applies retroactively to all recorded history.
"""

import discord
from discord.ext import commands
import logging
import re
import json
import asyncio
import datetime
from collections import defaultdict

logger = logging.getLogger("im8bot.cogs.active")

# ═══════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════

# Channels included in engagement scanning.
TRACKED_CHANNELS: list[int] = [
    1484912407447994443,
    1494715335981404280,
    1494173591581757491,
]

# Channels explicitly excluded from engagement tracking (and its threads).
IGNORED_CHANNEL_ID = 1484912407447994439

POINTS_PER_MESSAGE = 5
POINTS_PER_REACTION = 2

# Staff/mod roles to exclude entirely from the leaderboard (neither their
# messages nor their reactions earn points).
EXCLUDED_ROLE_IDS: set[int] = {
    1486421826799407115,
    1493905370975047790,
}

# Timeframes available for detection and public leaderboards.
# ``days`` counts calendar days including today; ``None`` = all recorded history.
# ``report_name`` is the plain word used in the public report title
# ("IM8 <report_name> Most Active Report").
TIMEFRAMES: dict[str, dict] = {
    "daily":   {"days": 1,    "label": "Today",        "report_name": "Daily",    "emoji": "☀️"},
    "weekly":  {"days": 7,    "label": "Last 7 Days",  "report_name": "Weekly",   "emoji": "📆"},
    "monthly": {"days": 30,   "label": "Last 30 Days", "report_name": "Monthly",  "emoji": "🗓️"},
    "alltime": {"days": None, "label": "All Time",     "report_name": "All-Time", "emoji": "🏛️"},
}
# Back-compat alias (other modules import this for day counts).
TIMEFRAME_DAYS = {k: v["days"] for k, v in TIMEFRAMES.items() if v["days"] is not None}

# Auto-refresh cadence for the public leaderboards. Refreshes are now instant
# SQL queries, so this can be aggressive.
REFRESH_HOURS = 1

# How many members to surface on the leaderboard.
LEADERBOARD_SIZE = 10

# How far back the one-time deep history backfill reaches (days).
BACKFILL_DAYS = 90

# Max days the automatic messages-only catch-up scan covers after downtime.
CATCHUP_MAX_DAYS = 14

# Low-effort messages that should not earn engagement points.
STOPWORDS = {
    "ok", "okay", "k", "kk", "hi", "hii", "hey", "hello", "yo", "sup",
    "lol", "lmao", "lmfao", "haha", "hahaha", "hehe", "xd", "yes", "no",
    "yep", "nope", "yeah", "yup", "nah", "ty", "thx", "thanks", "thank you",
    "np", "gg", "wow", "nice", "cool", "same", "this", "fr", "bruh", "oof",
    "rip", "yas", "yass", "omg", "bet", "facts", "true", "ikr", "brb",
    "gn", "gm", "good morning", "good night", "agreed", "ditto", "indeed",
}

# Minimum meaningful character length (after stripping punctuation/emoji).
MIN_MEANINGFUL_LENGTH = 4


def is_meaningful(content: str) -> bool:
    """Heuristic filter that rejects short / low-effort messages."""
    if not content:
        return False

    # Strip everything except word characters and whitespace (drops emoji,
    # custom emoji markup, links punctuation, mention brackets, etc.)
    cleaned = re.sub(r"[^\w\s]", "", content).strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)

    if not cleaned:
        return False
    if cleaned in STOPWORDS:
        return False
    if len(cleaned) < MIN_MEANINGFUL_LENGTH:
        return False
    return True


def _is_excluded(guild: discord.Guild, user_id: int) -> bool:
    """True if the member holds one of the excluded staff roles."""
    if not EXCLUDED_ROLE_IDS:
        return False
    member = guild.get_member(user_id)
    if member is None:
        return False  # Not cached / left the server — can't determine, so include.
    return any(r.id in EXCLUDED_ROLE_IDS for r in member.roles)


# ═══════════════════════════════════════════════
#  Date / scope helpers
# ═══════════════════════════════════════════════

def _utc_today() -> datetime.date:
    return datetime.datetime.now(datetime.timezone.utc).date()


def _cutoff_day(days: int) -> str:
    """First calendar day (ISO) included in an N-day window ending today."""
    return (_utc_today() - datetime.timedelta(days=days - 1)).isoformat()


def _tracked_ids() -> list[int]:
    return [cid for cid in TRACKED_CHANNELS if cid != IGNORED_CHANNEL_ID]


def _root_for_channel(channel) -> int | None:
    """Resolves a channel/thread to its tracked root channel id, or None.

    Thread activity is attributed to the parent channel so the tracked-channel
    filter keeps working when threads come and go.
    """
    cid = getattr(channel, "id", None)
    parent_id = getattr(channel, "parent_id", None)
    if cid == IGNORED_CHANNEL_ID or parent_id == IGNORED_CHANNEL_ID:
        return None
    if cid in TRACKED_CHANNELS:
        return cid
    if parent_id in TRACKED_CHANNELS:
        return parent_id
    return None


def _parse_sql_ts(ts: str) -> int | None:
    """Parses an SQLite ``datetime('now')`` UTC string into a unix timestamp."""
    try:
        dt = datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


# ═══════════════════════════════════════════════
#  Analytics metadata helpers
# ═══════════════════════════════════════════════
# (Local copies — importing them from cogs.retention would be a circular import,
#  since retention imports the tracked-channel config from this module.)

async def _get_meta(bot: commands.Bot, guild_id: int, key: str) -> str | None:
    row = await bot.database.fetch_one(
        "SELECT value FROM analytics_meta WHERE guild_id = ? AND key = ?",
        (guild_id, key),
    )
    return row["value"] if row else None


async def _set_meta(bot: commands.Bot, guild_id: int, key: str, value: str) -> None:
    await bot.database.execute(
        "INSERT OR REPLACE INTO analytics_meta (guild_id, key, value) VALUES (?, ?, ?)",
        (guild_id, key, value),
    )


async def _ensure_live_since(bot: commands.Bot, guild_id: int) -> None:
    if await _get_meta(bot, guild_id, "engagement_live_since") is None:
        await _set_meta(bot, guild_id, "engagement_live_since", _utc_today().isoformat())


async def get_backfill_state(bot: commands.Bot, guild_id: int) -> dict | None:
    raw = await _get_meta(bot, guild_id, "engagement_backfill")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def get_coverage_start(bot: commands.Bot, guild_id: int) -> str | None:
    """Earliest day with recorded engagement data ('YYYY-MM-DD'), or None."""
    tracked = _tracked_ids()
    placeholders = ",".join("?" * len(tracked))
    row = await bot.database.fetch_one(
        f"SELECT MIN(day) AS d FROM engagement_daily WHERE guild_id = ? AND channel_id IN ({placeholders})",
        (guild_id, *tracked),
    )
    return row["d"] if row and row["d"] else None


async def get_period_start(bot: commands.Bot, guild_id: int) -> str | None:
    """The instant the current engagement period began, as an ISO-8601 UTC
    string (set by an engagement reset). Both history scans clamp to it so they
    can never re-import activity from *before* the reset — without this, the
    startup catch-up would re-create up to 14 days of pre-reset message history.
    ``None`` means no floor (the original behaviour on un-reset guilds)."""
    return await _get_meta(bot, guild_id, "engagement_period_start")


def _period_start_dt(value: str | None) -> datetime.datetime | None:
    """Parses a stored ``engagement_period_start`` into a tz-aware UTC datetime
    (accepts a full timestamp or a bare 'YYYY-MM-DD' day). None if unset/bad."""
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(value)
    except ValueError:
        try:
            dt = datetime.datetime.fromisoformat(value[:10])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


# ═══════════════════════════════════════════════
#  Live recording
# ═══════════════════════════════════════════════

async def _bump(
    bot: commands.Bot,
    guild_id: int,
    channel_id: int,
    user_id: int,
    day: str,
    d_msg: int = 0,
    d_react: int = 0,
) -> None:
    """Adjusts a member's per-day counters. Decrements floor at 0 and never
    create rows (so e.g. removing a reaction added before tracking is a no-op)."""
    if d_msg >= 0 and d_react >= 0:
        await bot.database.execute(
            "INSERT INTO engagement_daily (guild_id, channel_id, user_id, day, messages, reactions) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id, channel_id, user_id, day) DO UPDATE SET "
            "messages = messages + excluded.messages, "
            "reactions = reactions + excluded.reactions",
            (guild_id, channel_id, user_id, day, d_msg, d_react),
        )
    else:
        await bot.database.execute(
            "UPDATE engagement_daily SET "
            "messages = MAX(messages + ?, 0), reactions = MAX(reactions + ?, 0) "
            "WHERE guild_id = ? AND channel_id = ? AND user_id = ? AND day = ?",
            (d_msg, d_react, guild_id, channel_id, user_id, day),
        )


# ═══════════════════════════════════════════════
#  Engagement computation (instant, DB-backed)
# ═══════════════════════════════════════════════

def _rank_rows(
    guild: discord.Guild,
    rows: list,
    limit: int | None,
) -> list[tuple[int, dict]]:
    """Applies point weights + staff exclusions and sorts the aggregate rows.

    Shared by every windowed query (rolling and custom-range) so the counting
    rules are identical everywhere.
    """
    results: list[tuple[int, dict]] = []
    for row in rows:
        m = row["m"] or 0
        r = row["r"] or 0
        points = m * POINTS_PER_MESSAGE + r * POINTS_PER_REACTION
        if points <= 0:
            continue
        if _is_excluded(guild, row["user_id"]):
            continue
        results.append((row["user_id"], {"points": points, "messages": m, "reactions": r}))

    results.sort(key=lambda kv: kv[1]["points"], reverse=True)
    return results if limit is None else results[:limit]


async def compute_engagement(
    bot: commands.Bot,
    guild: discord.Guild,
    days: int | None,
    limit: int | None = LEADERBOARD_SIZE,
) -> list[tuple[int, dict]]:
    """Returns the top engaged members from the live-tracked aggregates.

    ``days=None`` means all recorded history. ``limit=None`` returns the full
    ranking (used for rank lookups). Returns ``(user_id, {"points",
    "messages", "reactions"})`` tuples sorted by points descending. Members
    holding an excluded staff role are skipped.
    """
    tracked = _tracked_ids()
    if not tracked:
        return []

    placeholders = ",".join("?" * len(tracked))
    sql = (
        "SELECT user_id, SUM(messages) AS m, SUM(reactions) AS r "
        f"FROM engagement_daily WHERE guild_id = ? AND channel_id IN ({placeholders})"
    )
    params: list = [guild.id, *tracked]
    if days is not None:
        sql += " AND day >= ?"
        params.append(_cutoff_day(days))
    # Guard against future-dated rows (clock skew) so a window never counts
    # days beyond today.
    sql += " AND day <= ?"
    params.append(_utc_today().isoformat())
    sql += " GROUP BY user_id"

    rows = await bot.database.fetch_all(sql, tuple(params))
    return _rank_rows(guild, rows, limit)


async def compute_engagement_range(
    bot: commands.Bot,
    guild: discord.Guild,
    start_day: str,
    end_day: str,
    limit: int | None = LEADERBOARD_SIZE,
) -> list[tuple[int, dict]]:
    """Top engaged members over an inclusive custom UTC date range.

    ``start_day``/``end_day`` are 'YYYY-MM-DD'. Zero-padded ISO dates sort
    chronologically, so ``BETWEEN`` is a correct inclusive range. Same point
    weights and staff exclusions as :func:`compute_engagement`.
    """
    tracked = _tracked_ids()
    if not tracked:
        return []

    placeholders = ",".join("?" * len(tracked))
    rows = await bot.database.fetch_all(
        "SELECT user_id, SUM(messages) AS m, SUM(reactions) AS r "
        f"FROM engagement_daily WHERE guild_id = ? AND channel_id IN ({placeholders}) "
        "AND day BETWEEN ? AND ? GROUP BY user_id",
        (guild.id, *tracked, start_day, end_day),
    )
    return _rank_rows(guild, rows, limit)


async def get_member_stats(bot: commands.Bot, guild: discord.Guild, user_id: int) -> dict:
    """Per-timeframe points/rank for one member, plus a 14-day daily series."""
    per_timeframe: dict[str, dict | None] = {}
    for tf, spec in TIMEFRAMES.items():
        ranked = await compute_engagement(bot, guild, spec["days"], limit=None)
        per_timeframe[tf] = None
        for idx, (uid, data) in enumerate(ranked):
            if uid == user_id:
                per_timeframe[tf] = {"rank": idx + 1, "of": len(ranked), **data}
                break

    tracked = _tracked_ids()
    placeholders = ",".join("?" * len(tracked))
    rows = await bot.database.fetch_all(
        "SELECT day, SUM(messages) AS m, SUM(reactions) AS r FROM engagement_daily "
        f"WHERE guild_id = ? AND user_id = ? AND day >= ? AND channel_id IN ({placeholders}) "
        "GROUP BY day",
        (guild.id, user_id, _cutoff_day(14), *tracked),
    )
    by_day = {row["day"]: (row["m"] or 0, row["r"] or 0) for row in rows}
    series = []
    for i in range(13, -1, -1):
        d = (_utc_today() - datetime.timedelta(days=i)).isoformat()
        m, r = by_day.get(d, (0, 0))
        series.append((d, m * POINTS_PER_MESSAGE + r * POINTS_PER_REACTION))

    return {"timeframes": per_timeframe, "series": series}


async def get_channel_insights(bot: commands.Bot, guild: discord.Guild, days: int = 30) -> dict:
    """Per-tracked-channel totals and the busiest day in the window."""
    since = _cutoff_day(days)
    tracked = _tracked_ids()
    placeholders = ",".join("?" * len(tracked))
    rows = await bot.database.fetch_all(
        "SELECT channel_id, SUM(messages) AS m, SUM(reactions) AS r, "
        "COUNT(DISTINCT user_id) AS members "
        f"FROM engagement_daily WHERE guild_id = ? AND day >= ? AND channel_id IN ({placeholders}) "
        "GROUP BY channel_id ORDER BY m DESC",
        (guild.id, since, *tracked),
    )
    busiest = await bot.database.fetch_one(
        "SELECT day, SUM(messages) AS m FROM engagement_daily "
        f"WHERE guild_id = ? AND day >= ? AND channel_id IN ({placeholders}) "
        "GROUP BY day ORDER BY m DESC LIMIT 1",
        (guild.id, since, *tracked),
    )
    return {
        "days": days,
        "channels": [dict(row) for row in rows],
        "busiest": dict(busiest) if busiest and busiest["day"] else None,
    }


# ═══════════════════════════════════════════════
#  History scans (backfill + downtime catch-up)
# ═══════════════════════════════════════════════
# The only REST-heavy code paths left. Channels are scanned concurrently
# (separate rate-limit buckets) and there are no fixed sleeps — discord.py's
# rate limiter already waits exactly as long as each bucket requires.

_BACKFILL_RUNNING: set[int] = set()


async def _scan_root_channel(
    guild: discord.Guild,
    root_id: int,
    cutoff: datetime.datetime,
    buckets: dict,
    stats: dict,
    include_reactions: bool,
    include_archived_threads: bool,
    floor_day: str | None = None,
) -> None:
    """Scans one tracked channel (and its threads) into ``buckets``.

    Buckets are keyed ``(root_id, user_id, day)`` → ``[messages, reactions]``.
    Reactions are attributed to the message's day (their own timestamps are
    not exposed by the API). ``floor_day`` ('YYYY-MM-DD') drops anything dated
    before the current engagement period so a reset can never be undone by a
    scan (defence-in-depth on top of the clamped ``cutoff``).
    """
    channel = guild.get_channel(root_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(root_id)
        except Exception:
            logger.warning(f"Most Active: could not resolve channel {root_id} for scan.")
            return
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return

    sources: list = [channel]
    sources.extend(getattr(channel, "threads", None) or [])
    if include_archived_threads and isinstance(channel, discord.TextChannel):
        try:
            async for th in channel.archived_threads(limit=None):
                if th.archive_timestamp is None or th.archive_timestamp >= cutoff:
                    sources.append(th)
        except Exception:
            logger.warning(f"Most Active: could not list archived threads of {root_id}.")

    for src in sources:
        try:
            async for msg in src.history(limit=None, after=cutoff):
                stats["scanned"] += 1
                day = msg.created_at.astimezone(datetime.timezone.utc).date().isoformat()
                if floor_day and day < floor_day:
                    continue
                if not msg.author.bot and is_meaningful(msg.content):
                    buckets[(root_id, msg.author.id, day)][0] += 1
                    stats["messages"] += 1
                if include_reactions:
                    for reaction in msg.reactions:
                        try:
                            async for user in reaction.users():
                                if user.bot:
                                    continue
                                buckets[(root_id, user.id, day)][1] += 1
                                stats["reactions"] += 1
                        except Exception:
                            # One unreadable reaction must not abort the scan.
                            continue
                if stats["scanned"] % 500 == 0:
                    logger.info(f"Most Active: scan progress — {stats['scanned']} messages so far…")
        except discord.Forbidden:
            logger.warning(f"Most Active: missing read access to {getattr(src, 'id', root_id)}.")
        except Exception as e:
            logger.error(f"Most Active: error scanning {getattr(src, 'id', root_id)}: {e}")


async def _merge_buckets(bot: commands.Bot, guild_id: int, buckets: dict) -> None:
    """MAX-merges scanned counts into ``engagement_daily`` (idempotent —
    re-running a scan never inflates a day that live tracking already covers)."""
    for (root_id, user_id, day), (m, r) in buckets.items():
        await bot.database.execute(
            "INSERT INTO engagement_daily (guild_id, channel_id, user_id, day, messages, reactions) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id, channel_id, user_id, day) DO UPDATE SET "
            "messages = MAX(messages, excluded.messages), "
            "reactions = MAX(reactions, excluded.reactions)",
            (guild_id, root_id, user_id, day, m, r),
        )


async def backfill_engagement(bot: commands.Bot, guild: discord.Guild, days: int = BACKFILL_DAYS) -> dict | None:
    """One-time deep history seed: messages + reactions + archived threads.

    This is the old hours-long scan, but it now runs once, writes per-day
    aggregates, and never needs to run again. Returns a stats dict, or None
    if a backfill is already running for the guild.
    """
    if guild.id in _BACKFILL_RUNNING:
        return None
    _BACKFILL_RUNNING.add(guild.id)
    started = discord.utils.utcnow()
    await _set_meta(bot, guild.id, "engagement_backfill", json.dumps({
        "state": "running", "started": started.isoformat(), "days": days,
    }))

    try:
        cutoff = started - datetime.timedelta(days=days)
        # Never reach back past the start of the current engagement period.
        period_start = await get_period_start(bot, guild.id)
        floor_dt = _period_start_dt(period_start)
        if floor_dt and cutoff < floor_dt:
            cutoff = floor_dt
        floor_day = period_start[:10] if period_start else None
        buckets: dict = defaultdict(lambda: [0, 0])
        stats = {"scanned": 0, "messages": 0, "reactions": 0}

        await asyncio.gather(*[
            _scan_root_channel(
                guild, cid, cutoff, buckets, stats,
                include_reactions=True, include_archived_threads=True,
                floor_day=floor_day,
            )
            for cid in _tracked_ids()
        ])
        await _merge_buckets(bot, guild.id, buckets)

        await _set_meta(bot, guild.id, "engagement_backfill", json.dumps({
            "state": "done",
            "started": started.isoformat(),
            "finished": discord.utils.utcnow().isoformat(),
            "days": days,
            **stats,
        }))
        logger.info(f"Most Active: backfill complete for guild {guild.id}: {stats}")
        return stats
    except Exception as e:
        logger.error(f"Most Active: backfill failed for guild {guild.id}: {e}")
        await _set_meta(bot, guild.id, "engagement_backfill", json.dumps({
            "state": "failed", "started": started.isoformat(), "error": str(e)[:200],
        }))
        return None
    finally:
        _BACKFILL_RUNNING.discard(guild.id)


async def catchup_recent_messages(bot: commands.Bot, guild: discord.Guild) -> None:
    """Cheap messages-only scan covering the window since the last *completed*
    catch-up (capped), so downtime doesn't leave holes in the data. Reactions
    made while offline are skipped — per-day attribution isn't recoverable.

    The window is sized from the ``engagement_catchup_through`` meta marker,
    NOT from the newest row in ``engagement_daily`` — live listeners write
    today's rows before this runs, which would make the table look current and
    shrink the window to a single day.
    """
    through = await _get_meta(bot, guild.id, "engagement_catchup_through")
    if through:
        try:
            gap = (_utc_today() - datetime.date.fromisoformat(through)).days + 1
        except Exception:
            gap = CATCHUP_MAX_DAYS
        days = max(1, min(gap, CATCHUP_MAX_DAYS))
    else:
        days = CATCHUP_MAX_DAYS  # first run: seed recent message history

    cutoff = discord.utils.utcnow() - datetime.timedelta(days=days)
    # Never reach back past the start of the current engagement period (a reset
    # marks this instant; otherwise the gap window would resurrect old history).
    period_start = await get_period_start(bot, guild.id)
    floor_dt = _period_start_dt(period_start)
    if floor_dt and cutoff < floor_dt:
        cutoff = floor_dt
    floor_day = period_start[:10] if period_start else None
    buckets: dict = defaultdict(lambda: [0, 0])
    stats = {"scanned": 0, "messages": 0, "reactions": 0}

    await asyncio.gather(*[
        _scan_root_channel(
            guild, cid, cutoff, buckets, stats,
            include_reactions=False, include_archived_threads=False,
            floor_day=floor_day,
        )
        for cid in _tracked_ids()
    ])
    await _merge_buckets(bot, guild.id, buckets)
    await _set_meta(bot, guild.id, "engagement_catchup_through", _utc_today().isoformat())
    logger.info(
        f"Most Active: catch-up scan merged {days}d window for guild {guild.id} "
        f"({stats['scanned']} messages scanned)."
    )


# ═══════════════════════════════════════════════
#  Embeds
# ═══════════════════════════════════════════════

def _report_title(timeframe: str, custom_label: str | None = None) -> str:
    """Plain, timeframe-specific report title, e.g. 'IM8 Weekly Most Active Report'."""
    if custom_label is not None:
        return "IM8 Custom Most Active Report"
    spec = TIMEFRAMES.get(timeframe, TIMEFRAMES["monthly"])
    return f"IM8 {spec['report_name']} Most Active Report"


def build_placeholder_embed(timeframe: str, custom_label: str | None = None) -> discord.Embed:
    """A brief placeholder shown the instant a board is posted."""
    window = custom_label or TIMEFRAMES.get(timeframe, TIMEFRAMES["monthly"])["label"]
    embed = discord.Embed(
        title=_report_title(timeframe, custom_label),
        description=f"**{window}**\n\n⏳ Loading rankings…",
        color=0xF1C40F,
    )
    embed.set_footer(text="IM8 Health • Most Active")
    return embed


def build_leaderboard_embed(
    guild: discord.Guild,
    ranked: list[tuple[int, dict]],
    timeframe: str,
    coverage: str | None = None,
    custom_label: str | None = None,
) -> discord.Embed:
    """Builds the public-facing leaderboard embed.

    ``custom_label`` (e.g. '2026-05-01 → 2026-05-31') renders a custom-range
    report; otherwise the window comes from ``TIMEFRAMES[timeframe]``.
    """
    spec = TIMEFRAMES.get(timeframe, TIMEFRAMES["monthly"])
    if custom_label is not None:
        window = custom_label
    else:
        window = spec["label"]
        if timeframe == "alltime" and coverage:
            window += f" (since {coverage})"

    embed = discord.Embed(
        title=_report_title(timeframe, custom_label),
        description=(
            f"Top **{LEADERBOARD_SIZE}** most active members • **{window}**\n"
            f"`{POINTS_PER_MESSAGE} pts` per message  •  `{POINTS_PER_REACTION} pts` per reaction"
        ),
        color=0x00C9A7,
    )

    medals = ["🥇", "🥈", "🥉"]
    if not ranked:
        embed.add_field(
            name="No activity yet",
            value="No qualifying engagement was recorded in this period.",
            inline=False,
        )
    else:
        lines = []
        for idx, (user_id, data) in enumerate(ranked):
            rank = medals[idx] if idx < 3 else f"`#{idx + 1}`"
            member = guild.get_member(user_id)
            name = member.mention if member else f"<@{user_id}>"
            lines.append(
                f"{rank} {name} — **{data['points']:,} pts**\n"
                f"      └ {data['messages']} msgs · {data['reactions']} reactions"
            )
        embed.add_field(name="Rankings", value="\n".join(lines), inline=False)

    embed.set_footer(text=f"IM8 Health • Most Active • Auto-refreshes every {REFRESH_HOURS}h")
    embed.timestamp = discord.utils.utcnow()
    return embed


# Bar glyphs for the member-stats 14-day sparkline.
_SPARK_BARS = "▁▂▃▄▅▆▇█"


def _sparkline(series: list[tuple[str, int]]) -> str:
    peak = max((v for _, v in series), default=0)
    if peak <= 0:
        return "▁" * len(series)
    return "".join(_SPARK_BARS[min(int(v / peak * (len(_SPARK_BARS) - 1)), 7)] for _, v in series)


def build_member_stats_embed(guild: discord.Guild, user: discord.abc.User, stats: dict) -> discord.Embed:
    """Per-member engagement breakdown across all timeframes."""
    embed = discord.Embed(
        title="🔎 Member Engagement Stats",
        description=f"Engagement for {user.mention} across the tracked community channels.",
        color=0x00C9A7,
    )
    embed.set_thumbnail(url=user.display_avatar.url)

    if _is_excluded(guild, user.id):
        embed.add_field(
            name="⚠️ Excluded",
            value="This member holds an excluded staff role and does not appear on leaderboards.",
            inline=False,
        )

    lines = ["Period       │ Rank      │ Pts    │ Msgs  │ Reacts", "─────────────┼───────────┼────────┼───────┼───────"]
    for tf, spec in TIMEFRAMES.items():
        data = stats["timeframes"].get(tf)
        if data:
            rank = f"#{data['rank']}/{data['of']}"
            lines.append(
                f"{spec['label']:<12} │ {rank:<9} │ {data['points']:>6,} │ {data['messages']:>5} │ {data['reactions']:>5}"
            )
        else:
            lines.append(f"{spec['label']:<12} │ {'—':<9} │ {'0':>6} │ {'0':>5} │ {'0':>5}")
    embed.add_field(name="📊 Breakdown", value="```\n" + "\n".join(lines) + "\n```", inline=False)

    series = stats["series"]
    embed.add_field(
        name="📈 Last 14 Days (points/day)",
        value=(
            f"`{_sparkline(series)}`\n"
            f"{series[0][0]} → {series[-1][0]}  •  "
            f"peak **{max((v for _, v in series), default=0):,} pts/day**"
        ),
        inline=False,
    )

    embed.set_footer(text="IM8 Health • Most Active")
    embed.timestamp = discord.utils.utcnow()
    return embed


def build_channel_insights_embed(guild: discord.Guild, insights: dict) -> discord.Embed:
    """Where the community is most active, per tracked channel."""
    embed = discord.Embed(
        title="📡 Channel Insights",
        description=f"Engagement by tracked channel • **Last {insights['days']} days**",
        color=0x00C9A7,
    )

    if not insights["channels"]:
        embed.add_field(
            name="No data yet",
            value="No engagement has been recorded in this window.",
            inline=False,
        )
    else:
        lines = []
        total_msgs = sum(c["m"] or 0 for c in insights["channels"]) or 1
        for c in insights["channels"]:
            ch = guild.get_channel(c["channel_id"])
            name = ch.mention if ch else f"<#{c['channel_id']}>"
            share = (c["m"] or 0) / total_msgs * 100
            lines.append(
                f"{name} — **{c['m'] or 0:,} msgs** ({share:.0f}%) · "
                f"{c['r'] or 0:,} reactions · {c['members']} active members"
            )
        embed.add_field(name="Channels", value="\n".join(lines), inline=False)

    if insights["busiest"]:
        embed.add_field(
            name="🔥 Busiest Day",
            value=f"`{insights['busiest']['day']}` with **{insights['busiest']['m']:,} meaningful messages**",
            inline=False,
        )

    embed.set_footer(text="IM8 Health • Most Active")
    embed.timestamp = discord.utils.utcnow()
    return embed


def _fmt_channels(guild: discord.Guild) -> str:
    """Renders the tracked-channel list as mentions."""
    parts = []
    for cid in TRACKED_CHANNELS:
        ch = guild.get_channel(cid)
        parts.append(ch.mention if ch else f"<#{cid}>")
    return " ".join(parts)


async def build_hub_embed(bot: commands.Bot, guild: discord.Guild) -> discord.Embed:
    """Builds the detailed control-hub overview embed."""
    embed = discord.Embed(
        title="📊 Most Active • Engagement Tracking",
        description=(
            "Ranks the most engaged members across the community channels using a "
            "weighted point system. Engagement is tracked **live** as it happens, so "
            "rankings are instant. Low-effort messages (e.g. `ok`, `hi`, emoji-only) "
            "are filtered out so points reflect real participation."
        ),
        color=0x00C9A7,
    )

    embed.add_field(
        name="📡 Tracked Channels",
        value=_fmt_channels(guild) + "  *(threads included)*",
        inline=False,
    )
    excluded_str = " ".join(f"<@&{rid}>" for rid in EXCLUDED_ROLE_IDS) or "none"
    embed.add_field(
        name="🎯 Point System",
        value=(
            f"```\n"
            f"Message   │ +{POINTS_PER_MESSAGE} pts (meaningful only)\n"
            f"Reaction  │ +{POINTS_PER_REACTION} pts (per reaction given)\n"
            f"Leaderboard size │ Top {LEADERBOARD_SIZE}\n"
            f"```"
            f"Excluded staff roles: {excluded_str}"
        ),
        inline=False,
    )

    # Live data coverage + backfill status.
    coverage = await get_coverage_start(bot, guild.id)
    bf = await get_backfill_state(bot, guild.id)
    if bf and bf.get("state") == "running":
        bf_line = "⏳ History backfill **running now** — older history is filling in."
    elif bf and bf.get("state") == "done":
        bf_line = (
            f"✅ History backfilled ({bf.get('days', '?')} days · "
            f"{bf.get('messages', 0):,} msgs · {bf.get('reactions', 0):,} reactions)."
        )
    elif bf and bf.get("state") == "failed":
        bf_line = "❌ Last backfill failed — try *Backfill History* again."
    else:
        bf_line = (
            "💡 The one-time history seed (older messages **and** reactions) starts "
            "automatically on boot — or run *Backfill History* to trigger it now."
        )
    embed.add_field(
        name="🗃️ Data Coverage",
        value=(
            (f"Engagement data since `{coverage}`." if coverage else "No engagement recorded yet.")
            + f"\n{bf_line}"
        ),
        inline=False,
    )

    # Current public leaderboard status (several boards may run side by side,
    # including multiple of the same timeframe via the Update Leaderboard tool).
    rows = await bot.database.fetch_all(
        "SELECT * FROM active_leaderboards WHERE guild_id = ?", (guild.id,)
    )
    if rows:
        lines = []
        for row in rows:
            ch = guild.get_channel(row["channel_id"])
            ch_str = ch.mention if ch else f"<#{row['channel_id']}>"
            ts = _parse_sql_ts(row["updated_at"]) if row["updated_at"] else None
            when = f"<t:{ts}:R>" if ts else "unknown"
            rs, re = _row_get(row, "range_start"), _row_get(row, "range_end")
            if row["timeframe"] == "custom" and rs and re:
                label = f"Custom ({rs} → {re})"
            else:
                label = TIMEFRAMES.get(row["timeframe"], {}).get("report_name", row["timeframe"])
            tag = " • adopted" if _row_get(row, "source") == "adopt" else ""
            lines.append(f"🟢 **{label}** in {ch_str} • updated {when}{tag}")
        status = "\n".join(lines) + f"\n*Auto-refreshes every **{REFRESH_HOURS}h** and on every bot restart.*"
    else:
        status = "⚪ **Not deployed.** Use the *Post* dropdown below to publish one."
    embed.add_field(name="🏆 Public Reports", value=status, inline=False)

    embed.add_field(
        name="🛠️ Available Actions",
        value=(
            "**Daily / Weekly / Monthly / All-Time** — preview a report privately.\n"
            "**Custom Range** — preview any date range (YYYY-MM-DD).\n"
            "**Post** — publish a public, auto-refreshing report (any timeframe can run side by side).\n"
            "**Update Leaderboard** — adopt message(s) the bot already posted so they auto-refresh "
            "(point it at a board orphaned by *Remove*; add as many as you like).\n"
            "**Member Stats** — a member's points, rank, and 14-day trend.\n"
            "**Channel Insights** — where the community is most active.\n"
            "**Backfill History** — one-time deep scan to seed old messages + reactions.\n"
            "**Refresh Now / Remove** — manage the live reports.\n"
            "**🏟️ Enter the Arena** — turn this engagement into a live competition: "
            "seasons, tiers, reward roles, streaks, badges & champions."
        ),
        inline=False,
    )

    embed.set_footer(text="IM8 Health • Most Active")
    return embed


# ═══════════════════════════════════════════════
#  Refresh logic (shared by scheduler + UI)
# ═══════════════════════════════════════════════

def _row_get(row, key):
    """Safe column access for aiosqlite.Row / dict-like rows (None if absent)."""
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        return None


async def build_board_embed_for(bot: commands.Bot, guild: discord.Guild, row) -> discord.Embed | None:
    """Builds the leaderboard embed for a stored board row (custom-range aware).

    Returns None for an unrecognised timeframe with no stored range, so the
    caller can prune it instead of silently rendering the wrong window.
    """
    tf_key = row["timeframe"]
    rs, re = _row_get(row, "range_start"), _row_get(row, "range_end")

    if tf_key == "custom" or (rs and re):
        if not (rs and re):
            return None
        ranked = await compute_engagement_range(bot, guild, rs, re)
        return build_leaderboard_embed(guild, ranked, "custom", custom_label=f"{rs} → {re}")

    if tf_key in TIMEFRAMES:
        ranked = await compute_engagement(bot, guild, TIMEFRAMES[tf_key]["days"])
        coverage = await get_coverage_start(bot, guild.id)
        return build_leaderboard_embed(guild, ranked, tf_key, coverage)

    return None


async def deploy_board(
    bot: commands.Bot,
    guild: discord.Guild,
    channel,
    timeframe: str,
    range_start: str | None = None,
    range_end: str | None = None,
) -> discord.Message:
    """Posts a fully-populated public board and stores its config. Returns the message."""
    if timeframe == "custom":
        ranked = await compute_engagement_range(bot, guild, range_start, range_end)
        embed = build_leaderboard_embed(guild, ranked, "custom", custom_label=f"{range_start} → {range_end}")
    else:
        ranked = await compute_engagement(bot, guild, TIMEFRAMES[timeframe]["days"])
        coverage = await get_coverage_start(bot, guild.id)
        embed = build_leaderboard_embed(guild, ranked, timeframe, coverage)

    msg = await channel.send(embed=embed)
    # A Post keeps one canonical board per timeframe: replace any prior *post*
    # board of this timeframe (adopted boards are independent and left alone).
    await bot.database.execute(
        "DELETE FROM active_leaderboards WHERE guild_id = ? AND timeframe = ? AND source = 'post'",
        (guild.id, timeframe),
    )
    await bot.database.execute(
        "INSERT OR REPLACE INTO active_leaderboards "
        "(guild_id, timeframe, channel_id, message_id, range_start, range_end, source, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'post', datetime('now'))",
        (guild.id, timeframe, channel.id, msg.id, range_start, range_end),
    )
    return msg


# ── Adopt an existing bot message as a live board ──
# Used by the "Update Leaderboard" tool: instead of posting a *new* board, point
# the auto-refresh at a leaderboard message the bot already sent (e.g. a board
# orphaned by "Remove Boards", which leaves the message intact). Discord only
# lets a bot edit messages it authored, so adoption is restricted to bot
# messages.

# Timeframes that can be adopted. 'custom' is excluded — it needs a date range
# the rolling adopt flow doesn't collect.
ADOPTABLE_TIMEFRAMES = ["daily", "weekly", "monthly", "alltime"]

_MSG_LINK_RE = re.compile(r"channels/(\d+)/(\d+)/(\d+)")


async def _fetch_message_in_channel(
    guild: discord.Guild, channel_id: int, message_id: int
) -> "discord.Message | str":
    """Fetches a message from a known channel id. Returns the message or an error string."""
    channel = guild.get_channel(channel_id) or guild.get_thread(channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except Exception:
            return f"I can't see a channel with id `{channel_id}` in this server."
    try:
        return await channel.fetch_message(message_id)
    except discord.NotFound:
        return f"No message `{message_id}` exists in {getattr(channel, 'mention', channel_id)}."
    except discord.Forbidden:
        return f"I can't read messages in {getattr(channel, 'mention', channel_id)}."
    except Exception as e:
        return f"Couldn't fetch that message: {e}"


async def _search_message_by_id(guild: discord.Guild, message_id: int) -> "discord.Message | str":
    """Best-effort hunt for a message by id across readable channels/threads.

    A bare message id is ambiguous (Discord needs a channel to fetch it), so we
    probe each text channel and active thread until one returns the message.
    This is an occasional mod action, so the extra fetches are acceptable;
    pasting the full message link skips this entirely.
    """
    sources: list = list(guild.text_channels)
    sources.extend(guild.threads)
    for src in sources:
        try:
            return await src.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            continue
        except Exception:
            continue
    return (
        f"Couldn't find message `{message_id}` in any channel I can read. "
        "Paste the full message **link** (right-click the message → *Copy Message Link*) instead."
    )


async def resolve_message_reference(guild: discord.Guild, raw: str) -> "discord.Message | str":
    """Resolves a user-supplied message reference to a Message (or an error string).

    Accepts a full message link, the ``channelID-messageID`` shift-click form, or
    a bare message id (searched across channels as a fallback).
    """
    raw = (raw or "").strip()
    if not raw:
        return "Please provide a message link or id."

    link = _MSG_LINK_RE.search(raw)
    if link:
        g_id, ch_id, msg_id = int(link.group(1)), int(link.group(2)), int(link.group(3))
        if g_id != guild.id:
            return "That message link points to a different server."
        return await _fetch_message_in_channel(guild, ch_id, msg_id)

    if "-" in raw:
        left, _, right = raw.partition("-")
        if left.strip().isdigit() and right.strip().isdigit():
            return await _fetch_message_in_channel(guild, int(left.strip()), int(right.strip()))

    if raw.isdigit():
        return await _search_message_by_id(guild, int(raw))

    return "Couldn't read that. Paste a message **link** or its **ID**."


async def adopt_board(
    bot: commands.Bot, guild: discord.Guild, message: discord.Message, timeframe: str
) -> None:
    """Binds an existing bot message as a live board: fills it now and registers
    it for auto-refresh. The caller must first confirm the message was authored
    by the bot (Discord only lets a bot edit its own messages)."""
    ranked = await compute_engagement(bot, guild, TIMEFRAMES[timeframe]["days"])
    coverage = await get_coverage_start(bot, guild.id)
    embed = build_leaderboard_embed(guild, ranked, timeframe, coverage)
    await message.edit(embed=embed)
    await bot.database.execute(
        "INSERT OR REPLACE INTO active_leaderboards "
        "(guild_id, timeframe, channel_id, message_id, range_start, range_end, source, updated_at) "
        "VALUES (?, ?, ?, ?, NULL, NULL, 'adopt', datetime('now'))",
        (guild.id, timeframe, message.channel.id, message.id),
    )


async def refresh_one_leaderboard(bot: commands.Bot, row) -> bool:
    """Refreshes a single stored leaderboard. Returns True on success.

    Prunes the config if the channel/message no longer exists or the stored
    timeframe is unrecognised.
    """
    guild = bot.get_guild(row["guild_id"])
    if guild is None:
        return False

    tf_key = row["timeframe"]

    async def _prune() -> None:
        # Key on message_id: several boards may share a timeframe now, so prune
        # only the one broken board, never its siblings.
        await bot.database.execute(
            "DELETE FROM active_leaderboards WHERE guild_id = ? AND message_id = ?",
            (row["guild_id"], row["message_id"]),
        )

    channel = guild.get_channel(row["channel_id"])
    if channel is None:
        try:
            channel = await guild.fetch_channel(row["channel_id"])
        except Exception:
            logger.warning(f"Most Active: leaderboard channel {row['channel_id']} missing; removing config.")
            await _prune()
            return False

    try:
        message = await channel.fetch_message(row["message_id"])
    except discord.NotFound:
        logger.warning(f"Most Active: leaderboard message {row['message_id']} deleted; removing config.")
        await _prune()
        return False
    except Exception as e:
        logger.error(f"Most Active: could not fetch leaderboard message: {e}")
        return False

    embed = await build_board_embed_for(bot, guild, row)
    if embed is None:
        logger.warning(f"Most Active: unrecognised board timeframe '{tf_key}'; removing config.")
        await _prune()
        return False

    try:
        await message.edit(embed=embed)
        await bot.database.execute(
            "UPDATE active_leaderboards SET updated_at = datetime('now') "
            "WHERE guild_id = ? AND message_id = ?",
            (row["guild_id"], row["message_id"]),
        )
        logger.info(f"Most Active: refreshed {tf_key} leaderboard in guild {guild.id}.")
        return True
    except discord.NotFound:
        # Message was deleted between fetch and edit — prune the stale config.
        logger.warning(f"Most Active: leaderboard message {row['message_id']} vanished; removing config.")
        await _prune()
        return False
    except Exception as e:
        logger.error(f"Most Active: failed to edit leaderboard message: {e}")
        return False


# ── Background task tracking (keeps fire-and-forget tasks from being GC'd) ──
_BG_TASKS: set = set()


def _spawn(coro) -> "asyncio.Task":
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


# ═══════════════════════════════════════════════
#  UI
# ═══════════════════════════════════════════════

class MemberStatsView(discord.ui.View):
    """Ephemeral picker for the Member Stats lookup (not persistent)."""

    def __init__(self) -> None:
        super().__init__(timeout=300)

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="🔎 Pick a member to inspect",
        min_values=1,
        max_values=1,
    )
    async def pick_member(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        await interaction.response.defer(ephemeral=True)
        user = select.values[0]
        stats = await get_member_stats(interaction.client, interaction.guild, user.id)
        embed = build_member_stats_embed(interaction.guild, user, stats)
        await interaction.followup.send(embed=embed, ephemeral=True)


# Post-timeframe options shared by the preview buttons and the post dropdown.
_MAX_CUSTOM_RANGE_DAYS = 366


def _parse_range_inputs(start_raw: str, end_raw: str) -> tuple[str, str] | str:
    """Validates two date strings. Returns (start_iso, end_iso) or an error string."""
    try:
        sd = datetime.date.fromisoformat(start_raw.strip())
        ed = datetime.date.fromisoformat(end_raw.strip())
    except ValueError:
        return "Dates must be in `YYYY-MM-DD` format (e.g. `2026-05-01`)."
    if sd > ed:
        sd, ed = ed, sd  # tolerate reversed input
    today = _utc_today()
    if ed > today:
        ed = today  # no future data exists
    if sd > today:
        return "The start date is in the future — there is no data to report."
    if (ed - sd).days > _MAX_CUSTOM_RANGE_DAYS:
        return f"Range too large — keep it within {_MAX_CUSTOM_RANGE_DAYS} days."
    return sd.isoformat(), ed.isoformat()


class PostBoardView(discord.ui.View):
    """Ephemeral channel picker that posts a board for a chosen timeframe/range."""

    def __init__(self, timeframe: str, range_start: str | None = None, range_end: str | None = None) -> None:
        super().__init__(timeout=300)
        self.timeframe = timeframe
        self.range_start = range_start
        self.range_end = range_end

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="📢 Pick a channel to post to",
        min_values=1,
        max_values=1,
    )
    async def pick_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        app_channel = select.values[0]
        target = interaction.guild.get_channel(app_channel.id)
        if target is None:
            try:
                target = await interaction.guild.fetch_channel(app_channel.id)
            except Exception as e:
                return await interaction.response.send_message(f"❌ Could not resolve channel: {e}", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        try:
            msg = await deploy_board(
                interaction.client, interaction.guild, target,
                self.timeframe, self.range_start, self.range_end,
            )
        except Exception as e:
            return await interaction.followup.send(
                f"❌ Failed to post report to {target.mention}: {e}", ephemeral=True
            )

        if self.timeframe == "custom":
            what = f"Custom range ({self.range_start} → {self.range_end})"
        else:
            what = f"{TIMEFRAMES[self.timeframe]['report_name']}"
        await interaction.followup.send(
            f"✅ **IM8 {what} Most Active Report** posted in {target.mention} → {msg.jump_url}\n"
            f"It auto-refreshes every **{REFRESH_HOURS}h** and on every bot restart.",
            ephemeral=True,
        )


class CustomRangeModal(discord.ui.Modal, title="Custom Date Range"):
    """Collects a start/end date for a custom Most Active report."""

    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode  # 'preview' or 'post'
        self.start = discord.ui.TextInput(
            label="Start date (YYYY-MM-DD)", placeholder="2026-05-01", required=True, max_length=10,
        )
        self.end = discord.ui.TextInput(
            label="End date (YYYY-MM-DD)", placeholder="2026-05-31", required=True, max_length=10,
        )
        self.add_item(self.start)
        self.add_item(self.end)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        parsed = _parse_range_inputs(self.start.value, self.end.value)
        if isinstance(parsed, str):
            return await interaction.response.send_message(f"❌ {parsed}", ephemeral=True)
        start_iso, end_iso = parsed

        if self.mode == "post":
            return await interaction.response.send_message(
                f"📢 Posting a **Custom** report for `{start_iso} → {end_iso}`. Pick a channel:",
                view=PostBoardView("custom", start_iso, end_iso),
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)
        ranked = await compute_engagement_range(interaction.client, interaction.guild, start_iso, end_iso)
        embed = build_leaderboard_embed(
            interaction.guild, ranked, "custom", custom_label=f"{start_iso} → {end_iso}"
        )
        await interaction.followup.send(
            content=f"**IM8 Custom Most Active Report** — preview *(only you can see this).*",
            embed=embed,
            ephemeral=True,
        )


class AddMsgIdModal(discord.ui.Modal, title="Add Leaderboard Message"):
    """Collects one message reference to adopt under the builder's timeframe."""

    def __init__(self, parent_view: "UpdateBoardView") -> None:
        super().__init__()
        self.parent_view = parent_view
        self.ref = discord.ui.TextInput(
            label="Message link or ID",
            placeholder="Right-click the message → Copy Message Link, or paste its ID",
            required=True,
            max_length=200,
        )
        self.add_item(self.ref)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Resolving a bare id may probe several channels, so defer first.
        await interaction.response.defer(ephemeral=True)
        view = self.parent_view
        tf = view.timeframe

        resolved = await resolve_message_reference(interaction.guild, self.ref.value)
        if isinstance(resolved, str):
            return await interaction.followup.send(f"❌ {resolved}", ephemeral=True)
        message = resolved

        if message.author.id != interaction.client.user.id:
            return await interaction.followup.send(
                "❌ I can only auto-update messages **I** posted. Pick a leaderboard message that "
                "this bot sent (e.g. one it posted earlier via **Post**, even if its auto-refresh "
                "was later removed).",
                ephemeral=True,
            )

        try:
            await adopt_board(interaction.client, interaction.guild, message, tf)
        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ I don't have permission to edit that message.", ephemeral=True
            )
        except Exception as e:
            return await interaction.followup.send(f"❌ Failed to adopt that message: {e}", ephemeral=True)

        view.adopted.append((tf, message.jump_url))
        await view.sync_message()
        await interaction.followup.send(
            f"✅ Now auto-updating {message.jump_url} as the **{TIMEFRAMES[tf]['report_name']}** "
            f"leaderboard (refreshes every {REFRESH_HOURS}h). Use **➕ Add MSG ID** to add another.",
            ephemeral=True,
        )


class _TimeframeSelect(discord.ui.Select):
    """Chooses which timeframe the next adopted message(s) will display."""

    def __init__(self, current: str) -> None:
        options = [
            discord.SelectOption(
                label=TIMEFRAMES[tf]["report_name"],
                value=tf,
                emoji=TIMEFRAMES[tf]["emoji"],
                default=(tf == current),
            )
            for tf in ADOPTABLE_TIMEFRAMES
        ]
        super().__init__(
            placeholder="Which leaderboard should these messages show?",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view  # capture before rebuild() clears items
        view.timeframe = self.values[0]
        view.rebuild()
        await interaction.response.edit_message(content=view.content(), view=view)


class _AddMsgButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Add MSG ID", emoji="➕", style=discord.ButtonStyle.primary, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AddMsgIdModal(self.view))


class _DoneButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Done", emoji="✅", style=discord.ButtonStyle.success, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        count = len(view.adopted)
        if count:
            summary = f"✅ **{count}** leaderboard message(s) are now auto-updating every {REFRESH_HOURS}h."
        else:
            summary = "Closed — no messages were adopted."
        view.stop()
        await interaction.response.edit_message(content=summary, view=None)


class UpdateBoardView(discord.ui.View):
    """Ephemeral builder for adopting existing bot messages as live boards.

    Pick a timeframe, then add one or more message references; each becomes its
    own independent auto-refreshing board (several of the same timeframe are
    allowed). Not persistent — it's a short-lived per-mod workflow.
    """

    def __init__(self, timeframe: str = "weekly") -> None:
        super().__init__(timeout=600)
        self.timeframe = timeframe
        self.adopted: list[tuple[str, str]] = []  # (timeframe, jump_url)
        self.message: discord.Message | None = None  # the ephemeral builder message
        self.rebuild()

    def rebuild(self) -> None:
        """Re-creates components so the timeframe select reflects the current choice."""
        self.clear_items()
        self.add_item(_TimeframeSelect(self.timeframe))
        self.add_item(_AddMsgButton())
        self.add_item(_DoneButton())

    def content(self) -> str:
        lines = [
            "**🔁 Update Leaderboard — adopt existing messages**",
            "Point me at a leaderboard message **I already posted** and I'll fill it now and keep it "
            f"updated every **{REFRESH_HOURS}h**. Pick the timeframe, then **➕ Add MSG ID** (repeat for more).",
            "",
            f"🗓️ Current timeframe: **{TIMEFRAMES[self.timeframe]['report_name']}**",
        ]
        if self.adopted:
            lines.append("")
            lines.append("__Now auto-updating:__")
            for tf, url in self.adopted:
                lines.append(f"• **{TIMEFRAMES[tf]['report_name']}** → {url}")
        return "\n".join(lines)

    async def sync_message(self) -> None:
        """Re-renders the builder message after an adoption (best-effort)."""
        if self.message is None:
            return
        try:
            await self.message.edit(content=self.content(), view=self)
        except Exception as e:
            logger.error(f"Most Active: failed to update Update-Leaderboard builder: {e}")


class MostActiveHubView(discord.ui.View):
    """The Most Active control hub, opened from the Mod Panel."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def _refresh_hub(self, interaction: discord.Interaction) -> None:
        """Re-renders the hub message (overview embed + reset dropdowns)."""
        try:
            embed = await build_hub_embed(interaction.client, interaction.guild)
            await interaction.message.edit(content=None, embed=embed, view=MostActiveHubView())
        except Exception as e:
            logger.error(f"Most Active: failed to refresh hub: {e}")

    # ── Row 0: Preview a report (instant, private) ──
    async def _detect(self, interaction: discord.Interaction, timeframe: str) -> None:
        await interaction.response.defer(ephemeral=True)
        ranked = await compute_engagement(interaction.client, interaction.guild, TIMEFRAMES[timeframe]["days"])
        coverage = await get_coverage_start(interaction.client, interaction.guild.id)
        embed = build_leaderboard_embed(interaction.guild, ranked, timeframe, coverage)
        await interaction.followup.send(
            content=f"**IM8 {TIMEFRAMES[timeframe]['report_name']} Most Active Report** — preview *(only you can see this).*",
            embed=embed,
            ephemeral=True,
        )

    @discord.ui.button(label="Daily", emoji="☀️", style=discord.ButtonStyle.primary, row=0, custom_id="im8_active_daily")
    async def btn_daily(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._detect(interaction, "daily")

    @discord.ui.button(label="Weekly", emoji="📆", style=discord.ButtonStyle.primary, row=0, custom_id="im8_active_weekly")
    async def btn_weekly(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._detect(interaction, "weekly")

    @discord.ui.button(label="Monthly", emoji="🗓️", style=discord.ButtonStyle.primary, row=0, custom_id="im8_active_monthly")
    async def btn_monthly(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._detect(interaction, "monthly")

    @discord.ui.button(label="All-Time", emoji="🏛️", style=discord.ButtonStyle.primary, row=0, custom_id="im8_active_alltime")
    async def btn_alltime(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._detect(interaction, "alltime")

    @discord.ui.button(label="Custom Range", emoji="📅", style=discord.ButtonStyle.secondary, row=0, custom_id="im8_active_custom")
    async def btn_custom(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CustomRangeModal(mode="preview"))

    # ── Row 1: Post / update a public report ──
    @discord.ui.select(
        cls=discord.ui.Select,
        placeholder="📢 Post a public report → pick a timeframe",
        min_values=1,
        max_values=1,
        row=1,
        custom_id="im8_active_post_select",
        options=[
            discord.SelectOption(label="Daily", value="daily", emoji="☀️"),
            discord.SelectOption(label="Weekly", value="weekly", emoji="📆"),
            discord.SelectOption(label="Monthly", value="monthly", emoji="🗓️"),
            discord.SelectOption(label="All-Time", value="alltime", emoji="🏛️"),
            discord.SelectOption(label="Custom Range", value="custom", emoji="📅"),
        ],
    )
    async def select_post(self, interaction: discord.Interaction, select: discord.ui.Select):
        timeframe = select.values[0]
        if timeframe == "custom":
            return await interaction.response.send_modal(CustomRangeModal(mode="post"))
        await interaction.response.send_message(
            f"📢 Posting the **{TIMEFRAMES[timeframe]['report_name']}** report. Pick a channel:",
            view=PostBoardView(timeframe),
            ephemeral=True,
        )

    # ── Row 2: Insights & data tools ──
    @discord.ui.button(label="Member Stats", emoji="🔎", style=discord.ButtonStyle.secondary, row=2, custom_id="im8_active_stats")
    async def btn_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "🔎 Pick a member to see their points, ranks, and 14-day activity trend:",
            view=MemberStatsView(),
            ephemeral=True,
        )

    @discord.ui.button(label="Channel Insights", emoji="📡", style=discord.ButtonStyle.secondary, row=2, custom_id="im8_active_insights")
    async def btn_insights(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        insights = await get_channel_insights(interaction.client, interaction.guild)
        embed = build_channel_insights_embed(interaction.guild, insights)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Backfill History", emoji="⏳", style=discord.ButtonStyle.secondary, row=2, custom_id="im8_active_backfill")
    async def btn_backfill(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild.id in _BACKFILL_RUNNING:
            return await interaction.response.send_message(
                "⏳ A history backfill is already running for this server — check back soon.",
                ephemeral=True,
            )
        await interaction.response.send_message(
            f"⏳ Deep history scan started (last **{BACKFILL_DAYS} days**, messages **and** reactions, "
            "archived threads included). This is the slow, rate-limited scan — but it only ever needs "
            "to run **once**; everything after this is tracked live. Leaderboards will refresh "
            "automatically when it finishes.",
            ephemeral=True,
        )

        async def _run():
            try:
                stats = await backfill_engagement(interaction.client, interaction.guild)
                if stats is None:
                    return
                rows = await interaction.client.database.fetch_all(
                    "SELECT * FROM active_leaderboards WHERE guild_id = ?", (interaction.guild.id,)
                )
                for row in rows:
                    await refresh_one_leaderboard(interaction.client, row)
            except Exception as e:
                logger.error(f"Most Active: backfill task failed: {e}")

        _spawn(_run())
        await self._refresh_hub(interaction)

    # ── Row 3: Manage + navigation ──
    @discord.ui.button(label="Refresh Now", emoji="🔄", style=discord.ButtonStyle.success, row=3, custom_id="im8_active_refresh")
    async def btn_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = await interaction.client.database.fetch_all(
            "SELECT * FROM active_leaderboards WHERE guild_id = ?", (interaction.guild.id,)
        )
        if not rows:
            return await interaction.response.send_message(
                "❌ No public report is deployed yet. Use the *Post* dropdown first.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)
        ok = 0
        for row in rows:
            ok += 1 if await refresh_one_leaderboard(interaction.client, row) else 0
        await interaction.followup.send(
            f"🔄 Refreshed **{ok}/{len(rows)}** live leaderboard(s).", ephemeral=True
        )

    @discord.ui.button(label="Update Leaderboard", emoji="🔁", style=discord.ButtonStyle.primary, row=3, custom_id="im8_active_update")
    async def btn_update(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = UpdateBoardView()
        await interaction.response.send_message(content=view.content(), view=view, ephemeral=True)
        # Keep a handle so the modal can refresh this builder message as boards are added.
        view.message = await interaction.original_response()

    @discord.ui.button(label="Remove Boards", emoji="🗑️", style=discord.ButtonStyle.danger, row=3, custom_id="im8_active_remove")
    async def btn_remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = await interaction.client.database.fetch_all(
            "SELECT * FROM active_leaderboards WHERE guild_id = ?", (interaction.guild.id,)
        )
        if not rows:
            return await interaction.response.send_message(
                "❌ There is no active leaderboard to remove.", ephemeral=True
            )

        await interaction.client.database.execute(
            "DELETE FROM active_leaderboards WHERE guild_id = ?", (interaction.guild.id,)
        )
        await interaction.response.send_message(
            f"🗑️ Auto-refresh stopped for {len(rows)} board(s). The posted messages were left "
            "intact — delete them manually if you wish.",
            ephemeral=True,
        )
        await self._refresh_hub(interaction)

    @discord.ui.button(label="Back to Main Panel", emoji="🔙", style=discord.ButtonStyle.secondary, row=3, custom_id="im8_active_back")
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.panel import ModPanelView, Panel
        embed = await Panel.build_panel_embed(interaction.client, interaction.guild)
        await interaction.response.edit_message(content=None, embed=embed, view=ModPanelView())

    # ── Row 4: the competitive layer ──
    @discord.ui.button(label="Enter the Arena", emoji="🏟️", style=discord.ButtonStyle.primary, row=4, custom_id="im8_active_arena")
    async def btn_arena(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Lazy import avoids any module-load cycle (arena imports primitives from
        # this module at its top level).
        from cogs.arena import ArenaHubView, build_arena_hub_embed
        embed = await build_arena_hub_embed(interaction.client, interaction.guild)
        await interaction.response.edit_message(content=None, embed=embed, view=ArenaHubView())


# ═══════════════════════════════════════════════
#  Cog
# ═══════════════════════════════════════════════

class ActiveCog(commands.Cog):
    """Tracks engagement live and surfaces the most engaged members."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._startup_refreshed = False
        self._startup_task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        # Persistent hub view for restart resilience.
        self.bot.add_view(MostActiveHubView())

        # Schedule the recurring auto-refresh of public leaderboards.
        self.bot.scheduler.add_interval_job(
            self.refresh_all_leaderboards,
            job_id="active_leaderboard_refresh",
            hours=REFRESH_HOURS,
        )
        logger.info("Most Active module loaded; live tracking + leaderboard refresh scheduled.")

    async def cog_unload(self) -> None:
        if self._startup_task and not self._startup_task.done():
            self._startup_task.cancel()

    # ── Live engagement recording ──

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        root = _root_for_channel(message.channel)
        if root is None or not is_meaningful(message.content):
            return
        try:
            day = message.created_at.astimezone(datetime.timezone.utc).date().isoformat()
            await _bump(self.bot, message.guild.id, root, message.author.id, day, d_msg=1)
        except Exception as e:
            logger.error(f"Most Active: failed to record message: {e}")

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        # Only the cached copy tells us who wrote it and whether it counted.
        msg = payload.cached_message
        if msg is None or msg.guild is None or msg.author.bot:
            return
        root = _root_for_channel(msg.channel)
        if root is None or not is_meaningful(msg.content):
            return
        try:
            day = msg.created_at.astimezone(datetime.timezone.utc).date().isoformat()
            await _bump(self.bot, msg.guild.id, root, msg.author.id, day, d_msg=-1)
        except Exception as e:
            logger.error(f"Most Active: failed to record message deletion: {e}")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None or (payload.member and payload.member.bot):
            return
        channel = self.bot.get_channel(payload.channel_id)
        root = _root_for_channel(channel) if channel else None
        if root is None:
            return
        try:
            await _bump(
                self.bot, payload.guild_id, root, payload.user_id,
                _utc_today().isoformat(), d_react=1,
            )
        except Exception as e:
            logger.error(f"Most Active: failed to record reaction: {e}")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        user = (guild.get_member(payload.user_id) if guild else None) or self.bot.get_user(payload.user_id)
        if user is not None and user.bot:
            return
        channel = self.bot.get_channel(payload.channel_id)
        root = _root_for_channel(channel) if channel else None
        if root is None:
            return
        try:
            # Decrement today's row (floored at 0). If the reaction was added on
            # an earlier day the removal is intentionally a no-op for that day.
            await _bump(
                self.bot, payload.guild_id, root, payload.user_id,
                _utc_today().isoformat(), d_react=-1,
            )
        except Exception as e:
            logger.error(f"Most Active: failed to record reaction removal: {e}")

    # ── Startup / scheduled ──

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Refresh once on every bot run (guarded so reconnects don't re-trigger).
        if self._startup_refreshed:
            return
        self._startup_refreshed = True
        self._startup_task = asyncio.create_task(self._delayed_startup_refresh())

    async def _delayed_startup_refresh(self) -> None:
        # Let the gateway settle and the member cache fill before scanning.
        await asyncio.sleep(15)
        try:
            for guild in self.bot.guilds:
                await _ensure_live_since(self.bot, guild.id)
                # Cheap messages-only scan covering any downtime gap, so the
                # aggregates stay continuous without the heavy reaction scan.
                try:
                    await catchup_recent_messages(self.bot, guild)
                except Exception as e:
                    logger.error(f"Most Active: catch-up scan failed for guild {guild.id}: {e}")
            await self.refresh_all_leaderboards()
            logger.info("Most Active: startup catch-up + leaderboard refresh complete.")

            # First boot on this database: the deep history seed (older
            # messages + reactions) has never run, so leaderboard points would
            # look tiny until someone pressed Backfill History. Run it
            # automatically in the background instead — it only happens once.
            for guild in self.bot.guilds:
                if await get_backfill_state(self.bot, guild.id) is None:
                    logger.info(
                        f"Most Active: no history backfill on record for guild {guild.id}; "
                        "starting the one-time deep seed automatically."
                    )
                    _spawn(self._auto_backfill(guild))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Most Active: startup refresh failed: {e}")

    async def _auto_backfill(self, guild: discord.Guild) -> None:
        try:
            stats = await backfill_engagement(self.bot, guild)
            if stats is not None:
                await self.refresh_all_leaderboards()
        except Exception as e:
            logger.error(f"Most Active: automatic backfill failed for guild {guild.id}: {e}")

    async def refresh_all_leaderboards(self) -> None:
        """Updates every configured public leaderboard from the live aggregates."""
        try:
            rows = await self.bot.database.fetch_all("SELECT * FROM active_leaderboards")
        except Exception as e:
            logger.error(f"Most Active: failed to load leaderboard configs: {e}")
            return

        for row in rows:
            await refresh_one_leaderboard(self.bot, row)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ActiveCog(bot))
