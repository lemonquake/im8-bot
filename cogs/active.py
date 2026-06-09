"""
IM8 Bot — Most Active Module
Detects the most engaged members across the community channels using a
weighted point system, and maintains a public, auto-refreshing leaderboard.
"""

import discord
from discord.ext import commands
import logging
import re
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

# Delay between reaction-enumeration API calls to stay under Discord's
# per-route rate limit (counting reactions GIVEN requires one call per reaction).
REACTION_FETCH_DELAY = 0.25

TIMEFRAME_DAYS = {"weekly": 7, "monthly": 30}

# Auto-refresh cadence for the public leaderboard.
REFRESH_HOURS = 3

# How many members to surface on the leaderboard.
LEADERBOARD_SIZE = 10

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


async def compute_engagement(guild: discord.Guild, days: int) -> list[tuple[int, dict]]:
    """Scans the tracked channels and returns the top engaged members.

    Returns a list of ``(user_id, {"points", "messages", "reactions"})`` tuples,
    sorted by points descending and limited to ``LEADERBOARD_SIZE``.
    Members with an excluded staff role are skipped entirely.
    """
    cutoff = discord.utils.utcnow() - datetime.timedelta(days=days)
    scores: dict[int, dict] = defaultdict(lambda: {"points": 0, "messages": 0, "reactions": 0})

    # Resolve excluded members once so reaction enumeration can skip them cheaply.
    excluded_ids: set[int] = set()

    def excluded(user_id: int) -> bool:
        if user_id in excluded_ids:
            return True
        if _is_excluded(guild, user_id):
            excluded_ids.add(user_id)
            return True
        return False

    for ch_id in TRACKED_CHANNELS:
        if ch_id == IGNORED_CHANNEL_ID:
            continue
        channel = guild.get_channel(ch_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(ch_id)
            except Exception:
                logger.warning(f"Most Active: could not resolve channel {ch_id}.")
                continue

        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            continue

        try:
            async for msg in channel.history(limit=None, after=cutoff):
                # Skip any message/reactions from the ignored channel or its threads
                if msg.channel.id == IGNORED_CHANNEL_ID or getattr(msg.channel, "parent_id", None) == IGNORED_CHANNEL_ID:
                    continue

                # ── Messages ──
                if (
                    not msg.author.bot
                    and is_meaningful(msg.content)
                    and not excluded(msg.author.id)
                ):
                    s = scores[msg.author.id]
                    s["points"] += POINTS_PER_MESSAGE
                    s["messages"] += 1

                # ── Reactions (attributed to the reacting member) ──
                for reaction in msg.reactions:
                    try:
                        async for user in reaction.users():
                            if user.bot or excluded(user.id):
                                continue
                            s = scores[user.id]
                            s["points"] += POINTS_PER_REACTION
                            s["reactions"] += 1
                        # Throttle between reaction routes to avoid 429 rate limits.
                        await asyncio.sleep(REACTION_FETCH_DELAY)
                    except Exception:
                        # A single reaction failing to enumerate must not abort the scan.
                        continue
        except discord.Forbidden:
            logger.warning(f"Most Active: missing read access to channel {ch_id}.")
        except Exception as e:
            logger.error(f"Most Active: error scanning channel {ch_id}: {e}")

    ranked = sorted(scores.items(), key=lambda kv: kv[1]["points"], reverse=True)
    return ranked[:LEADERBOARD_SIZE]


def build_placeholder_embed(timeframe: str) -> discord.Embed:
    """A 'calculating' placeholder shown the instant a board is posted."""
    label = "Last 7 Days" if timeframe == "weekly" else "Last 30 Days"
    embed = discord.Embed(
        title="🏆 IM8 Active Leaderboard",
        description=(
            f"**{label}**\n\n"
            "⏳ Calculating rankings across the community channels…\n"
            "This message will update automatically in a few minutes."
        ),
        color=0xF1C40F,
    )
    embed.set_footer(text="IM8 Health • Most Active")
    return embed


def build_leaderboard_embed(guild: discord.Guild, ranked: list[tuple[int, dict]], timeframe: str) -> discord.Embed:
    """Builds the public-facing leaderboard embed."""
    label = "Last 7 Days" if timeframe == "weekly" else "Last 30 Days"

    embed = discord.Embed(
        title="🏆 IM8 Active Leaderboard",
        description=(
            f"The **{LEADERBOARD_SIZE}** most engaged members • **{label}**\n"
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

    embed.set_footer(text=f"Auto-refreshes every {REFRESH_HOURS}h • Last updated")
    embed.timestamp = discord.utils.utcnow()
    return embed


def _fmt_channels(guild: discord.Guild) -> str:
    """Renders the tracked-channel list as mentions."""
    parts = []
    for cid in TRACKED_CHANNELS:
        ch = guild.get_channel(cid)
        parts.append(ch.mention if ch else f"<#{cid}>")
    return " ".join(parts)


def _parse_sql_ts(ts: str) -> int | None:
    """Parses an SQLite ``datetime('now')`` UTC string into a unix timestamp."""
    try:
        dt = datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


async def build_hub_embed(bot: commands.Bot, guild: discord.Guild) -> discord.Embed:
    """Builds the detailed control-hub overview embed."""
    embed = discord.Embed(
        title="📊 Most Active • Engagement Tracking",
        description=(
            "Ranks the most engaged members across the community channels using a "
            "weighted point system. Low-effort messages (e.g. `ok`, `hi`, emoji-only) "
            "are filtered out so points reflect real participation."
        ),
        color=0x00C9A7,
    )

    embed.add_field(
        name="📡 Tracked Channels",
        value=_fmt_channels(guild),
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

    # Current public leaderboard status.
    row = await bot.database.fetch_one(
        "SELECT * FROM active_leaderboard WHERE guild_id = ?", (guild.id,)
    )
    if row:
        ch = guild.get_channel(row["channel_id"])
        ch_str = ch.mention if ch else f"<#{row['channel_id']}>"
        ts = _parse_sql_ts(row["updated_at"]) if row["updated_at"] else None
        when = f"<t:{ts}:R>" if ts else "unknown"
        status = (
            f"🟢 **Live** in {ch_str}\n"
            f"Timeframe: **{row['timeframe'].title()}**  •  Updated: {when}\n"
            f"Auto-refreshes every **{REFRESH_HOURS}h** and on every bot restart."
        )
    else:
        status = "⚪ **Not deployed.** Use a *Post* dropdown below to publish one."
    embed.add_field(name="🏆 Public Leaderboard", value=status, inline=False)

    embed.add_field(
        name="🛠️ Available Actions",
        value=(
            "**Detect** — preview current rankings privately (weekly / monthly).\n"
            "**Post** — publish a public, auto-refreshing leaderboard to a channel.\n"
            "**Refresh Now** — force an immediate recalculation of the live board.\n"
            "**Remove** — stop auto-refreshing the live board."
        ),
        inline=False,
    )

    embed.set_footer(text="IM8 Health • Most Active")
    return embed


# ═══════════════════════════════════════════════
#  Refresh logic (shared by scheduler + UI)
# ═══════════════════════════════════════════════

async def refresh_one_leaderboard(bot: commands.Bot, row) -> bool:
    """Refreshes a single stored leaderboard. Returns True on success.

    Prunes the config if the channel or message no longer exists.
    """
    guild = bot.get_guild(row["guild_id"])
    if guild is None:
        return False

    channel = guild.get_channel(row["channel_id"])
    if channel is None:
        try:
            channel = await guild.fetch_channel(row["channel_id"])
        except Exception:
            logger.warning(f"Most Active: leaderboard channel {row['channel_id']} missing; removing config.")
            await bot.database.execute("DELETE FROM active_leaderboard WHERE guild_id = ?", (row["guild_id"],))
            return False

    try:
        message = await channel.fetch_message(row["message_id"])
    except discord.NotFound:
        logger.warning(f"Most Active: leaderboard message {row['message_id']} deleted; removing config.")
        await bot.database.execute("DELETE FROM active_leaderboard WHERE guild_id = ?", (row["guild_id"],))
        return False
    except Exception as e:
        logger.error(f"Most Active: could not fetch leaderboard message: {e}")
        return False

    timeframe = row["timeframe"] if row["timeframe"] in TIMEFRAME_DAYS else "monthly"
    ranked = await compute_engagement(guild, TIMEFRAME_DAYS[timeframe])
    embed = build_leaderboard_embed(guild, ranked, timeframe)

    try:
        await message.edit(embed=embed)
        await bot.database.execute(
            "UPDATE active_leaderboard SET updated_at = datetime('now') WHERE guild_id = ?",
            (row["guild_id"],),
        )
        logger.info(f"Most Active: refreshed {timeframe} leaderboard in guild {guild.id}.")
        return True
    except discord.NotFound:
        # Message was deleted between fetch and edit — prune the stale config.
        logger.warning(f"Most Active: leaderboard message {row['message_id']} vanished; removing config.")
        await bot.database.execute("DELETE FROM active_leaderboard WHERE guild_id = ?", (row["guild_id"],))
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

    # ── Row 0: Detect (private preview) ──
    async def _detect(self, interaction: discord.Interaction, timeframe: str) -> None:
        await interaction.response.defer(ephemeral=True)
        ranked = await compute_engagement(interaction.guild, TIMEFRAME_DAYS[timeframe])
        embed = build_leaderboard_embed(interaction.guild, ranked, timeframe)
        await interaction.followup.send(
            content=(
                f"📊 **{timeframe.title()} engagement** scanned across "
                f"{len(TRACKED_CHANNELS)} channels. *(Preview — only you can see this.)*"
            ),
            embed=embed,
            ephemeral=True,
        )

    @discord.ui.button(label="Detect Weekly", emoji="📆", style=discord.ButtonStyle.primary, row=0, custom_id="im8_active_weekly")
    async def btn_weekly(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._detect(interaction, "weekly")

    @discord.ui.button(label="Detect Monthly", emoji="🗓️", style=discord.ButtonStyle.primary, row=0, custom_id="im8_active_monthly")
    async def btn_monthly(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._detect(interaction, "monthly")

    # ── Rows 1 & 2: Post / update the public board ──
    async def _deploy(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect, timeframe: str) -> None:
        app_channel = select.values[0]
        target = interaction.guild.get_channel(app_channel.id)
        if target is None:
            try:
                target = await interaction.guild.fetch_channel(app_channel.id)
            except Exception as e:
                return await interaction.response.send_message(f"❌ Could not resolve channel: {e}", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        # Post immediately with a placeholder so the message appears right away,
        # then calculate rankings in the background (the scan can take minutes).
        try:
            msg = await target.send(embed=build_placeholder_embed(timeframe))
        except Exception as e:
            return await interaction.followup.send(f"❌ Failed to post leaderboard to {target.mention}: {e}", ephemeral=True)

        await interaction.client.database.execute(
            "INSERT OR REPLACE INTO active_leaderboard (guild_id, channel_id, message_id, timeframe, updated_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (interaction.guild.id, target.id, msg.id, timeframe),
        )

        # Kick off the ranking calculation without blocking the interaction.
        _spawn(refresh_one_leaderboard(interaction.client, {
            "guild_id": interaction.guild.id,
            "channel_id": target.id,
            "message_id": msg.id,
            "timeframe": timeframe,
        }))

        await interaction.followup.send(
            f"✅ **{timeframe.title()} Active Leaderboard** posted in {target.mention} → {msg.jump_url}\n"
            f"⏳ Rankings are calculating now and will fill in shortly.\n"
            f"It will auto-refresh every **{REFRESH_HOURS} hours** and on every bot restart.",
            ephemeral=True,
        )
        await self._refresh_hub(interaction)

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="📢 Post WEEKLY leaderboard → pick a channel", min_values=1, max_values=1, row=1, custom_id="im8_active_post_weekly")
    async def select_post_weekly(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        await self._deploy(interaction, select, "weekly")

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="📢 Post MONTHLY leaderboard → pick a channel", min_values=1, max_values=1, row=2, custom_id="im8_active_post_monthly")
    async def select_post_monthly(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        await self._deploy(interaction, select, "monthly")

    # ── Row 3: Manage the live board ──
    @discord.ui.button(label="Refresh Now", emoji="🔄", style=discord.ButtonStyle.success, row=3, custom_id="im8_active_refresh")
    async def btn_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = await interaction.client.database.fetch_one(
            "SELECT * FROM active_leaderboard WHERE guild_id = ?", (interaction.guild.id,)
        )
        if not row:
            return await interaction.response.send_message(
                "❌ No public leaderboard is deployed yet. Use a *Post* dropdown first.", ephemeral=True
            )

        # Run the (slow, rate-limited) scan in the background so the click
        # responds instantly instead of timing out the interaction.
        _spawn(refresh_one_leaderboard(interaction.client, row))
        await interaction.response.send_message(
            "🔄 Recalculation started — the live leaderboard will update in a few minutes.",
            ephemeral=True,
        )

    @discord.ui.button(label="Remove Leaderboard", emoji="🗑️", style=discord.ButtonStyle.danger, row=3, custom_id="im8_active_remove")
    async def btn_remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = await interaction.client.database.fetch_one(
            "SELECT * FROM active_leaderboard WHERE guild_id = ?", (interaction.guild.id,)
        )
        if not row:
            return await interaction.response.send_message(
                "❌ There is no active leaderboard to remove.", ephemeral=True
            )

        await interaction.client.database.execute(
            "DELETE FROM active_leaderboard WHERE guild_id = ?", (interaction.guild.id,)
        )
        await interaction.response.send_message(
            "🗑️ Auto-refresh stopped. The posted message was left intact — delete it manually if you wish.",
            ephemeral=True,
        )
        await self._refresh_hub(interaction)

    # ── Row 4: Navigation ──
    @discord.ui.button(label="Back to Main Panel", emoji="🔙", style=discord.ButtonStyle.secondary, row=4, custom_id="im8_active_back")
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.panel import ModPanelView, Panel
        embed = await Panel.build_panel_embed(interaction.client, interaction.guild)
        await interaction.response.edit_message(content=None, embed=embed, view=ModPanelView())


# ═══════════════════════════════════════════════
#  Cog
# ═══════════════════════════════════════════════

class ActiveCog(commands.Cog):
    """Tracks and surfaces the most engaged members of the community."""

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
        logger.info("Most Active module loaded; leaderboard refresh scheduled.")

    async def cog_unload(self) -> None:
        if self._startup_task and not self._startup_task.done():
            self._startup_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Refresh once on every bot run (guarded so reconnects don't re-trigger).
        # Runs in the BACKGROUND so the engagement scan (which is slow and
        # rate-limited) never blocks startup or makes the bot look frozen.
        if self._startup_refreshed:
            return
        self._startup_refreshed = True
        self._startup_task = asyncio.create_task(self._delayed_startup_refresh())

    async def _delayed_startup_refresh(self) -> None:
        # Let the gateway settle and the member cache fill before scanning.
        await asyncio.sleep(15)
        logger.info("Most Active: starting background startup leaderboard refresh...")
        try:
            await self.refresh_all_leaderboards()
            logger.info("Most Active: background startup refresh complete.")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Most Active: background startup refresh failed: {e}")

    async def refresh_all_leaderboards(self) -> None:
        """Re-scans engagement and updates every configured public leaderboard."""
        try:
            rows = await self.bot.database.fetch_all("SELECT * FROM active_leaderboard")
        except Exception as e:
            logger.error(f"Most Active: failed to load leaderboard configs: {e}")
            return

        for row in rows:
            await refresh_one_leaderboard(self.bot, row)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ActiveCog(bot))
