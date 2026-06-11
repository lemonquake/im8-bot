"""
IM8 Bot — Retention & Cohorts Module
The community-health metric no off-the-shelf Discord bot offers: weekly join
cohorts and the percentage of each cohort still active 1, 4, and 12 weeks later.

How it works
────────────
• Every member's join week (Monday-anchored, UTC) is their **cohort**. New
  joins are recorded live; existing members are backfilled from the member
  cache on startup.
• A member is **active in a week** if they posted at least one message in the
  guild that week. Activity is recorded live per (member, week); historical
  activity can be seeded from the tracked channels' message history.
• Retention for cohort *C* at offset *N* weeks = the share of members who joined
  in week *C* that were active in week *C + N*.

Honesty notes (surfaced in the report)
───────────────────────────────────────
• Cohorts that formed **before** cohort tracking began only contain members who
  are still in the server (churned members were never recorded), so their
  denominator is a survivor count — these rows are marked ``~`` and excluded
  from the headline averages.
• A cohort's offset cell is only shown once we actually have activity data
  covering that target week; otherwise it reads ``—``.
"""

import discord
from discord.ext import commands
import logging
import asyncio
import datetime
from collections import defaultdict

import config
# Tracked channels are reused for the optional history backfill so the seeded
# activity lines up with the rest of the analytics suite.
from cogs.active import TRACKED_CHANNELS, IGNORED_CHANNEL_ID

logger = logging.getLogger("im8bot.cogs.retention")

# ═══════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════

# Retention offsets (in weeks) shown as columns. "% still active after N weeks."
OFFSETS: tuple[int, ...] = (1, 4, 12)

# How many recent cohorts (weeks) to display.
COHORTS_SHOWN = 8

# Auto-refresh cadence for a deployed public report.
REFRESH_HOURS = 6

# Default look-back window (days) for the optional history backfill.
BACKFILL_DAYS = 90


# ═══════════════════════════════════════════════
#  Date helpers (UTC, Monday-anchored weeks)
# ═══════════════════════════════════════════════

def _utc_today() -> datetime.date:
    return datetime.datetime.now(datetime.timezone.utc).date()


def _week_start(d: datetime.date) -> datetime.date:
    """Monday of the week containing ``d``."""
    return d - datetime.timedelta(days=d.weekday())


def _fmt_md(date_iso: str) -> str:
    try:
        return datetime.date.fromisoformat(date_iso).strftime("%b %d")
    except Exception:
        return date_iso


def _parse_sql_ts(ts: str) -> int | None:
    """Parses an SQLite ``datetime('now')`` UTC string into a unix timestamp."""
    try:
        dt = datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=datetime.timezone.utc
        )
        return int(dt.timestamp())
    except Exception:
        return None


# ═══════════════════════════════════════════════
#  Metadata helpers
# ═══════════════════════════════════════════════

async def get_meta(bot: commands.Bot, guild_id: int, key: str) -> str | None:
    row = await bot.database.fetch_one(
        "SELECT value FROM analytics_meta WHERE guild_id = ? AND key = ?",
        (guild_id, key),
    )
    return row["value"] if row else None


async def set_meta(bot: commands.Bot, guild_id: int, key: str, value: str) -> None:
    await bot.database.execute(
        "INSERT OR REPLACE INTO analytics_meta (guild_id, key, value) VALUES (?, ?, ?)",
        (guild_id, key, value),
    )


async def ensure_cohort_start(bot: commands.Bot, guild_id: int) -> None:
    """Records the date cohort tracking began (once), so we can tell which
    cohorts have a complete denominator and which are survivor-only."""
    existing = await get_meta(bot, guild_id, "cohort_start")
    if existing is None:
        await bot.database.execute(
            "INSERT OR IGNORE INTO analytics_meta (guild_id, key, value) VALUES (?, 'cohort_start', ?)",
            (guild_id, _utc_today().isoformat()),
        )


# ═══════════════════════════════════════════════
#  Recording (live)
# ═══════════════════════════════════════════════

async def record_cohort(bot: commands.Bot, guild_id: int, user_id: int, joined_at: datetime.datetime) -> None:
    """Assigns a member to their join-week cohort (idempotent)."""
    joined = joined_at.astimezone(datetime.timezone.utc)
    cohort_week = _week_start(joined.date()).isoformat()
    await bot.database.execute(
        "INSERT OR IGNORE INTO member_cohorts (guild_id, user_id, joined_at, cohort_week) "
        "VALUES (?, ?, ?, ?)",
        (guild_id, user_id, joined.isoformat(), cohort_week),
    )


async def record_activity(bot: commands.Bot, guild_id: int, user_id: int, when: datetime.datetime) -> None:
    """Marks a member active for the week containing ``when``."""
    when = when.astimezone(datetime.timezone.utc)
    week = _week_start(when.date()).isoformat()
    await bot.database.execute(
        "INSERT INTO member_activity_weeks (guild_id, user_id, activity_week, messages, last_active) "
        "VALUES (?, ?, ?, 1, ?) "
        "ON CONFLICT(guild_id, user_id, activity_week) DO UPDATE SET "
        "messages = messages + 1, last_active = excluded.last_active",
        (guild_id, user_id, week, when.isoformat()),
    )


# ═══════════════════════════════════════════════
#  Backfill (bootstrap from existing state)
# ═══════════════════════════════════════════════

async def backfill_cohorts(bot: commands.Bot, guild: discord.Guild) -> int:
    """Seeds cohorts from the current member cache. Returns rows touched.

    Members who already left were never observed, so only current members can
    be backfilled — see the module docstring's survivorship note.
    """
    touched = 0
    for member in guild.members:
        if member.bot or member.joined_at is None:
            continue
        await record_cohort(bot, guild.id, member.id, member.joined_at)
        touched += 1
    return touched


async def backfill_activity(bot: commands.Bot, guild: discord.Guild, days: int = BACKFILL_DAYS) -> dict:
    """Seeds weekly activity from the tracked channels' message history.

    Best-effort: covers the tracked channels only and counts messages (not
    reactions). Safe to re-run — counts are merged with ``MAX`` so a repeat
    backfill never inflates an existing week. Returns a small stats dict.
    """
    cutoff = discord.utils.utcnow() - datetime.timedelta(days=days)
    # (user_id, week_iso) -> [messages, last_active_iso]
    buckets: dict[tuple[int, str], list] = defaultdict(lambda: [0, None])
    scanned_channels = 0

    for ch_id in TRACKED_CHANNELS:
        if ch_id == IGNORED_CHANNEL_ID:
            continue
        channel = guild.get_channel(ch_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(ch_id)
            except Exception:
                logger.warning(f"Retention: could not resolve channel {ch_id} for backfill.")
                continue
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            continue

        scanned_channels += 1
        try:
            async for msg in channel.history(limit=None, after=cutoff):
                if msg.author.bot:
                    continue
                created = msg.created_at.astimezone(datetime.timezone.utc)
                week = _week_start(created.date()).isoformat()
                slot = buckets[(msg.author.id, week)]
                slot[0] += 1
                iso = created.isoformat()
                if slot[1] is None or iso > slot[1]:
                    slot[1] = iso
        except discord.Forbidden:
            logger.warning(f"Retention: missing read access to channel {ch_id} during backfill.")
        except Exception as e:
            logger.error(f"Retention: error scanning channel {ch_id} during backfill: {e}")

    for (user_id, week), (count, last_active) in buckets.items():
        await bot.database.execute(
            "INSERT INTO member_activity_weeks (guild_id, user_id, activity_week, messages, last_active) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id, user_id, activity_week) DO UPDATE SET "
            "messages = MAX(messages, excluded.messages), "
            "last_active = MAX(COALESCE(last_active, ''), COALESCE(excluded.last_active, ''))",
            (guild.id, user_id, week, count, last_active),
        )

    await set_meta(bot, guild.id, "last_backfill", _utc_today().isoformat())
    return {
        "channels": scanned_channels,
        "member_weeks": len(buckets),
        "days": days,
    }


# ═══════════════════════════════════════════════
#  Retention computation
# ═══════════════════════════════════════════════

async def compute_retention(
    bot: commands.Bot,
    guild: discord.Guild,
    n_cohorts: int = COHORTS_SHOWN,
    offsets: tuple[int, ...] = OFFSETS,
) -> dict:
    """Computes cohort retention for the most recent ``n_cohorts`` weeks.

    Returns a dict with per-cohort rows (newest first), the weighted overall
    retention per offset, and coverage metadata for honest rendering.
    """
    gid = guild.id
    current_week = _week_start(_utc_today())
    oldest_shown = current_week - datetime.timedelta(weeks=n_cohorts - 1)

    # When did cohort tracking begin? Cohorts before this are survivor-only.
    cohort_start_iso = await get_meta(bot, gid, "cohort_start")
    try:
        cohort_start_week = _week_start(datetime.date.fromisoformat(cohort_start_iso)) if cohort_start_iso else current_week
    except Exception:
        cohort_start_week = current_week

    # Activity-data coverage window.
    cov = await bot.database.fetch_one(
        "SELECT MIN(activity_week) AS lo, MAX(activity_week) AS hi "
        "FROM member_activity_weeks WHERE guild_id = ?",
        (gid,),
    )
    earliest_activity = None
    if cov and cov["lo"]:
        try:
            earliest_activity = datetime.date.fromisoformat(cov["lo"])
        except Exception:
            earliest_activity = None

    # Cohort membership for the shown window.
    cohort_rows = await bot.database.fetch_all(
        "SELECT cohort_week, user_id FROM member_cohorts "
        "WHERE guild_id = ? AND cohort_week >= ?",
        (gid, oldest_shown.isoformat()),
    )
    cohorts: dict[str, set] = defaultdict(set)
    for r in cohort_rows:
        cohorts[r["cohort_week"]].add(r["user_id"])

    # Gather every measurement week we'll need, then load activity for them once.
    needed_weeks: set[str] = set()
    for cw_iso in cohorts:
        cw = datetime.date.fromisoformat(cw_iso)
        for off in offsets:
            needed_weeks.add((cw + datetime.timedelta(weeks=off)).isoformat())

    active_by_week: dict[str, set] = defaultdict(set)
    if needed_weeks:
        placeholders = ",".join("?" * len(needed_weeks))
        arows = await bot.database.fetch_all(
            f"SELECT activity_week, user_id FROM member_activity_weeks "
            f"WHERE guild_id = ? AND activity_week IN ({placeholders})",
            (gid, *needed_weeks),
        )
        for r in arows:
            active_by_week[r["activity_week"]].add(r["user_id"])

    rows = []
    # Weighted overall: per offset, sum active / sum size over measurable, complete cohorts.
    overall_active = {off: 0 for off in offsets}
    overall_size = {off: 0 for off in offsets}

    for i in range(n_cohorts):
        cw = current_week - datetime.timedelta(weeks=i)
        cw_iso = cw.isoformat()
        members = cohorts.get(cw_iso, set())
        size = len(members)
        complete = cw >= cohort_start_week

        cells = []
        for off in offsets:
            mw = cw + datetime.timedelta(weeks=off)
            measurable = (
                earliest_activity is not None
                and earliest_activity <= mw <= current_week
                and size > 0
            )
            if not measurable:
                cells.append(None)
                continue
            active = len(members & active_by_week.get(mw.isoformat(), set()))
            pct = active / size * 100 if size else 0.0
            cells.append({"active": active, "size": size, "pct": pct})
            # Headline averages use only complete-denominator cohorts.
            if complete:
                overall_active[off] += active
                overall_size[off] += size

        rows.append({
            "week": cw_iso,
            "size": size,
            "complete": complete,
            "current": cw == current_week,
            "cells": cells,
        })

    overall = {}
    for off in offsets:
        overall[off] = (overall_active[off] / overall_size[off] * 100) if overall_size[off] else None

    return {
        "rows": rows,
        "offsets": offsets,
        "overall": overall,
        "cohort_start_week": cohort_start_week,
        "earliest_activity": earliest_activity,
        "current_week": current_week,
        "tracked_members": sum(len(s) for s in cohorts.values()),
    }


# ═══════════════════════════════════════════════
#  Embed rendering
# ═══════════════════════════════════════════════

def _fmt_cell(cell) -> str:
    if cell is None:
        return "  —  "
    return f"{cell['pct']:>3.0f}% "


def build_retention_field(data: dict) -> tuple[str, str]:
    """Builds the (name, value) for the cohort-retention table field."""
    offsets = data["offsets"]
    # Header: Cohort | Size | W1 | W4 | W12
    head = f"{'Cohort':<9}│{'Size':>5} │" + "│".join(f"{'W'+str(o):>5}" for o in offsets)
    sep = "─" * 9 + "┼" + "─" * 6 + "┼" + "┼".join("─" * 5 for _ in offsets)
    lines = [head, sep]

    any_incomplete = False
    for row in data["rows"]:
        if row["size"] == 0 and not row["current"]:
            # Skip empty historical weeks to reduce clutter (no joins recorded).
            continue
        marker = ""
        if row["current"]:
            marker = "*"
        elif not row["complete"]:
            marker = "~"
            any_incomplete = True
        label = f"{_fmt_md(row['week']):<7}{marker:<2}"
        cells = "│".join(_fmt_cell(c) for c in row["cells"])
        lines.append(f"{label}│{row['size']:>5} │{cells}")

    table = "```\n" + "\n".join(lines) + "\n```"

    notes = []
    notes.append("`*` current week (in progress)")
    if any_incomplete:
        notes.append("`~` formed before tracking began — survivor denominator, excluded from averages")
    notes.append("`—` not enough history yet for that window")
    value = table + "\n" + "  •  ".join(notes)
    return "📊 Weekly Join Cohorts — % still active", value


def build_overall_field(data: dict) -> tuple[str, str]:
    parts = []
    for off in data["offsets"]:
        v = data["overall"][off]
        parts.append(f"After **{off}w**: " + (f"**{v:.0f}%**" if v is not None else "—"))
    return "🎯 Overall Retention (complete cohorts)", "  •  ".join(parts)


def build_placeholder_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📈 IM8 Retention & Cohorts",
        description=(
            "⏳ Crunching weekly join cohorts…\n"
            "This report will populate automatically in a moment."
        ),
        color=config.COLOR_WARNING,
    )
    embed.set_footer(text="IM8 Health • Retention")
    return embed


def build_report_embed(guild: discord.Guild, data: dict) -> discord.Embed:
    """The public-facing retention report."""
    embed = discord.Embed(
        title="📈 IM8 Retention & Cohorts",
        description=(
            f"How well **{guild.name}** keeps the members it gains. Each row is the "
            f"group of members who joined that week; the columns show the share still "
            f"active **1 / 4 / 12 weeks** later. *Active = posted a message that week.*"
        ),
        color=config.COLOR_BRAND,
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    on, ov = build_overall_field(data)
    embed.add_field(name=on, value=ov, inline=False)

    rn, rv = build_retention_field(data)
    embed.add_field(name=rn, value=rv, inline=False)

    # Coverage footer line.
    ea = data["earliest_activity"]
    cov_line = (
        f"Activity data since `{ea.isoformat()}`" if ea else "No activity data recorded yet"
    )
    embed.add_field(
        name="ℹ️ Coverage",
        value=(
            f"{cov_line}  •  cohort tracking since `{data['cohort_start_week'].isoformat()}`\n"
            f"Tracking **{data['tracked_members']:,}** members across the last {COHORTS_SHOWN} weekly cohorts."
        ),
        inline=False,
    )

    embed.set_footer(text=f"IM8 Health • Retention • Auto-refreshes every {REFRESH_HOURS}h")
    embed.timestamp = discord.utils.utcnow()
    return embed


async def build_hub_embed(bot: commands.Bot, guild: discord.Guild) -> discord.Embed:
    """The Mod Panel control-hub overview."""
    embed = discord.Embed(
        title="📈 Retention & Cohorts • Community Health",
        description=(
            "Tracks weekly join cohorts and the percentage of each still active "
            "weeks later — the single best measure of whether the community is "
            "*growing* or just *churning*. No paid Discord bot offers this."
        ),
        color=config.COLOR_BRAND,
    )

    data = await compute_retention(bot, guild)
    on, ov = build_overall_field(data)
    embed.add_field(name=on, value=ov, inline=False)

    # A compact preview of the most recent measurable cohorts.
    rn, rv = build_retention_field(data)
    embed.add_field(name=rn, value=rv, inline=False)

    # Deployed report status.
    row = await bot.database.fetch_one(
        "SELECT * FROM retention_reports WHERE guild_id = ?", (guild.id,)
    )
    if row:
        ch = guild.get_channel(row["channel_id"])
        ch_str = ch.mention if ch else f"<#{row['channel_id']}>"
        ts = _parse_sql_ts(row["updated_at"]) if row["updated_at"] else None
        when = f"<t:{ts}:R>" if ts else "unknown"
        status = (
            f"🟢 **Live** in {ch_str} • updated {when}\n"
            f"Auto-refreshes every **{REFRESH_HOURS}h** and on every bot restart."
        )
    else:
        status = "⚪ **Not deployed.** Use *Post Report* below to publish one."
    embed.add_field(name="📰 Public Report", value=status, inline=False)

    last_backfill = await get_meta(bot, guild.id, "last_backfill")
    bf = f"last run `{last_backfill}`" if last_backfill else "never run"
    embed.add_field(
        name="🛠️ Available Actions",
        value=(
            "**Preview** — see the retention report privately first.\n"
            "**Post Report** — publish a public, auto-refreshing report.\n"
            "**Backfill History** — seed past activity from the tracked channels "
            f"(last {BACKFILL_DAYS} days) so retention has real history immediately ({bf}).\n"
            "**Refresh Now** — recalculate the live report.\n"
            "**Remove** — stop auto-refreshing the deployed report."
        ),
        inline=False,
    )

    embed.set_footer(text="IM8 Health • Retention")
    return embed


# ═══════════════════════════════════════════════
#  Refresh logic (shared by scheduler + UI)
# ═══════════════════════════════════════════════

async def refresh_one_report(bot: commands.Bot, row) -> bool:
    """Refreshes a single deployed retention report. Prunes stale config."""
    guild = bot.get_guild(row["guild_id"])
    if guild is None:
        return False

    channel = guild.get_channel(row["channel_id"])
    if channel is None:
        try:
            channel = await guild.fetch_channel(row["channel_id"])
        except Exception:
            logger.warning(f"Retention: channel {row['channel_id']} missing; removing config.")
            await bot.database.execute("DELETE FROM retention_reports WHERE guild_id = ?", (row["guild_id"],))
            return False

    try:
        message = await channel.fetch_message(row["message_id"])
    except discord.NotFound:
        logger.warning(f"Retention: message {row['message_id']} deleted; removing config.")
        await bot.database.execute("DELETE FROM retention_reports WHERE guild_id = ?", (row["guild_id"],))
        return False
    except Exception as e:
        logger.error(f"Retention: could not fetch message: {e}")
        return False

    data = await compute_retention(bot, guild)
    embed = build_report_embed(guild, data)
    try:
        await message.edit(embed=embed)
        await bot.database.execute(
            "UPDATE retention_reports SET updated_at = datetime('now') WHERE guild_id = ?",
            (row["guild_id"],),
        )
        logger.info(f"Retention: refreshed report in guild {guild.id}.")
        return True
    except discord.NotFound:
        await bot.database.execute("DELETE FROM retention_reports WHERE guild_id = ?", (row["guild_id"],))
        return False
    except Exception as e:
        logger.error(f"Retention: failed to edit message: {e}")
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

class RetentionHubView(discord.ui.View):
    """The Retention control hub, opened from the Mod Panel."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def _refresh_hub(self, interaction: discord.Interaction) -> None:
        try:
            embed = await build_hub_embed(interaction.client, interaction.guild)
            await interaction.message.edit(content=None, embed=embed, view=RetentionHubView())
        except Exception as e:
            logger.error(f"Retention: failed to refresh hub: {e}")

    # ── Row 0: Preview / Refresh ──
    @discord.ui.button(label="Preview", emoji="🔍", style=discord.ButtonStyle.primary, row=0, custom_id="im8_retention_preview")
    async def btn_preview(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        data = await compute_retention(interaction.client, interaction.guild)
        embed = build_report_embed(interaction.guild, data)
        await interaction.followup.send(
            content="📈 **Retention report** preview *(only you can see this).*",
            embed=embed,
            ephemeral=True,
        )

    @discord.ui.button(label="Backfill History", emoji="⏳", style=discord.ButtonStyle.secondary, row=0, custom_id="im8_retention_backfill")
    async def btn_backfill(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"⏳ Seeding activity from the tracked channels (last {BACKFILL_DAYS} days). "
            "This runs in the background and can take a few minutes — the report will "
            "fill in with real history when it finishes.",
            ephemeral=True,
        )

        async def _run():
            try:
                stats = await backfill_activity(interaction.client, interaction.guild)
                logger.info(f"Retention: backfill complete in guild {interaction.guild.id}: {stats}")
                # Refresh any deployed report so new history shows immediately.
                row = await interaction.client.database.fetch_one(
                    "SELECT * FROM retention_reports WHERE guild_id = ?", (interaction.guild.id,)
                )
                if row:
                    await refresh_one_report(interaction.client, row)
            except Exception as e:
                logger.error(f"Retention: backfill failed: {e}")

        _spawn(_run())

    @discord.ui.button(label="Refresh Now", emoji="🔄", style=discord.ButtonStyle.success, row=0, custom_id="im8_retention_refresh")
    async def btn_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = await interaction.client.database.fetch_one(
            "SELECT * FROM retention_reports WHERE guild_id = ?", (interaction.guild.id,)
        )
        if not row:
            return await interaction.response.send_message(
                "❌ No public report is deployed yet. Use *Post Report* first.", ephemeral=True
            )
        _spawn(refresh_one_report(interaction.client, row))
        await interaction.response.send_message("🔄 Refreshing the live report now.", ephemeral=True)

    # ── Row 1: Post a public report ──
    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="📰 Post report → pick a channel", min_values=1, max_values=1, row=1, custom_id="im8_retention_post")
    async def select_post(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        app_channel = select.values[0]
        target = interaction.guild.get_channel(app_channel.id)
        if target is None:
            try:
                target = await interaction.guild.fetch_channel(app_channel.id)
            except Exception as e:
                return await interaction.response.send_message(f"❌ Could not resolve channel: {e}", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        try:
            msg = await target.send(embed=build_placeholder_embed())
        except Exception as e:
            return await interaction.followup.send(f"❌ Failed to post report to {target.mention}: {e}", ephemeral=True)

        await interaction.client.database.execute(
            "INSERT OR REPLACE INTO retention_reports (guild_id, channel_id, message_id, updated_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (interaction.guild.id, target.id, msg.id),
        )
        _spawn(refresh_one_report(interaction.client, {
            "guild_id": interaction.guild.id,
            "channel_id": target.id,
            "message_id": msg.id,
        }))
        await interaction.followup.send(
            f"✅ **Retention report** posted in {target.mention} → {msg.jump_url}\n"
            f"It auto-refreshes every **{REFRESH_HOURS}h** and on every bot restart.",
            ephemeral=True,
        )
        await self._refresh_hub(interaction)

    # ── Row 2: Manage ──
    @discord.ui.button(label="Remove Report", emoji="🗑️", style=discord.ButtonStyle.danger, row=2, custom_id="im8_retention_remove")
    async def btn_remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = await interaction.client.database.fetch_one(
            "SELECT * FROM retention_reports WHERE guild_id = ?", (interaction.guild.id,)
        )
        if not row:
            return await interaction.response.send_message(
                "❌ There is no deployed report to remove.", ephemeral=True
            )
        await interaction.client.database.execute(
            "DELETE FROM retention_reports WHERE guild_id = ?", (interaction.guild.id,)
        )
        await interaction.response.send_message(
            "🗑️ Auto-refresh stopped. The posted message was left intact — delete it manually if you wish.",
            ephemeral=True,
        )
        await self._refresh_hub(interaction)

    # ── Row 4: Navigation ──
    @discord.ui.button(label="Back to Main Panel", emoji="🔙", style=discord.ButtonStyle.secondary, row=4, custom_id="im8_retention_back")
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.panel import ModPanelView, Panel
        embed = await Panel.build_panel_embed(interaction.client, interaction.guild)
        await interaction.response.edit_message(content=None, embed=embed, view=ModPanelView())


# ═══════════════════════════════════════════════
#  Cog
# ═══════════════════════════════════════════════

class RetentionCog(commands.Cog):
    """Records join cohorts + weekly activity and reports retention curves."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._startup_done = False
        self._startup_task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        self.bot.add_view(RetentionHubView())

        # Re-backfill cohorts from the cache daily so newly-cached members and
        # any joins missed while offline get assigned to a cohort.
        self.bot.scheduler.add_cron_job(
            self.backfill_all_cohorts,
            job_id="retention_cohort_backfill",
            cron_expression="10 0 * * *",
        )
        # Periodic refresh of the deployed report.
        self.bot.scheduler.add_interval_job(
            self.refresh_all_reports,
            job_id="retention_report_refresh",
            hours=REFRESH_HOURS,
        )
        logger.info("Retention module loaded; cohort + refresh jobs scheduled.")

    async def cog_unload(self) -> None:
        if self._startup_task and not self._startup_task.done():
            self._startup_task.cancel()

    # ── Live recording ──
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        try:
            joined = member.joined_at or discord.utils.utcnow()
            await record_cohort(self.bot, member.guild.id, member.id, joined)
        except Exception as e:
            logger.error(f"Retention: failed to record cohort for {member.id}: {e}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        try:
            await record_activity(self.bot, message.guild.id, message.author.id, message.created_at)
        except Exception as e:
            logger.error(f"Retention: failed to record activity: {e}")

    # ── Startup / scheduled ──
    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._startup_done:
            return
        self._startup_done = True
        self._startup_task = asyncio.create_task(self._delayed_startup())

    async def _delayed_startup(self) -> None:
        await asyncio.sleep(15)  # let the member cache fill
        try:
            for guild in self.bot.guilds:
                await ensure_cohort_start(self.bot, guild.id)
                count = await backfill_cohorts(self.bot, guild)
                logger.info(f"Retention: seeded {count} cohorts for guild {guild.id}.")
            await self.refresh_all_reports()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Retention: startup task failed: {e}")

    async def backfill_all_cohorts(self) -> None:
        for guild in self.bot.guilds:
            try:
                await ensure_cohort_start(self.bot, guild.id)
                await backfill_cohorts(self.bot, guild)
            except Exception as e:
                logger.error(f"Retention: cohort backfill failed for guild {guild.id}: {e}")

    async def refresh_all_reports(self) -> None:
        try:
            rows = await self.bot.database.fetch_all("SELECT * FROM retention_reports")
        except Exception as e:
            logger.error(f"Retention: failed to load report configs: {e}")
            return
        for row in rows:
            await refresh_one_report(self.bot, row)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RetentionCog(bot))
