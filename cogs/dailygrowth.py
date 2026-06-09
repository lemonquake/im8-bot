"""
IM8 Bot — Daily Growth Report Module
Posts a comprehensive daily report combining new-member growth (counted by
join timestamp and recorded so the next run can show a day-over-day delta) with
the day's top 3 most active members. A fresh report is posted to a configured
channel every day, and can be previewed or sent on demand from the Mod Panel.
"""

import discord
from discord.ext import commands
import logging
import asyncio
import datetime
import json

import config
# Reuse the engagement engine + tuning from the Most Active module so the
# "top active" ranking stays consistent across features. EXCLUDED_ROLE_IDS
# already excludes the two staff roles that must never appear here.
from cogs.active import (
    compute_engagement,
    EXCLUDED_ROLE_IDS,
    POINTS_PER_MESSAGE,
    POINTS_PER_REACTION,
    TRACKED_CHANNELS,
)

logger = logging.getLogger("im8bot.cogs.dailygrowth")

# ═══════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════

# How many of the day's most active members to surface.
TOP_N = 3

# When the automatic daily report fires (UTC). 23:55 captures the full day
# that is about to end before the date rolls over.
DAILY_CRON = "55 23 * * *"

# Cap on the number of new-joiner names listed inline (avoids oversized embeds).
MAX_LISTED_JOINERS = 15


# ═══════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════

def _utc_today() -> datetime.date:
    return datetime.datetime.now(datetime.timezone.utc).date()


def count_new_members(guild: discord.Guild, target_date: datetime.date) -> list[discord.Member]:
    """Returns the members whose join timestamp falls on ``target_date`` (UTC).

    Uses ``Member.joined_at`` directly so the count reflects who actually
    joined that calendar day, independent of whether the bot was online to
    observe the join event. Sorted earliest-join first.
    """
    joined: list[discord.Member] = []
    for m in guild.members:
        if m.joined_at is None:
            continue
        if m.joined_at.astimezone(datetime.timezone.utc).date() == target_date:
            joined.append(m)
    joined.sort(key=lambda m: m.joined_at or datetime.datetime.max.replace(tzinfo=datetime.timezone.utc))
    return joined


async def get_previous_log(bot: commands.Bot, guild_id: int, before_date: datetime.date):
    """Most recent recorded growth log strictly before ``before_date``.

    This is the "reference on the next run": the prior day we recorded, used to
    compute the day-over-day change in new members.
    """
    return await bot.database.fetch_one(
        "SELECT * FROM daily_growth_log WHERE guild_id = ? AND report_date < ? "
        "ORDER BY report_date DESC LIMIT 1",
        (guild_id, before_date.isoformat()),
    )


async def record_log(
    bot: commands.Bot,
    guild_id: int,
    report_date: datetime.date,
    new_members: int,
    top_active: list[dict],
) -> None:
    """Writes (or overwrites) the growth record for ``report_date``."""
    await bot.database.execute(
        "INSERT OR REPLACE INTO daily_growth_log "
        "(guild_id, report_date, new_members, top_active, recorded_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        (guild_id, report_date.isoformat(), new_members, json.dumps(top_active)),
    )


def _trend_line(today: int, previous: int | None) -> str:
    """Renders a 'vs. previous recorded day' trend string."""
    if previous is None:
        return "— *no prior day on record yet*"
    delta = today - previous
    if delta > 0:
        body = f"📈 **+{delta}** vs. previous day ({previous})"
    elif delta < 0:
        body = f"📉 **{delta}** vs. previous day ({previous})"
    else:
        body = f"➖ **no change** vs. previous day ({previous})"
    if previous > 0:
        body += f"  ·  {delta / previous * 100:+.0f}%"
    return body


# ═══════════════════════════════════════════════
#  Embed rendering
# ═══════════════════════════════════════════════

def build_placeholder_embed(target_date: datetime.date) -> discord.Embed:
    """A 'calculating' placeholder shown the instant a report is posted."""
    embed = discord.Embed(
        title="📊 IM8 Daily Growth Report",
        description=(
            f"**{target_date.strftime('%A, %B %d, %Y')}**\n\n"
            "⏳ Tallying new members and scanning the day's engagement…\n"
            "This report will populate automatically in a moment."
        ),
        color=config.COLOR_WARNING,
    )
    embed.set_footer(text="IM8 Health • Daily Growth")
    return embed


def build_growth_embed(
    guild: discord.Guild,
    target_date: datetime.date,
    new_joiners: list[discord.Member],
    previous_log,
    top_active: list[tuple[int, dict]],
) -> discord.Embed:
    """Builds the comprehensive, public-facing Daily Growth Report embed."""
    new_count = len(new_joiners)
    prev_count = previous_log["new_members"] if previous_log else None

    embed = discord.Embed(
        title="📊 IM8 Daily Growth Report",
        description=(
            f"A daily pulse-check for **{guild.name}**.\n"
            f"🗓️ **{target_date.strftime('%A, %B %d, %Y')}** *(UTC)*"
        ),
        color=config.COLOR_BRAND,
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    # ── New members joined today ──
    embed.add_field(
        name="🆕 New Members Today",
        value=(
            f"**{new_count}** new member(s) joined today.\n"
            f"{_trend_line(new_count, prev_count)}"
        ),
        inline=False,
    )

    # Compact roster of who joined (newest community members get a shout-out).
    if new_joiners:
        names = [m.mention for m in new_joiners[:MAX_LISTED_JOINERS]]
        extra = new_count - len(names)
        roster = " ".join(names)
        if extra > 0:
            roster += f"  *and {extra} more…*"
        embed.add_field(name="👋 Welcome", value=roster, inline=False)

    # ── Top 3 most active today ──
    medals = ["🥇", "🥈", "🥉"]
    if top_active:
        lines = []
        for idx, (user_id, data) in enumerate(top_active):
            rank = medals[idx] if idx < len(medals) else f"`#{idx + 1}`"
            member = guild.get_member(user_id)
            name = member.mention if member else f"<@{user_id}>"
            lines.append(
                f"{rank} {name} — **{data['points']:,} pts**\n"
                f"      └ {data['messages']} msgs · {data['reactions']} reactions"
            )
        active_value = "\n".join(lines)
    else:
        active_value = "No qualifying engagement was recorded today."

    embed.add_field(
        name=f"🔥 Most Active Today — Top {TOP_N}",
        value=(
            active_value
            + f"\n\n`{POINTS_PER_MESSAGE} pts` per message  •  "
            f"`{POINTS_PER_REACTION} pts` per reaction"
        ),
        inline=False,
    )

    embed.set_footer(text="IM8 Health • Daily Growth • Generated")
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


def _fmt_channels(guild: discord.Guild) -> str:
    """Renders the engagement-tracked channel list as mentions."""
    parts = []
    for cid in TRACKED_CHANNELS:
        ch = guild.get_channel(cid)
        parts.append(ch.mention if ch else f"<#{cid}>")
    return " ".join(parts)


async def build_hub_embed(bot: commands.Bot, guild: discord.Guild) -> discord.Embed:
    """Builds the detailed control-hub overview embed for the Mod Panel."""
    embed = discord.Embed(
        title="📊 Daily Growth • New Members + Top Movers",
        description=(
            "Publishes a daily report combining new-member growth (counted by "
            "join timestamp and recorded for day-over-day comparison) with the "
            "day's **top 3** most active members. A fresh report is posted every "
            f"day at **23:55 UTC**."
        ),
        color=config.COLOR_BRAND,
    )

    # Live preview of today's numbers so the operator sees them instantly.
    today = _utc_today()
    new_today = len(count_new_members(guild, today))
    prev = await get_previous_log(bot, guild.id, today)
    embed.add_field(
        name="🆕 New Members Today (so far)",
        value=f"**{new_today}** joined today\n{_trend_line(new_today, prev['new_members'] if prev else None)}",
        inline=False,
    )

    excluded_str = " ".join(f"<@&{rid}>" for rid in EXCLUDED_ROLE_IDS) or "none"
    embed.add_field(
        name="🔥 Engagement Source",
        value=(
            f"Tracked channels: {_fmt_channels(guild)}\n"
            f"Excluded staff roles: {excluded_str}\n"
            f"Scoring: `{POINTS_PER_MESSAGE}`/msg · `{POINTS_PER_REACTION}`/reaction"
        ),
        inline=False,
    )

    # Deployment status.
    row = await bot.database.fetch_one(
        "SELECT * FROM daily_growth_reports WHERE guild_id = ?", (guild.id,)
    )
    if row:
        ch = guild.get_channel(row["channel_id"])
        ch_str = ch.mention if ch else f"<#{row['channel_id']}>"
        ts = _parse_sql_ts(row["updated_at"]) if row["updated_at"] else None
        when = f"<t:{ts}:R>" if ts else "unknown"
        status = (
            f"🟢 **Active** → posts to {ch_str}\n"
            f"Last report: {when}\n"
            f"A new report is posted automatically every day at **23:55 UTC**."
        )
    else:
        status = "⚪ **Not configured.** Pick a channel below to start daily reports."
    embed.add_field(name="📰 Daily Report", value=status, inline=False)

    embed.add_field(
        name="🛠️ Available Actions",
        value=(
            "**Preview Today** — see today's report privately first.\n"
            "**Set Channel** — choose where daily reports are posted.\n"
            "**Send Now** — post today's report to the channel immediately.\n"
            "**Stop Daily Reports** — disable the automatic daily post."
        ),
        inline=False,
    )

    embed.set_footer(text="IM8 Health • Daily Growth")
    return embed


# ═══════════════════════════════════════════════
#  Report generation (shared by scheduler + UI)
# ═══════════════════════════════════════════════

async def compute_report(bot: commands.Bot, guild: discord.Guild, target_date: datetime.date):
    """Computes the day's new joiners and top movers.

    Returns ``(new_joiners, previous_log, top_active)``.
    """
    new_joiners = count_new_members(guild, target_date)
    previous_log = await get_previous_log(bot, guild.id, target_date)
    ranked = await compute_engagement(guild, 1)
    top_active = ranked[:TOP_N]
    return new_joiners, previous_log, top_active


async def post_daily_report(bot: commands.Bot, guild_id: int, target_date: datetime.date | None = None) -> bool:
    """Generates the report, posts a fresh message, and records the day's log.

    Returns True on success. Prunes the config if the target channel is gone.
    """
    guild = bot.get_guild(guild_id)
    if guild is None:
        return False

    row = await bot.database.fetch_one(
        "SELECT * FROM daily_growth_reports WHERE guild_id = ?", (guild_id,)
    )
    if not row:
        return False

    channel = guild.get_channel(row["channel_id"])
    if channel is None:
        try:
            channel = await guild.fetch_channel(row["channel_id"])
        except Exception:
            logger.warning(
                f"Daily Growth: channel {row['channel_id']} missing; removing config."
            )
            await bot.database.execute(
                "DELETE FROM daily_growth_reports WHERE guild_id = ?", (guild_id,)
            )
            return False

    target_date = target_date or _utc_today()

    # Post a placeholder first so the message appears instantly; the engagement
    # scan is slow and rate-limited, so it runs while the placeholder is live.
    try:
        msg = await channel.send(embed=build_placeholder_embed(target_date))
    except Exception as e:
        logger.error(f"Daily Growth: failed to post report in guild {guild_id}: {e}")
        return False

    try:
        new_joiners, previous_log, top_active = await compute_report(bot, guild, target_date)
        embed = build_growth_embed(guild, target_date, new_joiners, previous_log, top_active)
        await msg.edit(embed=embed)

        await record_log(
            bot,
            guild_id,
            target_date,
            len(new_joiners),
            [{"id": uid, **data} for uid, data in top_active],
        )
        await bot.database.execute(
            "UPDATE daily_growth_reports SET last_message_id = ?, updated_at = datetime('now') "
            "WHERE guild_id = ?",
            (msg.id, guild_id),
        )
        logger.info(f"Daily Growth: posted report for {target_date} in guild {guild_id}.")
        return True
    except Exception as e:
        logger.error(f"Daily Growth: failed to build/edit report in guild {guild_id}: {e}")
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

class DailyGrowthHubView(discord.ui.View):
    """The Daily Growth control hub, opened from the Mod Panel."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def _refresh_hub(self, interaction: discord.Interaction) -> None:
        """Re-renders the hub message (overview embed + reset components)."""
        try:
            embed = await build_hub_embed(interaction.client, interaction.guild)
            await interaction.message.edit(content=None, embed=embed, view=DailyGrowthHubView())
        except Exception as e:
            logger.error(f"Daily Growth: failed to refresh hub: {e}")

    # ── Row 0: Private preview ──
    @discord.ui.button(label="Preview Today", emoji="👀", style=discord.ButtonStyle.primary, row=0, custom_id="im8_growth_preview")
    async def btn_preview(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        today = _utc_today()
        new_joiners, previous_log, top_active = await compute_report(
            interaction.client, interaction.guild, today
        )
        embed = build_growth_embed(interaction.guild, today, new_joiners, previous_log, top_active)
        await interaction.followup.send(
            content="📊 **Daily Growth Report** preview *(only you can see this — not yet recorded).*",
            embed=embed,
            ephemeral=True,
        )

    # ── Row 1: Choose the destination channel ──
    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="📢 Set daily report channel → pick a channel", min_values=1, max_values=1, row=1, custom_id="im8_growth_set_channel")
    async def select_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        app_channel = select.values[0]
        target = interaction.guild.get_channel(app_channel.id)
        if target is None:
            try:
                target = await interaction.guild.fetch_channel(app_channel.id)
            except Exception as e:
                return await interaction.response.send_message(f"❌ Could not resolve channel: {e}", ephemeral=True)

        await interaction.client.database.execute(
            "INSERT INTO daily_growth_reports (guild_id, channel_id, updated_at) "
            "VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id, "
            "updated_at = datetime('now')",
            (interaction.guild.id, target.id),
        )
        await interaction.response.send_message(
            f"✅ Daily Growth Reports will post to {target.mention} every day at **23:55 UTC**.\n"
            f"Use **Send Now** to post one immediately.",
            ephemeral=True,
        )
        await self._refresh_hub(interaction)

    # ── Row 2: Send now / stop ──
    @discord.ui.button(label="Send Now", emoji="📨", style=discord.ButtonStyle.success, row=2, custom_id="im8_growth_send")
    async def btn_send(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = await interaction.client.database.fetch_one(
            "SELECT * FROM daily_growth_reports WHERE guild_id = ?", (interaction.guild.id,)
        )
        if not row:
            return await interaction.response.send_message(
                "❌ No channel configured yet. Pick one with the dropdown first.", ephemeral=True
            )

        # Run in the background — the engagement scan is slow and rate-limited.
        _spawn(post_daily_report(interaction.client, interaction.guild.id))
        await interaction.response.send_message(
            f"📨 Generating today's report now — it will appear in <#{row['channel_id']}> "
            "in a few moments.",
            ephemeral=True,
        )

    @discord.ui.button(label="Stop Daily Reports", emoji="🗑️", style=discord.ButtonStyle.danger, row=2, custom_id="im8_growth_stop")
    async def btn_stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = await interaction.client.database.fetch_one(
            "SELECT * FROM daily_growth_reports WHERE guild_id = ?", (interaction.guild.id,)
        )
        if not row:
            return await interaction.response.send_message(
                "❌ Daily reports are not configured.", ephemeral=True
            )
        await interaction.client.database.execute(
            "DELETE FROM daily_growth_reports WHERE guild_id = ?", (interaction.guild.id,)
        )
        await interaction.response.send_message(
            "🗑️ Automatic daily reports disabled. Previously posted reports were left intact.",
            ephemeral=True,
        )
        await self._refresh_hub(interaction)

    # ── Row 3: Navigation ──
    @discord.ui.button(label="Back to Main Panel", emoji="🔙", style=discord.ButtonStyle.secondary, row=3, custom_id="im8_growth_back")
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.panel import ModPanelView, Panel
        embed = await Panel.build_panel_embed(interaction.client, interaction.guild)
        await interaction.response.edit_message(content=None, embed=embed, view=ModPanelView())


# ═══════════════════════════════════════════════
#  Cog
# ═══════════════════════════════════════════════

class DailyGrowthCog(commands.Cog):
    """Generates and posts the daily growth report."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # Persistent hub view for restart resilience.
        self.bot.add_view(DailyGrowthHubView())

        # Automatic daily report just before midnight UTC.
        self.bot.scheduler.add_cron_job(
            self.post_all_reports,
            job_id="daily_growth_report",
            cron_expression=DAILY_CRON,
        )
        logger.info("Daily Growth module loaded; daily report job scheduled.")

    async def post_all_reports(self) -> None:
        """Posts the daily report for every configured guild."""
        try:
            rows = await self.bot.database.fetch_all("SELECT * FROM daily_growth_reports")
        except Exception as e:
            logger.error(f"Daily Growth: failed to load report configs: {e}")
            return
        for row in rows:
            try:
                await post_daily_report(self.bot, row["guild_id"])
            except Exception as e:
                logger.error(f"Daily Growth: report failed for guild {row['guild_id']}: {e}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DailyGrowthCog(bot))
