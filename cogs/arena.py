"""
IM8 Bot — The Arena (competitive gamification)
Turns the passive "Most Active" leaderboard into a living competitive sport,
built entirely on top of the live ``engagement_daily`` aggregate that the Most
Active module already maintains. There are **no new gateway listeners** here —
every number is computed at READ time from the same per-day message/reaction
counts, so the Arena can never double-count or drift from the leaderboards.

Six pillars (see ``docs/ENGAGEMENT.md`` → Arena):

1. **Seasons** — time-boxed competition windows with a finish line. When a
   season ends it is finalized (champion crowned, badges awarded, standings
   snapshotted to the Hall of Fame) and the next season rolls over.
2. **Ranked tiers + reward roles** — a Bronze→Legend ladder derived from season
   points; optional Discord roles are auto-assigned per tier.
3. **Live rivalry** — gap-to-next-rank, ▲▼ movement since yesterday, and the
   hottest climber, surfaced right on the board.
4. **Power-Up events** — staff launch 2×/3× scoring windows over a day range;
   engagement on those days is worth more in the Arena.
5. **Streaks + badges** — consecutive-day activity streaks and collectible
   achievements, shown on a personal profile card.
6. **Champions finale + Hall of Fame** — a cinematic season-end ceremony plus a
   permanent record of every past champion.

Scoring reuses the Most Active point weights and staff exclusions, so the two
features always agree on "what counts".
"""

import discord
from discord.ext import commands
import logging
import json
import asyncio
import datetime

# Read-time engagement primitives are owned by the Most Active module. Importing
# them here is one-directional (active.py never imports arena.py at module load;
# the Most Active hub lazy-imports the Arena view inside a button callback), so
# there is no import cycle.
from cogs.active import (
    POINTS_PER_MESSAGE,
    POINTS_PER_REACTION,
    _tracked_ids,
    _is_excluded,
    _utc_today,
    _cutoff_day,
    _get_meta,
    _set_meta,
    _parse_sql_ts,
    _row_get,
)

logger = logging.getLogger("im8bot.cogs.arena")

# ═══════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════

# Default length of a season when one is auto-created (tunable per guild via the
# ``arena_season_length`` meta key, settable from the hub).
SEASON_LENGTH_DEFAULT = 30

# How many competitors the public Arena board shows, and how many get the
# podium spotlight.
BOARD_SIZE = 10
PODIUM_SIZE = 3

# Auto-refresh cadence for the public Arena board (instant SQL, so aggressive).
REFRESH_HOURS = 1

# The ranked ladder. ``min`` is the season-points floor for the tier; a member
# is in the highest tier whose floor they have reached. Thresholds are tuned for
# a ~30-day season and can be adjusted freely — they are pure read-time math.
TIERS: list[dict] = [
    {"key": "bronze",   "name": "Bronze",   "emoji": "🥉", "min": 0},
    {"key": "silver",   "name": "Silver",   "emoji": "⚪", "min": 150},
    {"key": "gold",     "name": "Gold",     "emoji": "🥇", "min": 500},
    {"key": "platinum", "name": "Platinum", "emoji": "💠", "min": 1200},
    {"key": "diamond",  "name": "Diamond",  "emoji": "💎", "min": 2500},
    {"key": "legend",   "name": "Legend",   "emoji": "🏆", "min": 5000},
]

# Collectible achievements. ``season`` badges are awarded at season finalize;
# the rest are lifetime (season_no = 0) and awarded live on refresh.
BADGES: dict[str, dict] = {
    "champion":    {"emoji": "👑", "name": "Champion",     "desc": "Won a season outright"},
    "podium":      {"emoji": "🏅", "name": "Podium Finish", "desc": "Finished a season in the top 3"},
    "top10":       {"emoji": "🎖️", "name": "Top 10",        "desc": "Finished a season in the top 10"},
    "competitor":  {"emoji": "⚔️", "name": "Competitor",    "desc": "Scored points in a season"},
    "century":     {"emoji": "💯", "name": "Centurion",     "desc": "Reached 100 points in a season"},
    "high_roller": {"emoji": "💰", "name": "High Roller",    "desc": "Reached 1,000 points in a season"},
    "streak_7":    {"emoji": "🔥", "name": "On Fire",        "desc": "Held a 7-day activity streak"},
    "streak_30":   {"emoji": "🌋", "name": "Unstoppable",    "desc": "Held a 30-day activity streak"},
}

# Movement glyphs for the rivalry indicators.
_UP, _DOWN, _SAME, _NEW = "🔺", "🔻", "▬", "✦"


# ═══════════════════════════════════════════════
#  Date helpers
# ═══════════════════════════════════════════════

def _today_iso() -> str:
    return _utc_today().isoformat()


def _day_add(day_iso: str, delta: int) -> str:
    """Returns ``day_iso`` shifted by ``delta`` days (ISO 'YYYY-MM-DD')."""
    return (datetime.date.fromisoformat(day_iso) + datetime.timedelta(days=delta)).isoformat()


def _days_between(start_iso: str, end_iso: str) -> int:
    """Inclusive day count of the [start, end] window (≥ 1 for valid input)."""
    return (datetime.date.fromisoformat(end_iso) - datetime.date.fromisoformat(start_iso)).days + 1


def _bar(fraction: float, width: int = 12) -> str:
    """A unicode progress bar, e.g. ``▰▰▰▱▱▱`` for fraction 0.5, width 6."""
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return "▰" * filled + "▱" * (width - filled)


# ═══════════════════════════════════════════════
#  Per-guild settings (stored in analytics_meta)
# ═══════════════════════════════════════════════

async def _get_season_length(bot: commands.Bot, guild_id: int) -> int:
    raw = await _get_meta(bot, guild_id, "arena_season_length")
    try:
        return max(1, int(raw)) if raw else SEASON_LENGTH_DEFAULT
    except (TypeError, ValueError):
        return SEASON_LENGTH_DEFAULT


async def _get_announce_channel_id(bot: commands.Bot, guild_id: int) -> int | None:
    raw = await _get_meta(bot, guild_id, "arena_announce_channel")
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


async def _get_tier_roles(bot: commands.Bot, guild_id: int) -> dict[str, int]:
    """Returns the {tier_key|'champion': role_id} reward-role map (may be empty)."""
    raw = await _get_meta(bot, guild_id, "arena_tier_roles")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {str(k): int(v) for k, v in data.items() if v}
    except Exception:
        return {}


async def _set_tier_role(bot: commands.Bot, guild_id: int, tier_key: str, role_id: int | None) -> None:
    roles = await _get_tier_roles(bot, guild_id)
    if role_id:
        roles[tier_key] = role_id
    else:
        roles.pop(tier_key, None)
    await _set_meta(bot, guild_id, "arena_tier_roles", json.dumps(roles))


# ═══════════════════════════════════════════════
#  Tiers
# ═══════════════════════════════════════════════

def tier_for(points: int) -> dict:
    """The highest tier whose point floor ``points`` has reached."""
    result = TIERS[0]
    for tier in TIERS:
        if points >= tier["min"]:
            result = tier
    return result


def next_tier(points: int) -> dict | None:
    """The next tier up from ``points``, or None if already at the top."""
    for tier in TIERS:
        if tier["min"] > points:
            return tier
    return None


def _tier_ladder_legend() -> str:
    return "  ".join(f"{t['emoji']}{t['name']}" for t in TIERS)


# ═══════════════════════════════════════════════
#  Seasons
# ═══════════════════════════════════════════════

def _season_to_dict(row) -> dict | None:
    return dict(row) if row else None


async def get_active_season(bot: commands.Bot, guild_id: int) -> dict | None:
    row = await bot.database.fetch_one(
        "SELECT * FROM arena_seasons WHERE guild_id = ? AND status = 'active' "
        "ORDER BY season_no DESC LIMIT 1",
        (guild_id,),
    )
    return _season_to_dict(row)


async def _next_season_no(bot: commands.Bot, guild_id: int) -> int:
    row = await bot.database.fetch_one(
        "SELECT MAX(season_no) AS n FROM arena_seasons WHERE guild_id = ?", (guild_id,)
    )
    return (row["n"] or 0) + 1 if row else 1


async def create_season(
    bot: commands.Bot,
    guild_id: int,
    name: str | None = None,
    length_days: int | None = None,
    start_day: str | None = None,
) -> dict:
    """Creates and activates a new season. Caller ensures no other is active."""
    season_no = await _next_season_no(bot, guild_id)
    length = length_days or await _get_season_length(bot, guild_id)
    start = start_day or _today_iso()
    end = _day_add(start, max(1, length) - 1)
    name = name or f"Season {season_no}"
    await bot.database.execute(
        "INSERT INTO arena_seasons (guild_id, season_no, name, start_day, end_day, status) "
        "VALUES (?, ?, ?, ?, ?, 'active')",
        (guild_id, season_no, name, start, end),
    )
    logger.info(f"Arena: started season {season_no} '{name}' ({start} → {end}) for guild {guild_id}.")
    return {
        "guild_id": guild_id, "season_no": season_no, "name": name,
        "start_day": start, "end_day": end, "status": "active",
        "champion_id": None, "finalized_at": None,
    }


async def ensure_season(bot: commands.Bot, guild_id: int) -> dict:
    """Returns the active season, creating Season 1 if none exists yet."""
    season = await get_active_season(bot, guild_id)
    if season is None:
        season = await create_season(bot, guild_id)
    return season


# ═══════════════════════════════════════════════
#  Power-Up events
# ═══════════════════════════════════════════════

async def get_events_overlapping(bot: commands.Bot, guild_id: int, start_day: str, end_day: str) -> list[dict]:
    """All events whose window intersects [start_day, end_day]."""
    rows = await bot.database.fetch_all(
        "SELECT * FROM arena_events WHERE guild_id = ? AND end_day >= ? AND start_day <= ?",
        (guild_id, start_day, end_day),
    )
    return [dict(r) for r in rows]


def _day_multiplier(events: list[dict], day: str) -> float:
    """The strongest multiplier active on ``day`` (events do not stack)."""
    return max((e["multiplier"] for e in events if e["start_day"] <= day <= e["end_day"]), default=1.0)


def active_events(events: list[dict], day: str) -> list[dict]:
    return [e for e in events if e["start_day"] <= day <= e["end_day"]]


async def create_event(
    bot: commands.Bot, guild_id: int, name: str, multiplier: float,
    start_day: str, end_day: str, emoji: str = "⚡", created_by: int | None = None,
) -> None:
    await bot.database.execute(
        "INSERT INTO arena_events (guild_id, name, emoji, multiplier, start_day, end_day, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (guild_id, name, emoji, multiplier, start_day, end_day, created_by),
    )
    logger.info(f"Arena: launched power-up '{name}' ×{multiplier} ({start_day}→{end_day}) for guild {guild_id}.")


# ═══════════════════════════════════════════════
#  Scoring (the competitive standings)
# ═══════════════════════════════════════════════

async def compute_arena_standings(
    bot: commands.Bot,
    guild: discord.Guild,
    season: dict,
    through_day: str | None = None,
    limit: int | None = BOARD_SIZE,
) -> list[tuple[int, dict]]:
    """Ranks competitors for ``season``, applying Power-Up multipliers per day.

    Points use the Most Active weights (``messages × 5 + reactions × 2``); days
    covered by a Power-Up event are multiplied by its factor. ``through_day``
    caps the window early (used to compute "rank as of yesterday" for movement
    arrows). Staff-excluded members are dropped, exactly as on the leaderboards.
    Returns ``(user_id, {points, base, bonus, messages, reactions})`` sorted by
    points (messages break ties), limited to ``limit`` (None = full ranking).
    """
    tracked = _tracked_ids()
    if not tracked:
        return []

    start = season["start_day"]
    end = through_day or min(season["end_day"], _today_iso())
    if end < start:
        return []

    events = await get_events_overlapping(bot, guild.id, start, end)
    placeholders = ",".join("?" * len(tracked))
    rows = await bot.database.fetch_all(
        "SELECT user_id, day, SUM(messages) AS m, SUM(reactions) AS r "
        f"FROM engagement_daily WHERE guild_id = ? AND channel_id IN ({placeholders}) "
        "AND day BETWEEN ? AND ? GROUP BY user_id, day",
        (guild.id, *tracked, start, end),
    )

    agg: dict[int, dict] = {}
    for row in rows:
        uid = row["user_id"]
        base = (row["m"] or 0) * POINTS_PER_MESSAGE + (row["r"] or 0) * POINTS_PER_REACTION
        pts = base * _day_multiplier(events, row["day"])
        a = agg.setdefault(uid, {"points": 0.0, "base": 0, "messages": 0, "reactions": 0})
        a["points"] += pts
        a["base"] += base
        a["messages"] += row["m"] or 0
        a["reactions"] += row["r"] or 0

    results: list[tuple[int, dict]] = []
    for uid, a in agg.items():
        if a["base"] <= 0 or _is_excluded(guild, uid):
            continue
        pts = int(round(a["points"]))
        results.append((uid, {
            "points": pts, "base": a["base"], "bonus": pts - a["base"],
            "messages": a["messages"], "reactions": a["reactions"],
        }))

    results.sort(key=lambda kv: (kv[1]["points"], kv[1]["messages"]), reverse=True)
    return results if limit is None else results[:limit]


def _rank_map(standings: list[tuple[int, dict]]) -> dict[int, int]:
    """user_id → 1-based rank for a computed standings list."""
    return {uid: idx + 1 for idx, (uid, _) in enumerate(standings)}


# ═══════════════════════════════════════════════
#  Streaks
# ═══════════════════════════════════════════════

async def streaks_for(
    bot: commands.Bot, guild: discord.Guild, user_ids: list[int], lookback: int = 120
) -> dict[int, int]:
    """Current consecutive-day activity streak per user (UTC days).

    A streak is the run of consecutive days, ending today or yesterday, on which
    the member had any tracked activity. Anchoring on yesterday means an active
    member's streak doesn't read as broken early in a new UTC day before they've
    posted.
    """
    if not user_ids:
        return {}
    tracked = _tracked_ids()
    if not tracked:
        return {}

    ch_ph = ",".join("?" * len(tracked))
    uid_ph = ",".join("?" * len(user_ids))
    rows = await bot.database.fetch_all(
        "SELECT user_id, day FROM engagement_daily "
        f"WHERE guild_id = ? AND user_id IN ({uid_ph}) AND channel_id IN ({ch_ph}) "
        "AND (messages > 0 OR reactions > 0) AND day >= ?",
        (guild.id, *user_ids, *tracked, _cutoff_day(lookback)),
    )
    active_days: dict[int, set] = {}
    for row in rows:
        active_days.setdefault(row["user_id"], set()).add(row["day"])

    today = _today_iso()
    yesterday = _day_add(today, -1)
    out: dict[int, int] = {}
    for uid in user_ids:
        days = active_days.get(uid, set())
        if today in days:
            anchor = today
        elif yesterday in days:
            anchor = yesterday
        else:
            out[uid] = 0
            continue
        streak = 0
        cursor = anchor
        while cursor in days:
            streak += 1
            cursor = _day_add(cursor, -1)
        out[uid] = streak
    return out


# ═══════════════════════════════════════════════
#  Badges
# ═══════════════════════════════════════════════

async def award_badge(bot: commands.Bot, guild_id: int, user_id: int, key: str, season_no: int = 0) -> None:
    """Idempotently grants a badge (no-op if already held)."""
    await bot.database.execute(
        "INSERT OR IGNORE INTO arena_badges (guild_id, user_id, badge_key, season_no) "
        "VALUES (?, ?, ?, ?)",
        (guild_id, user_id, key, season_no),
    )


async def get_badges_for(bot: commands.Bot, guild_id: int, user_id: int) -> list[dict]:
    """Distinct badge keys held by a member (most recent first), with metadata."""
    rows = await bot.database.fetch_all(
        "SELECT badge_key, MAX(earned_at) AS earned FROM arena_badges "
        "WHERE guild_id = ? AND user_id = ? GROUP BY badge_key ORDER BY earned DESC",
        (guild_id, user_id),
    )
    out = []
    for row in rows:
        meta = BADGES.get(row["badge_key"])
        if meta:
            out.append({"key": row["badge_key"], **meta})
    return out


async def _badge_emojis_for(bot: commands.Bot, guild_id: int, user_ids: list[int], cap: int = 3) -> dict[int, str]:
    """Compact emoji strip of each user's distinct badges (capped), for the board."""
    if not user_ids:
        return {}
    uid_ph = ",".join("?" * len(user_ids))
    rows = await bot.database.fetch_all(
        f"SELECT DISTINCT user_id, badge_key FROM arena_badges WHERE guild_id = ? AND user_id IN ({uid_ph})",
        (guild_id, *user_ids),
    )
    by_user: dict[int, list[str]] = {}
    for row in rows:
        meta = BADGES.get(row["badge_key"])
        if meta:
            by_user.setdefault(row["user_id"], []).append(meta["emoji"])
    # Dedupe preserving first-seen order, then cap.
    return {uid: "".join(list(dict.fromkeys(emojis))[:cap]) for uid, emojis in by_user.items()}


async def evaluate_live_badges(
    bot: commands.Bot, guild: discord.Guild, season: dict, standings: list[tuple[int, dict]]
) -> None:
    """Awards the milestone badges that can be earned mid-season (idempotent)."""
    for uid, data in standings:
        if data["points"] >= 100:
            await award_badge(bot, guild.id, uid, "century", season["season_no"])
        if data["points"] >= 1000:
            await award_badge(bot, guild.id, uid, "high_roller", season["season_no"])

    top_ids = [uid for uid, _ in standings]
    streaks = await streaks_for(bot, guild, top_ids)
    for uid, streak in streaks.items():
        if streak >= 7:
            await award_badge(bot, guild.id, uid, "streak_7", 0)
        if streak >= 30:
            await award_badge(bot, guild.id, uid, "streak_30", 0)


# ═══════════════════════════════════════════════
#  Reward roles
# ═══════════════════════════════════════════════

async def sync_reward_roles(
    bot: commands.Bot, guild: discord.Guild, full_standings: list[tuple[int, dict]]
) -> None:
    """Assigns each ranked member their tier role and strips stale tier roles.

    No-op unless reward roles are configured. Defensive: a missing role,
    hierarchy issue, or permission error on one member never aborts the sync.
    """
    roles_map = await _get_tier_roles(bot, guild.id)
    tier_role_ids = {roles_map[t["key"]] for t in TIERS if t["key"] in roles_map}
    if not tier_role_ids:
        return

    me = guild.me
    can_manage = bool(me and me.guild_permissions.manage_roles)
    if not can_manage:
        logger.warning(f"Arena: reward roles configured but missing Manage Roles in guild {guild.id}.")
        return

    desired: dict[int, int | None] = {}
    for uid, data in full_standings:
        desired[uid] = roles_map.get(tier_for(data["points"])["key"])

    async def _safe(coro):
        try:
            await coro
        except (discord.Forbidden, discord.HTTPException):
            pass

    # Grant the right tier role to each ranked member; remove any other managed
    # tier role they may be carrying from a previous tier.
    for uid, want_id in desired.items():
        member = guild.get_member(uid)
        if member is None:
            continue
        want = guild.get_role(want_id) if want_id else None
        if want and want not in member.roles and want < me.top_role:
            await _safe(member.add_roles(want, reason="IM8 Arena tier"))
        for rid in tier_role_ids:
            if rid == want_id:
                continue
            role = guild.get_role(rid)
            if role and role in member.roles and role < me.top_role:
                await _safe(member.remove_roles(role, reason="IM8 Arena tier change"))

    # Strip tier roles from members who have dropped off the standings entirely.
    ranked = set(desired)
    for rid in tier_role_ids:
        role = guild.get_role(rid)
        if not role or not (role < me.top_role):
            continue
        for member in list(role.members):
            if member.id not in ranked:
                await _safe(member.remove_roles(role, reason="IM8 Arena: no longer ranked"))


async def _assign_champion_role(bot: commands.Bot, guild: discord.Guild, champion_id: int | None) -> None:
    """Moves the optional 'champion' reward role to the newest champion."""
    roles_map = await _get_tier_roles(bot, guild.id)
    role_id = roles_map.get("champion")
    if not role_id:
        return
    role = guild.get_role(role_id)
    me = guild.me
    if not role or not me or not me.guild_permissions.manage_roles or not (role < me.top_role):
        return
    for member in list(role.members):
        if member.id != champion_id:
            try:
                await member.remove_roles(role, reason="IM8 Arena: dethroned")
            except (discord.Forbidden, discord.HTTPException):
                pass
    if champion_id:
        member = guild.get_member(champion_id)
        if member and role not in member.roles:
            try:
                await member.add_roles(role, reason="IM8 Arena: reigning champion")
            except (discord.Forbidden, discord.HTTPException):
                pass


# ═══════════════════════════════════════════════
#  Season finalize + rollover
# ═══════════════════════════════════════════════

async def finalize_season(bot: commands.Bot, guild: discord.Guild, season: dict) -> dict:
    """Crowns the champion, awards season badges, snapshots the Hall of Fame, and
    marks the season ended. Returns ``{season, champion_id, standings}``."""
    standings = await compute_arena_standings(
        bot, guild, season, through_day=season["end_day"], limit=None
    )
    champion_id = standings[0][0] if standings else None

    for idx, (uid, _) in enumerate(standings):
        await award_badge(bot, guild.id, uid, "competitor", season["season_no"])
        if idx < 10:
            await award_badge(bot, guild.id, uid, "top10", season["season_no"])
        if idx < 3:
            await award_badge(bot, guild.id, uid, "podium", season["season_no"])
        if idx == 0:
            await award_badge(bot, guild.id, uid, "champion", season["season_no"])

    snapshot = [
        {"user_id": uid, "points": d["points"], "messages": d["messages"], "reactions": d["reactions"]}
        for uid, d in standings[:10]
    ]
    await bot.database.execute(
        "INSERT OR REPLACE INTO arena_champions "
        "(guild_id, season_no, name, start_day, end_day, champion_id, standings) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (guild.id, season["season_no"], season["name"], season["start_day"],
         season["end_day"], champion_id, json.dumps(snapshot)),
    )
    await bot.database.execute(
        "UPDATE arena_seasons SET status = 'ended', champion_id = ?, finalized_at = datetime('now') "
        "WHERE guild_id = ? AND season_no = ?",
        (champion_id, guild.id, season["season_no"]),
    )
    await _assign_champion_role(bot, guild, champion_id)
    logger.info(
        f"Arena: finalized season {season['season_no']} for guild {guild.id}; "
        f"champion={champion_id}, competitors={len(standings)}."
    )
    return {"season": season, "champion_id": champion_id, "standings": standings}


async def get_hall_of_fame(bot: commands.Bot, guild_id: int, limit: int = 12) -> list[dict]:
    rows = await bot.database.fetch_all(
        "SELECT * FROM arena_champions WHERE guild_id = ? ORDER BY season_no DESC LIMIT ?",
        (guild_id, limit),
    )
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════
#  Embeds
# ═══════════════════════════════════════════════

def _name(guild: discord.Guild, user_id: int) -> str:
    member = guild.get_member(user_id)
    return member.mention if member else f"<@{user_id}>"


async def build_arena_embed(bot: commands.Bot, guild: discord.Guild) -> discord.Embed:
    """The public Arena board — the gamified centerpiece. Single source of truth
    shared by deploy, refresh, and preview so they can never diverge."""
    season = await ensure_season(bot, guild.id)
    standings = await compute_arena_standings(bot, guild, season, limit=BOARD_SIZE)

    # Movement: rank as of end-of-yesterday within this season.
    yesterday = _day_add(_today_iso(), -1)
    prev_ranks: dict[int, int] = {}
    if yesterday >= season["start_day"]:
        prev = await compute_arena_standings(bot, guild, season, through_day=yesterday, limit=None)
        prev_ranks = _rank_map(prev)

    top_ids = [uid for uid, _ in standings]
    streaks = await streaks_for(bot, guild, top_ids)
    badge_strip = await _badge_emojis_for(bot, guild.id, top_ids)
    events_now = active_events(
        await get_events_overlapping(bot, guild.id, _today_iso(), _today_iso()), _today_iso()
    )

    # Season header — progress bar + live countdown.
    total_days = _days_between(season["start_day"], season["end_day"])
    day_no = min(_days_between(season["start_day"], _today_iso()), total_days)
    end_dt = datetime.datetime.fromisoformat(season["end_day"] + "T23:59:59+00:00")
    end_ts = int(end_dt.timestamp())

    embed = discord.Embed(
        title=f"🏟️ IM8 Arena — {season['name']}",
        description=(
            f"**Day {day_no} of {total_days}**  ·  ends <t:{end_ts}:R>\n"
            f"`{_bar(day_no / total_days)}`\n"
            f"*Earn points by being active across the community — "
            f"`{POINTS_PER_MESSAGE}` per message, `{POINTS_PER_REACTION}` per reaction.*"
        ),
        color=0xF1C40F,
    )

    if events_now:
        ev = max(events_now, key=lambda e: e["multiplier"])
        embed.add_field(
            name=f"{ev.get('emoji') or '⚡'} POWER-UP ACTIVE — {ev['name']}",
            value=f"**×{ev['multiplier']:g} points** on everything you do until <t:"
                  f"{int(datetime.datetime.fromisoformat(ev['end_day'] + 'T23:59:59+00:00').timestamp())}:R>!",
            inline=False,
        )

    if not standings:
        embed.add_field(
            name="The arena is empty…",
            value="Be the first to score — send a meaningful message or react in the community channels!",
            inline=False,
        )
        embed.set_footer(text=f"IM8 Health • Arena • Auto-refreshes every {REFRESH_HOURS}h")
        embed.timestamp = discord.utils.utcnow()
        return embed

    medals = ["🥇", "🥈", "🥉"]

    def _move(uid: int, rank: int) -> str:
        if not prev_ranks:
            return ""
        if uid not in prev_ranks:
            return f" {_NEW}"
        delta = prev_ranks[uid] - rank
        if delta > 0:
            return f" {_UP}{delta}"
        if delta < 0:
            return f" {_DOWN}{abs(delta)}"
        return f" {_SAME}"

    # Podium spotlight.
    podium_lines = []
    for idx in range(min(PODIUM_SIZE, len(standings))):
        uid, data = standings[idx]
        tier = tier_for(data["points"])
        streak = streaks.get(uid, 0)
        flame = f" · 🔥{streak}d" if streak >= 2 else ""
        badges = badge_strip.get(uid, "")
        badges = f"  {badges}" if badges else ""
        bonus = f"  (+{data['bonus']:,} ⚡)" if data["bonus"] > 0 else ""
        podium_lines.append(
            f"{medals[idx]} {_name(guild, uid)} {tier['emoji']}{badges}\n"
            f"      **{data['points']:,} pts**{bonus} · {data['messages']} msgs · "
            f"{data['reactions']} reactions{flame}{_move(uid, idx + 1)}"
        )
    embed.add_field(name="🏆 Podium", value="\n".join(podium_lines), inline=False)

    # The chasing pack — with the all-important gap-to-overtake.
    if len(standings) > PODIUM_SIZE:
        chase_lines = []
        for idx in range(PODIUM_SIZE, len(standings)):
            uid, data = standings[idx]
            tier = tier_for(data["points"])
            ahead = standings[idx - 1][1]["points"]
            gap = ahead - data["points"]
            streak = streaks.get(uid, 0)
            flame = f" 🔥{streak}" if streak >= 2 else ""
            gap_str = f"  ·  ▲ {gap:,} to rank {idx}" if gap > 0 else "  ·  tied!"
            chase_lines.append(
                f"`#{idx + 1:>2}` {tier['emoji']} {_name(guild, uid)} — "
                f"**{data['points']:,}**{flame}{gap_str}{_move(uid, idx + 1)}"
            )
        embed.add_field(name="⚔️ The Chase", value="\n".join(chase_lines), inline=False)

    # Hottest climber callout (most ranks gained since yesterday).
    if prev_ranks:
        cur_ranks = _rank_map(standings)
        climber = max(
            (s for s in standings if s[0] in prev_ranks),
            key=lambda s: prev_ranks[s[0]] - cur_ranks[s[0]],
            default=None,
        )
        if climber:
            gained = prev_ranks[climber[0]] - cur_ranks[climber[0]]
            if gained > 0:
                embed.add_field(
                    name="🔥 Hottest Climber",
                    value=f"{_name(guild, climber[0])} surged **+{gained} rank"
                          f"{'s' if gained != 1 else ''}** since yesterday!",
                    inline=False,
                )

    embed.add_field(name="🎖️ Tiers", value=_tier_ladder_legend(), inline=False)
    embed.set_footer(text=f"IM8 Health • Arena • Auto-refreshes every {REFRESH_HOURS}h")
    embed.timestamp = discord.utils.utcnow()
    return embed


def build_placeholder_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🏟️ IM8 Arena",
        description="⏳ Entering the arena…",
        color=0xF1C40F,
    )
    embed.set_footer(text="IM8 Health • Arena")
    return embed


async def build_member_card_embed(bot: commands.Bot, guild: discord.Guild, user: discord.abc.User) -> discord.Embed:
    """A personal competitor card — rank, tier, streak, badges, next-tier goal."""
    season = await ensure_season(bot, guild.id)
    standings = await compute_arena_standings(bot, guild, season, limit=None)
    rank_map = _rank_map(standings)
    data = dict(standings[rank_map[user.id] - 1][1]) if user.id in rank_map else None

    points = data["points"] if data else 0
    tier = tier_for(points)
    nxt = next_tier(points)
    streaks = await streaks_for(bot, guild, [user.id])
    streak = streaks.get(user.id, 0)
    badges = await get_badges_for(bot, guild.id, user.id)

    embed = discord.Embed(
        title=f"{tier['emoji']} Arena Card — {user.display_name}",
        description=f"**{season['name']}** competitor profile",
        color=0xF1C40F,
    )
    embed.set_thumbnail(url=user.display_avatar.url)

    if _is_excluded(guild, user.id):
        embed.add_field(
            name="⚠️ Staff",
            value="This member holds an excluded staff role and does not compete in the Arena.",
            inline=False,
        )

    if data:
        rank = rank_map[user.id]
        place = {1: "🥇 1st", 2: "🥈 2nd", 3: "🥉 3rd"}.get(rank, f"#{rank}")
        bonus = f"  (incl. +{data['bonus']:,} ⚡ bonus)" if data["bonus"] > 0 else ""
        lines = [
            f"**Rank:** {place} of {len(standings)}",
            f"**Tier:** {tier['emoji']} {tier['name']}",
            f"**Points:** {data['points']:,}{bonus}",
            f"**Activity:** {data['messages']} msgs · {data['reactions']} reactions",
            f"**Streak:** {'🔥 ' + str(streak) + '-day' if streak >= 1 else '— none yet'}",
        ]
        if rank > 1:
            ahead_uid, ahead = standings[rank - 2]
            lines.append(f"**Next target:** {_name(guild, ahead_uid)} — **{ahead['points'] - data['points']:,} pts** ahead")
        embed.add_field(name="📊 This Season", value="\n".join(lines), inline=False)

        if nxt:
            span = max(1, nxt["min"] - tier["min"])
            prog = (points - tier["min"]) / span
            embed.add_field(
                name=f"⬆️ Climb to {nxt['emoji']} {nxt['name']}",
                value=f"`{_bar(prog)}`  **{max(0, nxt['min'] - points):,} pts** to go",
                inline=False,
            )
        else:
            embed.add_field(name="🏆 Apex", value="You're at the **top tier** — defend your throne!", inline=False)
    else:
        embed.add_field(
            name="📊 This Season",
            value="You haven't scored yet this season. Jump into the community channels to enter the Arena!",
            inline=False,
        )

    if badges:
        embed.add_field(
            name=f"🏅 Badges ({len(badges)})",
            value=" ".join(f"{b['emoji']}" for b in badges) + "\n" +
                  "\n".join(f"{b['emoji']} **{b['name']}** — {b['desc']}" for b in badges[:6]),
            inline=False,
        )
    else:
        embed.add_field(name="🏅 Badges", value="No badges yet — they're waiting to be earned!", inline=False)

    embed.set_footer(text="IM8 Health • Arena")
    embed.timestamp = discord.utils.utcnow()
    return embed


async def build_hall_of_fame_embed(bot: commands.Bot, guild: discord.Guild) -> discord.Embed:
    """Permanent record of past champions."""
    hof = await get_hall_of_fame(bot, guild.id)
    embed = discord.Embed(
        title="👑 IM8 Arena — Hall of Fame",
        description="Every champion who has ever ruled the Arena.",
        color=0xE91E63,
    )
    if not hof:
        embed.add_field(
            name="No champions yet",
            value="The first season is still being written. Compete now to etch your name here forever!",
            inline=False,
        )
    else:
        lines = []
        for entry in hof:
            champ = f"{_name(guild, entry['champion_id'])}" if entry["champion_id"] else "*no competitors*"
            try:
                standings = json.loads(entry["standings"] or "[]")
                pts = standings[0]["points"] if standings else 0
            except Exception:
                pts = 0
            pts_str = f" — **{pts:,} pts**" if pts else ""
            lines.append(f"👑 **{entry['name']}** ({entry['start_day']} → {entry['end_day']})\n      {champ}{pts_str}")
        embed.add_field(name="Champions", value="\n".join(lines), inline=False)

    embed.set_footer(text="IM8 Health • Arena")
    embed.timestamp = discord.utils.utcnow()
    return embed


def build_finale_embed(guild: discord.Guild, result: dict, next_season: dict | None) -> discord.Embed:
    """The cinematic season-end ceremony posted to the announce channel."""
    season = result["season"]
    standings = result["standings"]
    embed = discord.Embed(
        title=f"🏆 {season['name']} — FINALE",
        description=(
            f"The curtain falls on **{season['name']}** "
            f"({season['start_day']} → {season['end_day']}). The results are in…"
        ),
        color=0xFFD700,
    )
    if standings:
        champ_id, champ = standings[0]
        embed.add_field(
            name="👑 CHAMPION",
            value=f"# {_name(guild, champ_id)}\n**{champ['points']:,} points** · "
                  f"{champ['messages']} msgs · {champ['reactions']} reactions",
            inline=False,
        )
        medals = ["🥇", "🥈", "🥉"]
        podium = [
            f"{medals[i]} {_name(guild, uid)} — **{d['points']:,} pts**"
            for i, (uid, d) in enumerate(standings[:3])
        ]
        embed.add_field(name="🏅 Final Podium", value="\n".join(podium), inline=False)
        if len(standings) > 3:
            rest = [f"`#{i + 1}` {_name(guild, uid)} — {d['points']:,}" for i, (uid, d) in enumerate(standings[3:10], start=3)]
            embed.add_field(name="Top 10", value="\n".join(rest), inline=False)
        embed.add_field(
            name="🎖️ Badges awarded",
            value="👑 Champion · 🏅 Podium · 🎖️ Top 10 · ⚔️ Competitor — check your **Arena Card**!",
            inline=False,
        )
    else:
        embed.add_field(name="A quiet season", value="No qualifying activity this time. A fresh start awaits!", inline=False)

    if next_season:
        start_ts = int(datetime.datetime.fromisoformat(next_season["start_day"] + "T00:00:00+00:00").timestamp())
        embed.add_field(
            name="🚀 A new season begins!",
            value=f"**{next_season['name']}** is now live (started <t:{start_ts}:R>). "
                  f"The scoreboard is reset — everyone starts from zero. **Go!**",
            inline=False,
        )
    embed.set_footer(text="IM8 Health • Arena")
    embed.timestamp = discord.utils.utcnow()
    return embed


async def build_arena_hub_embed(bot: commands.Bot, guild: discord.Guild) -> discord.Embed:
    """The Arena control-hub overview shown in the Mod Panel."""
    season = await ensure_season(bot, guild.id)
    embed = discord.Embed(
        title="🏟️ The Arena • Competitive Engagement",
        description=(
            "A live competition layered on the Most Active engine. Members earn points for "
            "real participation, climb a tier ladder, build streaks, collect badges, and race "
            "for the season crown — all computed instantly from existing engagement data."
        ),
        color=0xF1C40F,
    )

    total_days = _days_between(season["start_day"], season["end_day"])
    day_no = min(_days_between(season["start_day"], _today_iso()), total_days)
    end_ts = int(datetime.datetime.fromisoformat(season["end_day"] + "T23:59:59+00:00").timestamp())
    embed.add_field(
        name=f"📅 Active Season — {season['name']}",
        value=f"Day **{day_no}/{total_days}** · ends <t:{end_ts}:R>\n`{_bar(day_no / total_days)}`",
        inline=False,
    )

    events = await get_events_overlapping(bot, guild.id, _today_iso(), _day_add(_today_iso(), 60))
    if events:
        ev_lines = []
        for ev in sorted(events, key=lambda e: e["start_day"])[:4]:
            state = "🟢 LIVE" if ev["start_day"] <= _today_iso() <= ev["end_day"] else "⏳ upcoming"
            ev_lines.append(f"{ev.get('emoji') or '⚡'} **{ev['name']}** ×{ev['multiplier']:g} "
                            f"({ev['start_day']}→{ev['end_day']}) {state}")
        embed.add_field(name="⚡ Power-Ups", value="\n".join(ev_lines), inline=False)

    roles_map = await _get_tier_roles(bot, guild.id)
    if roles_map:
        bound = []
        for t in TIERS:
            if t["key"] in roles_map:
                bound.append(f"{t['emoji']}→<@&{roles_map[t['key']]}>")
        if "champion" in roles_map:
            bound.append(f"👑→<@&{roles_map['champion']}>")
        embed.add_field(name="🎁 Reward Roles", value=" · ".join(bound) or "none", inline=False)
    else:
        embed.add_field(
            name="🎁 Reward Roles",
            value="Not configured — use **Reward Roles** to auto-grant a Discord role at each tier.",
            inline=False,
        )

    board = await bot.database.fetch_one("SELECT * FROM arena_boards WHERE guild_id = ?", (guild.id,))
    if board:
        ch = guild.get_channel(board["channel_id"])
        ch_str = ch.mention if ch else f"<#{board['channel_id']}>"
        ts = _parse_sql_ts(board["updated_at"]) if board["updated_at"] else None
        when = f"<t:{ts}:R>" if ts else "unknown"
        embed.add_field(name="📢 Public Board", value=f"🟢 Live in {ch_str} • updated {when}", inline=False)
    else:
        embed.add_field(name="📢 Public Board", value="⚪ Not posted yet — use **Post Board** below.", inline=False)

    embed.add_field(
        name="🛠️ Actions",
        value=(
            "**Preview** — see the live board privately.  **My Card** — any member's profile.\n"
            "**Hall of Fame** — past champions.\n"
            "**Post / Refresh / Remove Board** — manage the public board.\n"
            "**Power-Up** — launch a ×2/×3 scoring event.  **End Season** — crown now & start fresh.\n"
            "**Reward Roles / Settings** — tier roles, season length, announce channel."
        ),
        inline=False,
    )
    embed.set_footer(text="IM8 Health • Arena")
    return embed


# ═══════════════════════════════════════════════
#  Board deploy / refresh
# ═══════════════════════════════════════════════

_BG_TASKS: set = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


async def deploy_arena_board(bot: commands.Bot, guild: discord.Guild, channel) -> discord.Message:
    embed = await build_arena_embed(bot, guild)
    msg = await channel.send(embed=embed)
    await bot.database.execute(
        "INSERT OR REPLACE INTO arena_boards (guild_id, channel_id, message_id, updated_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        (guild.id, channel.id, msg.id),
    )
    return msg


async def refresh_arena_board(bot: commands.Bot, row) -> bool:
    guild = bot.get_guild(row["guild_id"])
    if guild is None:
        return False

    async def _prune():
        await bot.database.execute("DELETE FROM arena_boards WHERE guild_id = ?", (row["guild_id"],))

    channel = guild.get_channel(row["channel_id"])
    if channel is None:
        try:
            channel = await guild.fetch_channel(row["channel_id"])
        except Exception:
            await _prune()
            return False
    try:
        message = await channel.fetch_message(row["message_id"])
    except discord.NotFound:
        await _prune()
        return False
    except Exception as e:
        logger.error(f"Arena: could not fetch board message: {e}")
        return False

    try:
        embed = await build_arena_embed(bot, guild)
        await message.edit(embed=embed)
        await bot.database.execute(
            "UPDATE arena_boards SET updated_at = datetime('now') WHERE guild_id = ?",
            (row["guild_id"],),
        )
        return True
    except discord.NotFound:
        await _prune()
        return False
    except Exception as e:
        logger.error(f"Arena: failed to edit board message: {e}")
        return False


# ═══════════════════════════════════════════════
#  UI — ephemeral helper views/modals
# ═══════════════════════════════════════════════

class PostArenaBoardView(discord.ui.View):
    """Channel picker that posts the public Arena board."""

    def __init__(self) -> None:
        super().__init__(timeout=300)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="📢 Pick a channel for the Arena board",
        min_values=1, max_values=1,
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
            msg = await deploy_arena_board(interaction.client, interaction.guild, target)
        except Exception as e:
            return await interaction.followup.send(f"❌ Failed to post the Arena board: {e}", ephemeral=True)
        await interaction.followup.send(
            f"✅ **Arena board** is live in {target.mention} → {msg.jump_url}\n"
            f"It auto-refreshes every **{REFRESH_HOURS}h** and on every bot restart.",
            ephemeral=True,
        )


class MemberCardView(discord.ui.View):
    """Member picker for the personal Arena card."""

    def __init__(self) -> None:
        super().__init__(timeout=300)

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="🔎 Pick a member", min_values=1, max_values=1)
    async def pick(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        await interaction.response.defer(ephemeral=True)
        embed = await build_member_card_embed(interaction.client, interaction.guild, select.values[0])
        await interaction.followup.send(embed=embed, ephemeral=True)


class RewardRoleView(discord.ui.View):
    """Two-step picker that binds a Discord role to a tier (or the champion)."""

    def __init__(self) -> None:
        super().__init__(timeout=300)
        self.tier_key: str | None = None

    @discord.ui.select(
        cls=discord.ui.Select,
        placeholder="1️⃣ Pick a tier to bind a role to",
        min_values=1, max_values=1,
        options=[discord.SelectOption(label=t["name"], value=t["key"], emoji=t["emoji"]) for t in TIERS]
        + [discord.SelectOption(label="Reigning Champion", value="champion", emoji="👑")],
    )
    async def pick_tier(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.tier_key = select.values[0]
        await interaction.response.send_message(
            f"Now pick the role to grant for **{self.tier_key}** (or pick none to unbind):",
            view=_RewardRoleStep2(self.tier_key), ephemeral=True,
        )


class _RewardRoleStep2(discord.ui.View):
    def __init__(self, tier_key: str) -> None:
        super().__init__(timeout=300)
        self.tier_key = tier_key

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="2️⃣ Pick the reward role", min_values=0, max_values=1)
    async def pick_role(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        role_id = select.values[0].id if select.values else None
        await _set_tier_role(interaction.client, interaction.guild.id, self.tier_key, role_id)
        if role_id:
            await interaction.response.send_message(
                f"✅ Bound **{self.tier_key}** → <@&{role_id}>. It syncs on the daily tick and when seasons end.\n"
                f"*(Make sure my role is **above** the reward role and I have **Manage Roles**.)*",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(f"✅ Unbound the role for **{self.tier_key}**.", ephemeral=True)


class PowerUpModal(discord.ui.Modal, title="Launch a Power-Up Event"):
    """Creates a multiplier event over a UTC day range."""

    def __init__(self) -> None:
        super().__init__()
        self.name = discord.ui.TextInput(label="Event name", placeholder="Double Points Weekend", max_length=80)
        self.multiplier = discord.ui.TextInput(label="Multiplier (e.g. 2 or 3)", placeholder="2", max_length=4)
        self.start = discord.ui.TextInput(label="Start date (YYYY-MM-DD)", placeholder=_today_iso(), max_length=10)
        self.end = discord.ui.TextInput(label="End date (YYYY-MM-DD)", placeholder=_day_add(_today_iso(), 2), max_length=10)
        self.emoji = discord.ui.TextInput(label="Emoji (optional)", placeholder="⚡", required=False, max_length=8)
        for item in (self.name, self.multiplier, self.start, self.end, self.emoji):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            mult = float(str(self.multiplier.value).strip())
            if not (1.0 < mult <= 10.0):
                raise ValueError
        except ValueError:
            return await interaction.response.send_message("❌ Multiplier must be a number between 1 and 10.", ephemeral=True)
        try:
            sd = datetime.date.fromisoformat(self.start.value.strip())
            ed = datetime.date.fromisoformat(self.end.value.strip())
        except ValueError:
            return await interaction.response.send_message("❌ Dates must be `YYYY-MM-DD`.", ephemeral=True)
        if sd > ed:
            sd, ed = ed, sd
        emoji = (self.emoji.value or "").strip() or "⚡"
        await create_event(
            interaction.client, interaction.guild.id, self.name.value.strip(), mult,
            sd.isoformat(), ed.isoformat(), emoji, interaction.user.id,
        )
        await interaction.response.send_message(
            f"✅ {emoji} **{self.name.value.strip()}** launched — **×{mult:g} points** "
            f"from `{sd.isoformat()}` to `{ed.isoformat()}`! The board will hype it automatically.",
            ephemeral=True,
        )
        # Push the hype to the live board immediately.
        row = await interaction.client.database.fetch_one(
            "SELECT * FROM arena_boards WHERE guild_id = ?", (interaction.guild.id,)
        )
        if row:
            _spawn(refresh_arena_board(interaction.client, row))


class SeasonSettingsModal(discord.ui.Modal, title="Arena Settings"):
    """Adjusts season length and the announcement channel."""

    def __init__(self, current_length: int, current_channel: int | None) -> None:
        super().__init__()
        self.length = discord.ui.TextInput(
            label="Season length (days)", placeholder=str(current_length),
            default=str(current_length), max_length=4,
        )
        self.announce = discord.ui.TextInput(
            label="Announce channel ID (finale/power-ups)", required=False,
            default=str(current_channel) if current_channel else "", max_length=20,
        )
        self.add_item(self.length)
        self.add_item(self.announce)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            length = int(str(self.length.value).strip())
            if not (1 <= length <= 366):
                raise ValueError
        except ValueError:
            return await interaction.response.send_message("❌ Season length must be 1–366 days.", ephemeral=True)
        await _set_meta(interaction.client, interaction.guild.id, "arena_season_length", str(length))

        ann = str(self.announce.value or "").strip()
        if ann:
            if not ann.isdigit() or interaction.guild.get_channel(int(ann)) is None:
                return await interaction.response.send_message(
                    "❌ Announce channel must be a valid text-channel ID in this server.", ephemeral=True
                )
            await _set_meta(interaction.client, interaction.guild.id, "arena_announce_channel", ann)
        else:
            await _set_meta(interaction.client, interaction.guild.id, "arena_announce_channel", "")
        await interaction.response.send_message(
            f"✅ Saved. New seasons run **{length} days**; "
            f"{'announcements → <#' + ann + '>' if ann else 'announcements use the board channel'}.",
            ephemeral=True,
        )


class ConfirmEndSeasonView(discord.ui.View):
    """Guards the destructive 'crown now & reset' action behind a confirm."""

    def __init__(self) -> None:
        super().__init__(timeout=120)

    @discord.ui.button(label="Crown champion & start new season", style=discord.ButtonStyle.danger, emoji="🏆")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        cog = interaction.client.get_cog("ArenaCog")
        result, nxt = await cog.end_and_rollover(interaction.guild, finalize_through_today=True)
        await cog.announce_finale(interaction.guild, result, nxt)
        champ = result["standings"][0][0] if result["standings"] else None
        champ_str = f"<@{champ}>" if champ else "*nobody (no activity)*"
        await interaction.followup.send(
            f"🏆 Season finalized — champion: {champ_str}. **{nxt['name']}** has begun!", ephemeral=True
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled — the season continues.", view=None)


# ═══════════════════════════════════════════════
#  UI — the persistent Arena hub
# ═══════════════════════════════════════════════

class ArenaHubView(discord.ui.View):
    """The Arena control hub, reachable from the Most Active hub."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def _refresh_hub(self, interaction: discord.Interaction) -> None:
        try:
            embed = await build_arena_hub_embed(interaction.client, interaction.guild)
            await interaction.message.edit(content=None, embed=embed, view=ArenaHubView())
        except Exception as e:
            logger.error(f"Arena: failed to refresh hub: {e}")

    # ── Row 0: views ──
    @discord.ui.button(label="Preview Board", emoji="👁️", style=discord.ButtonStyle.primary, row=0, custom_id="im8_arena_preview")
    async def btn_preview(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        embed = await build_arena_embed(interaction.client, interaction.guild)
        await interaction.followup.send(content="**Arena board** — preview *(only you can see this).*", embed=embed, ephemeral=True)

    @discord.ui.button(label="My Card", emoji="🪪", style=discord.ButtonStyle.primary, row=0, custom_id="im8_arena_card")
    async def btn_card(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔎 Pick a member to see their Arena card:", view=MemberCardView(), ephemeral=True)

    @discord.ui.button(label="Hall of Fame", emoji="👑", style=discord.ButtonStyle.primary, row=0, custom_id="im8_arena_hof")
    async def btn_hof(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        embed = await build_hall_of_fame_embed(interaction.client, interaction.guild)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Row 1: public board management ──
    @discord.ui.button(label="Post Board", emoji="📢", style=discord.ButtonStyle.success, row=1, custom_id="im8_arena_post")
    async def btn_post(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("📢 Pick a channel to post the Arena board:", view=PostArenaBoardView(), ephemeral=True)

    @discord.ui.button(label="Refresh Now", emoji="🔄", style=discord.ButtonStyle.success, row=1, custom_id="im8_arena_refresh")
    async def btn_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = await interaction.client.database.fetch_one("SELECT * FROM arena_boards WHERE guild_id = ?", (interaction.guild.id,))
        if not row:
            return await interaction.response.send_message("❌ No public board is posted yet.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        ok = await refresh_arena_board(interaction.client, row)
        await interaction.followup.send("🔄 Board refreshed." if ok else "⚠️ Could not refresh (board may have been deleted).", ephemeral=True)

    @discord.ui.button(label="Remove Board", emoji="🗑️", style=discord.ButtonStyle.danger, row=1, custom_id="im8_arena_remove")
    async def btn_remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = await interaction.client.database.fetch_one("SELECT * FROM arena_boards WHERE guild_id = ?", (interaction.guild.id,))
        if not row:
            return await interaction.response.send_message("❌ No board to remove.", ephemeral=True)
        await interaction.client.database.execute("DELETE FROM arena_boards WHERE guild_id = ?", (interaction.guild.id,))
        await interaction.response.send_message("🗑️ Auto-refresh stopped. The posted message was left intact.", ephemeral=True)
        await self._refresh_hub(interaction)

    # ── Row 2: season + events ──
    @discord.ui.button(label="Power-Up", emoji="⚡", style=discord.ButtonStyle.secondary, row=2, custom_id="im8_arena_powerup")
    async def btn_powerup(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PowerUpModal())

    @discord.ui.button(label="End Season", emoji="🏁", style=discord.ButtonStyle.secondary, row=2, custom_id="im8_arena_endseason")
    async def btn_end(self, interaction: discord.Interaction, button: discord.ui.Button):
        season = await ensure_season(interaction.client, interaction.guild.id)
        await interaction.response.send_message(
            f"🏁 End **{season['name']}** right now? This crowns the current leader, awards badges, "
            "posts the finale, and starts a fresh season from today.",
            view=ConfirmEndSeasonView(), ephemeral=True,
        )

    @discord.ui.button(label="Reward Roles", emoji="🎁", style=discord.ButtonStyle.secondary, row=2, custom_id="im8_arena_roles")
    async def btn_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "🎁 Bind a Discord role to each tier (auto-granted as members climb):",
            view=RewardRoleView(), ephemeral=True,
        )

    @discord.ui.button(label="Settings", emoji="⚙️", style=discord.ButtonStyle.secondary, row=2, custom_id="im8_arena_settings")
    async def btn_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        length = await _get_season_length(interaction.client, interaction.guild.id)
        ann = await _get_announce_channel_id(interaction.client, interaction.guild.id)
        await interaction.response.send_modal(SeasonSettingsModal(length, ann))

    # ── Row 3: navigation ──
    @discord.ui.button(label="Back to Most Active", emoji="🔙", style=discord.ButtonStyle.secondary, row=3, custom_id="im8_arena_back")
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.active import MostActiveHubView, build_hub_embed
        embed = await build_hub_embed(interaction.client, interaction.guild)
        await interaction.response.edit_message(content=None, embed=embed, view=MostActiveHubView())


# ═══════════════════════════════════════════════
#  Cog
# ═══════════════════════════════════════════════

class ArenaCog(commands.Cog):
    """The competitive Arena built on top of the live engagement engine."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._startup_done = False
        self._startup_task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        self.bot.add_view(ArenaHubView())
        self.bot.scheduler.add_interval_job(
            self.refresh_all_arena_boards, job_id="arena_board_refresh", hours=REFRESH_HOURS,
        )
        # Daily rollover/finalize + badge + reward-role tick, just after UTC midnight.
        self.bot.scheduler.add_cron_job(
            self.daily_tick, job_id="arena_daily_tick", cron_expression="5 0 * * *",
        )
        logger.info("Arena module loaded; board refresh + daily season tick scheduled.")

    async def cog_unload(self) -> None:
        if self._startup_task and not self._startup_task.done():
            self._startup_task.cancel()

    # ── Season lifecycle ──

    async def end_and_rollover(self, guild: discord.Guild, finalize_through_today: bool = False) -> tuple[dict, dict]:
        """Finalizes the active season and starts the next one (from today).

        ``finalize_through_today`` forces an early end (the manual 'End Season'
        path); otherwise the season is finalized through its own ``end_day``.
        Returns ``(finalize_result, next_season)``.
        """
        season = await ensure_season(self.bot, guild.id)
        if finalize_through_today:
            season = {**season, "end_day": _today_iso()}
        result = await finalize_season(self.bot, guild, season)
        nxt = await create_season(self.bot, guild.id, start_day=_today_iso())
        return result, nxt

    async def announce_finale(self, guild: discord.Guild, result: dict, next_season: dict | None) -> None:
        """Posts the finale ceremony to the announce channel (or board channel)."""
        channel_id = await _get_announce_channel_id(self.bot, guild.id)
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None:
            board = await self.bot.database.fetch_one("SELECT * FROM arena_boards WHERE guild_id = ?", (guild.id,))
            if board:
                channel = guild.get_channel(board["channel_id"])
        if channel is None:
            logger.info(f"Arena: no announce/board channel for guild {guild.id}; skipping finale post.")
            return
        try:
            await channel.send(embed=build_finale_embed(guild, result, next_season))
        except Exception as e:
            logger.error(f"Arena: failed to post finale for guild {guild.id}: {e}")

    async def daily_tick(self) -> None:
        """Runs each UTC morning: roll finished seasons over, refresh, award badges, sync roles."""
        for guild in self.bot.guilds:
            try:
                season = await ensure_season(self.bot, guild.id)
                if _today_iso() > season["end_day"]:
                    result, nxt = await self.end_and_rollover(guild, finalize_through_today=False)
                    await self.announce_finale(guild, result, nxt)
                    season = nxt

                standings = await compute_arena_standings(self.bot, guild, season, limit=None)
                await evaluate_live_badges(self.bot, guild, season, standings)
                await sync_reward_roles(self.bot, guild, standings)
            except Exception as e:
                logger.error(f"Arena: daily tick failed for guild {guild.id}: {e}")
        await self.refresh_all_arena_boards()

    # ── Startup / scheduled refresh ──

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._startup_done:
            return
        self._startup_done = True
        self._startup_task = asyncio.create_task(self._delayed_startup())

    async def _delayed_startup(self) -> None:
        await asyncio.sleep(20)  # let the gateway + member cache settle
        try:
            for guild in self.bot.guilds:
                season = await ensure_season(self.bot, guild.id)
                # Catch up any season(s) that ended while the bot was offline.
                if _today_iso() > season["end_day"]:
                    result, nxt = await self.end_and_rollover(guild, finalize_through_today=False)
                    await self.announce_finale(guild, result, nxt)
            await self.refresh_all_arena_boards()
            logger.info("Arena: startup season check + board refresh complete.")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Arena: startup refresh failed: {e}")

    async def refresh_all_arena_boards(self) -> None:
        try:
            rows = await self.bot.database.fetch_all("SELECT * FROM arena_boards")
        except Exception as e:
            logger.error(f"Arena: failed to load board configs: {e}")
            return
        for row in rows:
            await refresh_arena_board(self.bot, row)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ArenaCog(bot))
