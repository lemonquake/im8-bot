"""
IM8 Bot — Member Report Module
Tracks daily membership snapshots and publishes a comprehensive, public,
auto-refreshing report comparing current membership against a prior point in
time (daily = vs yesterday, weekly = vs 7 days ago), alongside a live feed of
online members, bots, and admins.
"""

import discord
from discord.ext import commands
import logging
import asyncio
import datetime

import config
# Reuse the engagement engine from the Most Active module so the weekly
# "top active" ranking follows the exact same counting rules everywhere.
from cogs.active import compute_engagement, POINTS_PER_MESSAGE, POINTS_PER_REACTION

logger = logging.getLogger("im8bot.cogs.memberreport")

# ═══════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════

# Staff/Admin role surfaced in the live feed.
ADMIN_ROLE_ID: int = config.ADMIN_ROLE_ID

# Auto-refresh cadence for posted reports (keeps the live feed fresh).
REFRESH_HOURS = 3

# Comparison windows, in days, for each timeframe.
TIMEFRAME_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}
TIMEFRAME_LABEL = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly"}
TIMEFRAME_VS = {"daily": "vs. Yesterday", "weekly": "vs. Last Week", "monthly": "vs. Last Month"}

# How many of the period's most active members to surface on the reports.
TOP_ACTIVE_N = 5

# Window label for the report's "Top Most Active" section, per timeframe.
TOP_ACTIVE_LABEL = {"daily": "Today", "weekly": "Last 7 Days", "monthly": "Last 30 Days"}


# ═══════════════════════════════════════════════
#  Stats computation
# ═══════════════════════════════════════════════

def compute_member_stats(guild: discord.Guild) -> dict:
    """Computes a live snapshot of the guild's membership.

    Returns a dict with total / humans / bots / online / admins counts.
    ``online`` requires the privileged Presence intent to be meaningful;
    without it every member reports as offline and this will be 0.
    """
    members = guild.members
    total = guild.member_count or len(members)
    bots = sum(1 for m in members if m.bot)
    humans = max(total - bots, 0)

    online = sum(
        1 for m in members
        if not m.bot and m.status is not discord.Status.offline
    )

    admin_role = guild.get_role(ADMIN_ROLE_ID)
    admins = len(admin_role.members) if admin_role else 0

    return {
        "total": total,
        "humans": humans,
        "bots": bots,
        "online": online,
        "admins": admins,
    }


async def record_snapshot(bot: commands.Bot, guild: discord.Guild) -> dict:
    """Records (or refreshes) today's membership snapshot for a guild.

    Returns the computed stats. Uses UTC date as the snapshot key so the
    daily/weekly comparisons line up with Discord's UTC timestamps.
    """
    stats = compute_member_stats(guild)
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    await bot.database.execute(
        "INSERT OR REPLACE INTO member_snapshots "
        "(guild_id, snapshot_date, total, humans, bots, online, admins, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            guild.id, today,
            stats["total"], stats["humans"], stats["bots"],
            stats["online"], stats["admins"],
        ),
    )
    return stats


async def get_baseline(bot: commands.Bot, guild_id: int, days_ago: int):
    """Returns the most recent snapshot taken on or before ``days_ago`` days back.

    Falls back to the closest earlier snapshot when an exact-date row is
    missing (e.g. the bot was offline that day). Returns None if there is no
    historical data to compare against yet.
    """
    target = (
        datetime.datetime.now(datetime.timezone.utc).date()
        - datetime.timedelta(days=days_ago)
    ).isoformat()
    return await bot.database.fetch_one(
        "SELECT * FROM member_snapshots "
        "WHERE guild_id = ? AND snapshot_date <= ? "
        "ORDER BY snapshot_date DESC LIMIT 1",
        (guild_id, target),
    )


# ═══════════════════════════════════════════════
#  New-member join tracking
# ═══════════════════════════════════════════════

# Historical join data carried over from the previous reporting system.
# These seed an empty guild so growth/trend reports aren't blank on day one.
#   • Weekly aggregates (Monday-anchored) for weeks that predate day-level
#     tracking.
#   • Daily counts for the current week, which also sum to the current
#     week's total on the fly.
SEED_WEEKLY_JOINS: dict[str, int] = {
    "2026-05-18": 100,
    "2026-05-25": 88,
}
SEED_DAILY_JOINS: dict[str, int] = {
    "2026-06-01": 11,
    "2026-06-02": 13,
}


def _utc_today() -> datetime.date:
    return datetime.datetime.now(datetime.timezone.utc).date()


def _week_start(d: datetime.date) -> datetime.date:
    """Monday of the week containing ``d``."""
    return d - datetime.timedelta(days=d.weekday())


async def record_join(bot: commands.Bot, guild_id: int) -> None:
    """Increments today's join counter for a guild (called on member join)."""
    today = _utc_today().isoformat()
    await bot.database.execute(
        "INSERT INTO member_joins (guild_id, period, period_date, joins) "
        "VALUES (?, 'day', ?, 1) "
        "ON CONFLICT(guild_id, period, period_date) "
        "DO UPDATE SET joins = joins + 1",
        (guild_id, today),
    )


async def seed_join_history(bot: commands.Bot, guild_id: int) -> bool:
    """Seeds historical join data for a guild that has none yet.

    Idempotent: only seeds when the guild has zero join rows, so it never
    clobbers live-tracked data. Returns True if seeding occurred.
    """
    existing = await bot.database.fetch_one(
        "SELECT COUNT(*) AS c FROM member_joins WHERE guild_id = ?", (guild_id,)
    )
    if existing and existing["c"]:
        return False

    for date, count in SEED_WEEKLY_JOINS.items():
        await bot.database.execute(
            "INSERT OR IGNORE INTO member_joins (guild_id, period, period_date, joins) "
            "VALUES (?, 'week', ?, ?)",
            (guild_id, date, count),
        )
    for date, count in SEED_DAILY_JOINS.items():
        await bot.database.execute(
            "INSERT OR IGNORE INTO member_joins (guild_id, period, period_date, joins) "
            "VALUES (?, 'day', ?, ?)",
            (guild_id, date, count),
        )
    logger.info(f"Member Report: seeded historical join data for guild {guild_id}.")
    return True


async def get_daily_joins(bot: commands.Bot, guild_id: int, n_days: int) -> list[tuple[str, int]]:
    """Returns (date, joins) for the last ``n_days`` calendar days, oldest first.

    Days with no recorded joins are included as 0.
    """
    rows = await bot.database.fetch_all(
        "SELECT period_date, joins FROM member_joins "
        "WHERE guild_id = ? AND period = 'day'",
        (guild_id,),
    )
    by_date = {r["period_date"]: r["joins"] for r in rows}
    today = _utc_today()
    out: list[tuple[str, int]] = []
    for i in range(n_days - 1, -1, -1):
        d = (today - datetime.timedelta(days=i)).isoformat()
        out.append((d, by_date.get(d, 0)))
    return out


async def _sum_daily_in_range(bot: commands.Bot, guild_id: int, start: datetime.date, end: datetime.date) -> int:
    """Sum of daily join rows in the inclusive [start, end] date range."""
    row = await bot.database.fetch_one(
        "SELECT COALESCE(SUM(joins), 0) AS s FROM member_joins "
        "WHERE guild_id = ? AND period = 'day' AND period_date BETWEEN ? AND ?",
        (guild_id, start.isoformat(), end.isoformat()),
    )
    return int(row["s"]) if row else 0


async def get_weekly_joins(bot: commands.Bot, guild_id: int, n_weeks: int) -> list[tuple[str, int]]:
    """Returns (week_start, joins) for the last ``n_weeks`` weeks, oldest first.

    The current week is summed live from daily rows; past weeks prefer a stored
    weekly aggregate and fall back to summing any daily rows in that week.
    """
    stored = await bot.database.fetch_all(
        "SELECT period_date, joins FROM member_joins "
        "WHERE guild_id = ? AND period = 'week'",
        (guild_id,),
    )
    stored_weeks = {r["period_date"]: r["joins"] for r in stored}

    current_start = _week_start(_utc_today())
    out: list[tuple[str, int]] = []
    for i in range(n_weeks - 1, -1, -1):
        ws = current_start - datetime.timedelta(weeks=i)
        we = ws + datetime.timedelta(days=6)
        if ws == current_start:
            count = await _sum_daily_in_range(bot, guild_id, ws, we)
        elif ws.isoformat() in stored_weeks:
            count = stored_weeks[ws.isoformat()]
        else:
            count = await _sum_daily_in_range(bot, guild_id, ws, we)
        out.append((ws.isoformat(), count))
    return out


async def get_monthly_joins(bot: commands.Bot, guild_id: int, n_months: int) -> list[tuple[str, int]]:
    """Returns (month_label, joins) for the last ``n_months`` calendar months, oldest first.

    The month_label is formatted like 'YYYY-MM'. Falls back to weekly aggregates if no daily joins.
    """
    today = _utc_today()
    out: list[tuple[str, int]] = []

    current_year = today.year
    current_month = today.month

    for i in range(n_months - 1, -1, -1):
        y = current_year
        m = current_month - i
        while m <= 0:
            m += 12
            y -= 1

        start_date = datetime.date(y, m, 1)
        if m == 12:
            end_date = datetime.date(y + 1, 1, 1) - datetime.timedelta(days=1)
        else:
            end_date = datetime.date(y, m + 1, 1) - datetime.timedelta(days=1)

        # Try daily sum first
        joins = await _sum_daily_in_range(bot, guild_id, start_date, end_date)
        if joins == 0:
            # Fall back to weekly aggregates starting in this month (handles seed historical data)
            row = await bot.database.fetch_one(
                "SELECT COALESCE(SUM(joins), 0) AS s FROM member_joins "
                "WHERE guild_id = ? AND period = 'week' AND period_date BETWEEN ? AND ?",
                (guild_id, start_date.isoformat(), end_date.isoformat()),
            )
            joins = int(row["s"]) if row else 0

        label = f"{y}-{m:02d}"
        out.append((label, joins))

    return out


def _fmt_md(date_iso: str) -> str:
    """Formats 'YYYY-MM-DD' as 'Mon DD' for compact display."""
    try:
        return datetime.date.fromisoformat(date_iso).strftime("%b %d")
    except Exception:
        return date_iso


# ═══════════════════════════════════════════════
#  Embed rendering
# ═══════════════════════════════════════════════

def _delta_line(label: str, current: int, previous: int | None) -> str:
    """Renders a single 'previous → current (Δ)' growth line."""
    if previous is None:
        return f"{label:<10}│ {current:>5}   (no prior data)"

    delta = current - previous
    if delta > 0:
        arrow, sign = "📈", f"+{delta}"
    elif delta < 0:
        arrow, sign = "📉", f"{delta}"
    else:
        arrow, sign = "➖", "±0"

    pct = ""
    if previous > 0:
        pct = f" ({delta / previous * 100:+.1f}%)"
    return f"{label:<10}│ {previous:>5} → {current:<5} {arrow} {sign}{pct}"


def build_joins_field(
    timeframe: str,
    daily: list[tuple[str, int]],
    weekly: list[tuple[str, int]],
    monthly: list[tuple[str, int]] | None = None,
) -> tuple[str, str]:
    """Builds the (name, value) for the 'Registration Breakdown' report field."""
    if timeframe == "daily":
        if not daily:
            return "📅 Daily Registration Breakdown", "No registration data available."
        lines = []
        for d, c in daily:
            unit = "new member" if c == 1 else "new members"
            lines.append(f"• **{d}**: {c} {unit}")
        return "📅 Daily Registration Breakdown", "\n".join(lines)

    elif timeframe == "weekly":
        if not weekly:
            return "📅 Weekly Registration Breakdown", "No registration data available."
        lines = []
        for ws, c in weekly:
            try:
                ws_dt = datetime.date.fromisoformat(ws)
                we_dt = ws_dt + datetime.timedelta(days=6)
                ws_fmt = ws_dt.strftime("%b %d")
                we_fmt = we_dt.strftime("%b %d")
                week_range = f"{ws_fmt} – {we_fmt}"
            except Exception:
                week_range = ws
            unit = "new member" if c == 1 else "new members"
            lines.append(f"• **Week of {week_range}**: {c} {unit}")
        return "📅 Weekly Registration Breakdown", "\n".join(lines)

    elif timeframe == "monthly":
        if not monthly:
            return "📅 Monthly Registration Breakdown", "No registration data available."
        lines = []
        for m_iso, c in monthly:
            try:
                dt = datetime.datetime.strptime(m_iso, "%Y-%m")
                m_label = dt.strftime("%B %Y")
            except Exception:
                m_label = m_iso
            unit = "new member" if c == 1 else "new members"
            lines.append(f"• **{m_label}**: {c} {unit}")
        return "📅 Monthly Registration Breakdown", "\n".join(lines)

    return "📅 Registration Breakdown", "No registration data available."


def build_top_active_value(guild: discord.Guild, top_active: list[tuple[int, dict]]) -> str:
    """Renders the 'Top Most Active' field value for the reports."""
    if not top_active:
        return "No qualifying engagement was recorded in this period."

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for idx, (user_id, data) in enumerate(top_active):
        rank = medals[idx] if idx < len(medals) else f"`#{idx + 1}`"
        member = guild.get_member(user_id)
        name = member.mention if member else f"<@{user_id}>"
        lines.append(
            f"{rank} {name} — **{data['points']:,} pts**\n"
            f"      └ {data['messages']} msgs · {data['reactions']} reactions"
        )
    return (
        "\n".join(lines)
        + f"\n\n`{POINTS_PER_MESSAGE} pts` per message  •  `{POINTS_PER_REACTION} pts` per reaction"
    )


def build_placeholder_embed(timeframe: str) -> discord.Embed:
    """A 'calculating' placeholder shown the instant a report is posted."""
    embed = discord.Embed(
        title=f"📊 IM8 Member Report • {TIMEFRAME_LABEL[timeframe]}",
        description=(
            "⏳ Gathering membership statistics…\n"
            "This report will populate automatically in a moment."
        ),
        color=config.COLOR_WARNING,
    )
    embed.set_footer(text="IM8 Health • Member Report")
    return embed


def build_report_embed(
    guild: discord.Guild,
    timeframe: str,
    stats: dict,
    baseline,
    daily_joins: list[tuple[str, int]] | None = None,
    weekly_joins: list[tuple[str, int]] | None = None,
    monthly_joins: list[tuple[str, int]] | None = None,
    top_active: list[tuple[int, dict]] | None = None,
) -> discord.Embed:
    """Builds the comprehensive, public-facing member report embed."""
    label = TIMEFRAME_LABEL[timeframe]
    vs = TIMEFRAME_VS[timeframe]

    today_str = datetime.datetime.now(datetime.timezone.utc).strftime('%A, %B %d, %Y')
    embed = discord.Embed(
        title=f"📊 IM8 Member Report  •  {label}",
        description=(
            f"A community pulse-check for **{guild.name}**.\n"
            f"🗓️ **{today_str}** *(UTC)*\n\n"
            f"New-member joins **({label.lower()})**, membership growth "
            f"**{vs.lower()}**, and a live look at who's around right now."
        ),
        color=config.COLOR_BRAND,
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    # ── Headline: current membership ──
    embed.add_field(
        name="👥 Current Membership",
        value=(
            "```\n"
            f"Total      │ {stats['total']:>5}\n"
            f"Members    │ {stats['humans']:>5}\n"
            f"Bots       │ {stats['bots']:>5}\n"
            "```"
        ),
        inline=False,
    )

    # ── Registration Breakdown ──
    jn, jv = build_joins_field(
        timeframe,
        daily_joins or [],
        weekly_joins or [],
        monthly_joins or [],
    )
    embed.add_field(name=jn, value=jv, inline=False)

    # ── Growth vs the prior point in time ──
    if baseline is not None:
        when = baseline["snapshot_date"]
        b_total = baseline["total"]
        b_humans = baseline["humans"]
        b_bots = baseline["bots"]
        growth_value = (
            "```\n"
            f"{_delta_line('Total', stats['total'], b_total)}\n"
            f"{_delta_line('Members', stats['humans'], b_humans)}\n"
            f"{_delta_line('Bots', stats['bots'], b_bots)}\n"
            "```"
            f"Baseline snapshot: `{when}` (UTC)"
        )
    else:
        growth_value = (
            "```\n"
            f"{_delta_line('Total', stats['total'], None)}\n"
            "```"
            "📭 No baseline snapshot yet — growth will appear once history "
            "has been recorded. The first daily snapshot is saved automatically."
        )
    embed.add_field(
        name=f"📈 Growth  •  {vs}",
        value=growth_value,
        inline=False,
    )

    # ── Top most active members (instant — read from the live aggregates) ──
    if top_active is not None:
        embed.add_field(
            name=f"🔥 Top {TOP_ACTIVE_N} Most Active • {TOP_ACTIVE_LABEL.get(timeframe, 'Last 7 Days')}",
            value=build_top_active_value(guild, top_active),
            inline=False,
        )

    # ── Live community feed ──
    admin_role = guild.get_role(ADMIN_ROLE_ID)
    admin_str = admin_role.mention if admin_role else f"<@&{ADMIN_ROLE_ID}>"
    embed.add_field(
        name="🟢 Live Community Feed",
        value=(
            f"🟢 **Online Members:** `{stats['online']:,}`\n"
            f"🤖 **Bots:** `{stats['bots']:,}`\n"
            f"🛡️ **Admins** ({admin_str})**:** `{stats['admins']:,}`"
        ),
        inline=False,
    )

    embed.set_footer(text=f"IM8 Health • Member Report • Auto-refreshes every {REFRESH_HOURS}h")
    embed.timestamp = discord.utils.utcnow()
    return embed


def _parse_sql_ts(ts: str) -> int | None:
    """Parses an SQLite ``datetime('now')`` UTC string into a unix timestamp."""
    try:
        dt = datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=datetime.timezone.utc
        )
        return int(dt.timestamp())
    except Exception:
        return None


async def build_hub_embed(bot: commands.Bot, guild: discord.Guild) -> discord.Embed:
    """Builds the detailed control-hub overview embed for the Mod Panel."""
    embed = discord.Embed(
        title="📊 Member Report • Community Analytics",
        description=(
            "Publishes a comprehensive public report on community growth and "
            "activity. The bot records a daily membership snapshot so every "
            "report can show how the server has changed over time."
        ),
        color=config.COLOR_BRAND,
    )

    # Live snapshot preview so the operator sees current numbers instantly.
    stats = compute_member_stats(guild)
    embed.add_field(
        name="👥 Right Now",
        value=(
            "```\n"
            f"Total     │ {stats['total']:>5}\n"
            f"Members   │ {stats['humans']:>5}\n"
            f"Bots      │ {stats['bots']:>5}\n"
            f"Online    │ {stats['online']:>5}\n"
            f"Admins    │ {stats['admins']:>5}\n"
            "```"
        ),
        inline=False,
    )

    # Recent new-member joins (daily + weekly + monthly trend at a glance).
    daily = await get_daily_joins(bot, guild.id, 7)
    weekly = await get_weekly_joins(bot, guild.id, 3)
    monthly = await get_monthly_joins(bot, guild.id, 3)
    today_c = daily[-1][1] if daily else 0
    week_c = weekly[-1][1] if weekly else 0
    month_c = monthly[-1][1] if monthly else 0
    daily_line = "  ".join(f"{_fmt_md(d).split()[1]}:{c}" for d, c in daily)
    weekly_line = "  ".join(f"{_fmt_md(w)}:{c}" for w, c in weekly)
    monthly_line = "  ".join(f"{m}:{c}" for m, c in monthly)
    embed.add_field(
        name="🆕 New Joins",
        value=(
            f"**{today_c}** today  •  **{week_c}** this week  •  **{month_c}** this month\n"
            f"```\n"
            f"Last 7 days │ {daily_line}\n"
            f"By week     │ {weekly_line}\n"
            f"By month    │ {monthly_line}\n"
            f"```"
        ),
        inline=False,
    )

    # Status of any deployed public reports.
    rows = await bot.database.fetch_all(
        "SELECT * FROM member_reports WHERE guild_id = ?", (guild.id,)
    )
    if rows:
        lines = []
        for row in rows:
            ch = guild.get_channel(row["channel_id"])
            ch_str = ch.mention if ch else f"<#{row['channel_id']}>"
            ts = _parse_sql_ts(row["updated_at"]) if row["updated_at"] else None
            when = f"<t:{ts}:R>" if ts else "unknown"
            lines.append(
                f"🟢 **{TIMEFRAME_LABEL.get(row['timeframe'], row['timeframe'])}** "
                f"in {ch_str} • updated {when}"
            )
        status = "\n".join(lines) + f"\n*Auto-refreshes every {REFRESH_HOURS}h and on restart.*"
    else:
        status = "⚪ **No public report deployed.** Use a *Post* dropdown below to publish one."
    embed.add_field(name="📰 Public Reports", value=status, inline=False)

    embed.add_field(
        name="🛠️ Available Actions",
        value=(
            "**Preview Daily / Weekly / Monthly** — see the report privately first.\n"
            "**Post Daily / Weekly / Monthly** — publish a public, auto-refreshing report.\n"
            "**Refresh Now** — recalculate all live reports immediately.\n"
            "**Remove** — stop auto-refreshing the deployed reports."
        ),
        inline=False,
    )

    embed.set_footer(text="IM8 Health • Member Report")
    return embed


# ═══════════════════════════════════════════════
#  Refresh logic (shared by scheduler + UI)
# ═══════════════════════════════════════════════

async def refresh_one_report(bot: commands.Bot, row) -> bool:
    """Refreshes a single deployed report. Returns True on success.

    Prunes the config if the channel or message no longer exists.
    """
    guild = bot.get_guild(row["guild_id"])
    if guild is None:
        return False

    timeframe = row["timeframe"] if row["timeframe"] in TIMEFRAME_DAYS else "daily"

    channel = guild.get_channel(row["channel_id"])
    if channel is None:
        try:
            channel = await guild.fetch_channel(row["channel_id"])
        except Exception:
            logger.warning(f"Member Report: channel {row['channel_id']} missing; removing config.")
            await bot.database.execute(
                "DELETE FROM member_reports WHERE guild_id = ? AND timeframe = ?",
                (row["guild_id"], timeframe),
            )
            return False

    try:
        message = await channel.fetch_message(row["message_id"])
    except discord.NotFound:
        logger.warning(f"Member Report: message {row['message_id']} deleted; removing config.")
        await bot.database.execute(
            "DELETE FROM member_reports WHERE guild_id = ? AND timeframe = ?",
            (row["guild_id"], timeframe),
        )
        return False
    except Exception as e:
        logger.error(f"Member Report: could not fetch message: {e}")
        return False

    # Refresh today's snapshot, then compare against the timeframe baseline.
    stats = await record_snapshot(bot, guild)
    baseline = await get_baseline(bot, guild.id, TIMEFRAME_DAYS[timeframe])
    daily_joins = await get_daily_joins(bot, guild.id, 7)
    weekly_joins = await get_weekly_joins(bot, guild.id, 4)
    monthly_joins = await get_monthly_joins(bot, guild.id, 4)
    # The period's most active members
    ranked = await compute_engagement(bot, guild, TIMEFRAME_DAYS[timeframe])
    top_active = ranked[:TOP_ACTIVE_N]
    embed = build_report_embed(
        guild,
        timeframe,
        stats,
        baseline,
        daily_joins=daily_joins,
        weekly_joins=weekly_joins,
        monthly_joins=monthly_joins,
        top_active=top_active,
    )

    try:
        await message.edit(embed=embed)
        await bot.database.execute(
            "UPDATE member_reports SET updated_at = datetime('now') "
            "WHERE guild_id = ? AND timeframe = ?",
            (row["guild_id"], timeframe),
        )
        logger.info(f"Member Report: refreshed {timeframe} report in guild {guild.id}.")
        return True
    except discord.NotFound:
        await bot.database.execute(
            "DELETE FROM member_reports WHERE guild_id = ? AND timeframe = ?",
            (row["guild_id"], timeframe),
        )
        return False
    except Exception as e:
        logger.error(f"Member Report: failed to edit message: {e}")
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

class MemberReportHubView(discord.ui.View):
    """The Member Report control hub, opened from the Mod Panel."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def _refresh_hub(self, interaction: discord.Interaction) -> None:
        """Re-renders the hub message (overview embed + reset components)."""
        try:
            embed = await build_hub_embed(interaction.client, interaction.guild)
            await interaction.message.edit(content=None, embed=embed, view=MemberReportHubView())
        except Exception as e:
            logger.error(f"Member Report: failed to refresh hub: {e}")

    # ── Row 0: Private previews ──
    async def _preview(self, interaction: discord.Interaction, timeframe: str) -> None:
        await interaction.response.defer(ephemeral=True)
        stats = await record_snapshot(interaction.client, interaction.guild)
        baseline = await get_baseline(interaction.client, interaction.guild.id, TIMEFRAME_DAYS[timeframe])
        daily_joins = await get_daily_joins(interaction.client, interaction.guild.id, 7)
        weekly_joins = await get_weekly_joins(interaction.client, interaction.guild.id, 4)
        monthly_joins = await get_monthly_joins(interaction.client, interaction.guild.id, 4)
        ranked = await compute_engagement(interaction.client, interaction.guild, TIMEFRAME_DAYS[timeframe])
        top_active = ranked[:TOP_ACTIVE_N]
        embed = build_report_embed(
            interaction.guild,
            timeframe,
            stats,
            baseline,
            daily_joins=daily_joins,
            weekly_joins=weekly_joins,
            monthly_joins=monthly_joins,
            top_active=top_active,
        )
        await interaction.followup.send(
            content=f"📊 **{TIMEFRAME_LABEL[timeframe]} report** preview *(only you can see this).*",
            embed=embed,
            ephemeral=True,
        )

    @discord.ui.button(label="Preview Daily", emoji="📆", style=discord.ButtonStyle.primary, row=0, custom_id="im8_report_prev_daily")
    async def btn_prev_daily(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._preview(interaction, "daily")

    @discord.ui.button(label="Preview Weekly", emoji="🗓️", style=discord.ButtonStyle.primary, row=0, custom_id="im8_report_prev_weekly")
    async def btn_prev_weekly(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._preview(interaction, "weekly")

    @discord.ui.button(label="Preview Monthly", emoji="📊", style=discord.ButtonStyle.primary, row=0, custom_id="im8_report_prev_monthly")
    async def btn_prev_monthly(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._preview(interaction, "monthly")

    # ── Rows 1, 2 & 3: Post / update a public report ──
    async def _deploy(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect, timeframe: str) -> None:
        app_channel = select.values[0]
        target = interaction.guild.get_channel(app_channel.id)
        if target is None:
            try:
                target = await interaction.guild.fetch_channel(app_channel.id)
            except Exception as e:
                return await interaction.response.send_message(f"❌ Could not resolve channel: {e}", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        try:
            msg = await target.send(embed=build_placeholder_embed(timeframe))
        except Exception as e:
            return await interaction.followup.send(
                f"❌ Failed to post report to {target.mention}: {e}", ephemeral=True
            )

        await interaction.client.database.execute(
            "INSERT OR REPLACE INTO member_reports (guild_id, timeframe, channel_id, message_id, updated_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (interaction.guild.id, timeframe, target.id, msg.id),
        )

        # Fill in the real report without blocking the interaction.
        _spawn(refresh_one_report(interaction.client, {
            "guild_id": interaction.guild.id,
            "timeframe": timeframe,
            "channel_id": target.id,
            "message_id": msg.id,
        }))

        await interaction.followup.send(
            f"✅ **{TIMEFRAME_LABEL[timeframe]} Member Report** posted in {target.mention} → {msg.jump_url}\n"
            f"It auto-refreshes every **{REFRESH_HOURS}h** and on every bot restart.",
            ephemeral=True,
        )
        await self._refresh_hub(interaction)

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="📰 Post DAILY report → pick a channel", min_values=1, max_values=1, row=1, custom_id="im8_report_post_daily")
    async def select_post_daily(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        await self._deploy(interaction, select, "daily")

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="📰 Post WEEKLY report → pick a channel", min_values=1, max_values=1, row=2, custom_id="im8_report_post_weekly")
    async def select_post_weekly(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        await self._deploy(interaction, select, "weekly")

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="📰 Post MONTHLY report → pick a channel", min_values=1, max_values=1, row=3, custom_id="im8_report_post_monthly")
    async def select_post_monthly(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        await self._deploy(interaction, select, "monthly")

    # ── Row 4: Manage live reports & Navigation ──
    @discord.ui.button(label="Refresh Now", emoji="🔄", style=discord.ButtonStyle.success, row=4, custom_id="im8_report_refresh")
    async def btn_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = await interaction.client.database.fetch_all(
            "SELECT * FROM member_reports WHERE guild_id = ?", (interaction.guild.id,)
        )
        if not rows:
            return await interaction.response.send_message(
                "❌ No public report is deployed yet. Use a *Post* dropdown first.", ephemeral=True
            )
        for row in rows:
            _spawn(refresh_one_report(interaction.client, row))
        await interaction.response.send_message(
            f"🔄 Refreshing {len(rows)} live report(s) now.", ephemeral=True
        )

    @discord.ui.button(label="Remove Reports", emoji="🗑️", style=discord.ButtonStyle.danger, row=4, custom_id="im8_report_remove")
    async def btn_remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = await interaction.client.database.fetch_all(
            "SELECT * FROM member_reports WHERE guild_id = ?", (interaction.guild.id,)
        )
        if not rows:
            return await interaction.response.send_message(
                "❌ There are no deployed reports to remove.", ephemeral=True
            )
        await interaction.client.database.execute(
            "DELETE FROM member_reports WHERE guild_id = ?", (interaction.guild.id,)
        )
        await interaction.response.send_message(
            "🗑️ Auto-refresh stopped for all reports. The posted messages were left "
            "intact — delete them manually if you wish.",
            ephemeral=True,
        )
        await self._refresh_hub(interaction)

    @discord.ui.button(label="Back to Main Panel", emoji="🔙", style=discord.ButtonStyle.secondary, row=4, custom_id="im8_report_back")
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.panel import ModPanelView, Panel
        embed = await Panel.build_panel_embed(interaction.client, interaction.guild)
        await interaction.response.edit_message(content=None, embed=embed, view=ModPanelView())


# ═══════════════════════════════════════════════
#  Cog
# ═══════════════════════════════════════════════

class MemberReportCog(commands.Cog):
    """Records daily membership snapshots and publishes growth reports."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._startup_done = False
        self._startup_task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        # Persistent hub view for restart resilience.
        self.bot.add_view(MemberReportHubView())

        # Daily snapshot just after midnight UTC so growth deltas line up
        # with Discord's UTC dates.
        self.bot.scheduler.add_cron_job(
            self.record_all_snapshots,
            job_id="member_snapshot_daily",
            cron_expression="5 0 * * *",
        )
        # Periodic refresh of every deployed report (also keeps the live feed fresh).
        self.bot.scheduler.add_interval_job(
            self.refresh_all_reports,
            job_id="member_report_refresh",
            hours=REFRESH_HOURS,
        )
        logger.info("Member Report module loaded; snapshot + refresh jobs scheduled.")

    async def cog_unload(self) -> None:
        if self._startup_task and not self._startup_task.done():
            self._startup_task.cancel()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Counts a new human/bot join toward today's daily tally."""
        try:
            await record_join(self.bot, member.guild.id)
        except Exception as e:
            logger.error(f"Member Report: failed to record join in guild {member.guild.id}: {e}")

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._startup_done:
            return
        self._startup_done = True
        self._startup_task = asyncio.create_task(self._delayed_startup())

    async def _delayed_startup(self) -> None:
        # Let the gateway settle and the member cache fill before snapshotting.
        await asyncio.sleep(15)
        try:
            for guild in self.bot.guilds:
                await seed_join_history(self.bot, guild.id)
            await self.record_all_snapshots()
            await self.refresh_all_reports()
            logger.info("Member Report: startup seed + snapshot + refresh complete.")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Member Report: startup task failed: {e}")

    async def record_all_snapshots(self) -> None:
        """Records today's membership snapshot for every guild."""
        for guild in self.bot.guilds:
            try:
                await record_snapshot(self.bot, guild)
            except Exception as e:
                logger.error(f"Member Report: snapshot failed for guild {guild.id}: {e}")

    async def refresh_all_reports(self) -> None:
        """Re-renders every configured public report."""
        try:
            rows = await self.bot.database.fetch_all("SELECT * FROM member_reports")
        except Exception as e:
            logger.error(f"Member Report: failed to load report configs: {e}")
            return
        for row in rows:
            await refresh_one_report(self.bot, row)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MemberReportCog(bot))
