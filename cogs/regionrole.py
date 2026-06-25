"""
IM8 Bot — Region Role Module
Posts a self-assignable "Region Role" message: members react with the emoji
for their part of the world to receive the matching role. Enforces a single
selection per member (only one reaction / one region role at a time) and strips
any reaction that isn't one of the offered regions. The same message can be
posted to as many channels as desired — every copy is tracked and works.
"""

import discord
from discord.ext import commands
import logging
import asyncio
from collections import defaultdict

import config

logger = logging.getLogger("im8bot.cogs.regionrole")

# ═══════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════

# The offered regions, in display/reaction order. Each maps a continent emoji
# to the role granted when a member reacts with it.
REGIONS: list[dict] = [
    {"name": "Europe",         "emoji": "🏰", "role_id": 1513933110155415593},
    {"name": "North America",  "emoji": "🗽", "role_id": 1513933383741477065},
    {"name": "Asia",           "emoji": "🏮", "role_id": 1513933223669928209},
    {"name": "United Kingdom", "emoji": "🫖", "role_id": 1513933436065419274},
    {"name": "Middle East",    "emoji": "🐪", "role_id": 1513933508840521798},
    {"name": "Oceania",        "emoji": "🦘", "role_id": 1514766433525956809},
]

# Fast lookups derived from REGIONS.
EMOJI_TO_ROLE: dict[str, int] = {r["emoji"]: r["role_id"] for r in REGIONS}
REGION_EMOJIS: list[str] = [r["emoji"] for r in REGIONS]
REGION_ROLE_IDS: list[int] = [r["role_id"] for r in REGIONS]
ROLE_ID_TO_REGION: dict[int, dict] = {r["role_id"]: r for r in REGIONS}

# Delay between reaction-enumeration API calls during reconciliation to stay
# under Discord's per-route rate limit (one call per reaction listing).
REACTION_FETCH_DELAY = 0.25


# ═══════════════════════════════════════════════
#  Embed rendering
# ═══════════════════════════════════════════════

def build_region_embed(guild: discord.Guild) -> discord.Embed:
    """The public-facing Region Role message members react to."""
    lines = []
    for r in REGIONS:
        role = guild.get_role(r["role_id"])
        target = role.mention if role else f"**{r['name']}**"
        lines.append(f"{r['emoji']}  →  {target}")

    embed = discord.Embed(
        title="🌍 Select Your Region",
        description=(
            "React with the emoji for the part of the world you're in to receive "
            "your region role.\n\n"
            "• You can only pick **one** region — reacting with a different one "
            "switches you over automatically.\n"
            "• Remove your reaction to clear your region role.\n"
            "• Any other reactions are removed automatically.\n\n"
            + "\n".join(lines)
        ),
        color=config.COLOR_BRAND,
    )
    embed.set_footer(text="IM8 Health • Region Roles")
    return embed


async def build_hub_embed(bot: commands.Bot, guild: discord.Guild) -> discord.Embed:
    """The detailed control-hub overview embed for the Mod Panel."""
    embed = discord.Embed(
        title="🌍 Region Role • Self-Assign by Continent",
        description=(
            "Posts a message members react to in order to pick their region. "
            "Only **one** region per member is allowed (switching regions moves "
            "their role automatically), and reactions outside the offered set are "
            "removed. The post can be published to **multiple channels** — every "
            "copy stays fully functional."
        ),
        color=config.COLOR_BRAND,
    )

    region_lines = []
    for r in REGIONS:
        role = guild.get_role(r["role_id"])
        target = role.mention if role else f"`{r['role_id']}` *(missing)*"
        region_lines.append(f"{r['emoji']}  →  {target}")
    embed.add_field(name="🗺️ Regions", value="\n".join(region_lines), inline=False)

    # Current member-per-role distribution so the operator sees live numbers.
    counts = region_role_counts(guild)
    assigned = sum(counts.values())
    dist_lines = ["Region            │ Members", "──────────────────┼────────"]
    for r in REGIONS:
        dist_lines.append(f"{r['emoji']} {r['name']:<14} │ {counts.get(r['role_id'], 0):>5}")
    embed.add_field(
        name="👥 Member Distribution",
        value=(
            "```\n" + "\n".join(dist_lines) + "\n```"
            f"**{assigned}** member(s) hold a region role.\n"
            "*Roles auto-sync on every bot restart — reactions left while the bot "
            "was offline are caught and granted automatically.*"
        ),
        inline=False,
    )

    rows = await bot.database.fetch_all(
        "SELECT * FROM region_role_messages WHERE guild_id = ?", (guild.id,)
    )
    if rows:
        links = []
        for row in rows:
            ch = guild.get_channel(row["channel_id"])
            ch_str = ch.mention if ch else f"<#{row['channel_id']}>"
            jump = f"https://discord.com/channels/{guild.id}/{row['channel_id']}/{row['message_id']}"
            links.append(f"• {ch_str} → [jump]({jump})")

        # Discord caps a field value at 1024 chars. Each jump link is ~120 chars,
        # so show as many as fit, leaving headroom for the header and trailer.
        header = f"🟢 **{len(rows)}** active post(s):\n"
        shown: list[str] = []
        used = len(header)
        for link in links:
            if used + len(link) + 1 > 1024 - 40:  # 40-char buffer for trailer
                break
            shown.append(link)
            used += len(link) + 1
        status = header + "\n".join(shown)
        if len(shown) < len(rows):
            status += f"\n*…and {len(rows) - len(shown)} more.*"
    else:
        status = "⚪ **No posts yet.** Pick a channel below to publish one."
    embed.add_field(name="📰 Active Posts", value=status, inline=False)

    embed.add_field(
        name="🛠️ Available Actions",
        value=(
            "**Post Region Role** — publish the reaction message to a channel "
            "(tags `@everyone`). Repeat for as many channels as you like.\n"
            "**Sync Reactions Now** — re-scan every post, grant roles for any "
            "reactions made while the bot was offline, and clear stray reactions.\n"
            "**Post Distribution Report** — publish the member-per-region counts "
            "to a channel of your choice.\n"
            "**Remove All Posts** — stop tracking every post (messages are left intact)."
        ),
        inline=False,
    )

    embed.set_footer(text="IM8 Health • Region Roles")
    return embed


# ═══════════════════════════════════════════════
#  Reconciliation & distribution
# ═══════════════════════════════════════════════

def region_role_counts(guild: discord.Guild) -> dict[int, int]:
    """Returns ``{role_id: member_count}`` for every offered region role."""
    counts: dict[int, int] = {}
    for rid in REGION_ROLE_IDS:
        role = guild.get_role(rid)
        counts[rid] = len(role.members) if role else 0
    return counts


def build_distribution_embed(guild: discord.Guild) -> discord.Embed:
    """Public report of how many members currently hold each region role."""
    counts = region_role_counts(guild)
    assigned = sum(counts.values())

    embed = discord.Embed(
        title="🌍 Region Role Distribution",
        description=(
            f"A breakdown of where the **{guild.name}** community is based, by "
            "self-assigned region role."
        ),
        color=config.COLOR_BRAND,
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    lines = ["Region            │ Members │  Share", "──────────────────┼─────────┼───────"]
    for r in REGIONS:
        c = counts.get(r["role_id"], 0)
        pct = (c / assigned * 100) if assigned else 0
        lines.append(f"{r['emoji']} {r['name']:<14} │ {c:>7} │ {pct:>5.1f}%")
    embed.add_field(
        name="👥 Members by Region",
        value="```\n" + "\n".join(lines) + "\n```",
        inline=False,
    )
    embed.add_field(
        name="📊 Total Assigned",
        value=f"**{assigned}** member(s) hold a region role.",
        inline=False,
    )

    embed.set_footer(text="IM8 Health • Region Roles")
    embed.timestamp = discord.utils.utcnow()
    return embed


async def reconcile_guild(bot: commands.Bot, guild: discord.Guild) -> dict:
    """Reconciles region roles against the reactions on every tracked post.

    For each tracked Region Role message this:
      • grants the matching role to anyone who reacted (catching reactions made
        while the bot was offline),
      • enforces a single region per member (extra region reactions/roles are
        stripped, keeping the first region in display order they picked),
      • clears any stray reaction that isn't one of the offered regions.

    Returns a stats dict: ``added``, ``switched``, ``stray_cleared``,
    ``messages_scanned``, ``reactors``, ``counts`` (role_id → member count),
    and ``pruned`` (tracked rows removed because the message was gone).
    """
    rows = await bot.database.fetch_all(
        "SELECT * FROM region_role_messages WHERE guild_id = ?", (guild.id,)
    )

    member_emojis: dict[int, set[str]] = defaultdict(set)
    # message_id → {emoji: {user_id}} for region emojis (for targeted cleanup).
    per_msg_region: dict[int, dict[str, set[int]]] = {}
    msg_objs: dict[int, discord.Message] = {}
    scanned = 0
    stray_cleared = 0
    pruned = 0

    for row in rows:
        channel = guild.get_channel(row["channel_id"])
        if channel is None:
            try:
                channel = await guild.fetch_channel(row["channel_id"])
            except Exception:
                continue

        try:
            message = await channel.fetch_message(row["message_id"])
        except discord.NotFound:
            # The post was deleted — stop tracking it.
            await bot.database.execute(
                "DELETE FROM region_role_messages WHERE message_id = ?",
                (row["message_id"],),
            )
            pruned += 1
            continue
        except Exception as e:
            logger.warning(f"Region Role: could not fetch tracked message {row['message_id']}: {e}")
            continue

        msg_objs[message.id] = message
        per_msg_region[message.id] = defaultdict(set)
        scanned += 1

        for reaction in message.reactions:
            emoji = str(reaction.emoji)

            # Stray reaction (not an offered region) — clear it entirely.
            if emoji not in EMOJI_TO_ROLE:
                try:
                    await message.clear_reaction(reaction.emoji)
                    stray_cleared += 1
                except Exception:
                    pass
                continue

            try:
                async for user in reaction.users():
                    if user.bot:
                        continue
                    member_emojis[user.id].add(emoji)
                    per_msg_region[message.id][emoji].add(user.id)
            except Exception:
                # A single reaction failing to enumerate must not abort the scan.
                continue
            # Throttle between reaction routes to avoid 429 rate limits.
            await asyncio.sleep(REACTION_FETCH_DELAY)

    added = 0
    switched = 0

    # No message could be read — return early without touching roles so a
    # transient fetch failure never strips anything.
    if scanned == 0:
        counts = region_role_counts(guild)
        return {
            "added": 0, "switched": 0, "stray_cleared": stray_cleared,
            "messages_scanned": 0, "reactors": 0, "counts": counts, "pruned": pruned,
        }

    for user_id, emojis in member_emojis.items():
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                continue
        if member.bot:
            continue

        # Canonical pick: first region in display order the member reacted with.
        canonical_emoji = next((e for e in REGION_EMOJIS if e in emojis), None)
        if canonical_emoji is None:
            continue
        target_role = guild.get_role(EMOJI_TO_ROLE[canonical_emoji])
        if target_role is None:
            continue
        if target_role >= guild.me.top_role:
            logger.warning(f"Region Role: cannot assign {target_role.name} (above my top role).")
            continue

        # Enforce single selection on the messages: remove this member's
        # reactions for every non-canonical region they also reacted with.
        if len(emojis) > 1:
            for mid, emap in per_msg_region.items():
                for e, uids in emap.items():
                    if e != canonical_emoji and user_id in uids:
                        try:
                            await msg_objs[mid].remove_reaction(e, member)
                        except Exception:
                            pass

        # Enforce a single region role: drop any other region role they hold.
        to_remove = [
            guild.get_role(rid) for rid in REGION_ROLE_IDS if rid != target_role.id
        ]
        to_remove = [r for r in to_remove if r is not None and r in member.roles]
        if to_remove:
            try:
                await member.remove_roles(*to_remove, reason="Region Role: reconcile (single region)")
                switched += 1
            except Exception as e:
                logger.error(f"Region Role: reconcile failed to remove old roles for {member}: {e}")

        if target_role not in member.roles:
            try:
                await member.add_roles(target_role, reason="Region Role: reconcile (offline reaction)")
                added += 1
            except Exception as e:
                logger.error(f"Region Role: reconcile failed to add {target_role.name} for {member}: {e}")

    counts = region_role_counts(guild)
    logger.info(
        f"Region Role: reconciled guild {guild.id} — scanned {scanned} post(s), "
        f"{len(member_emojis)} reactor(s), +{added} role(s), {switched} switched, "
        f"{stray_cleared} stray reaction(s) cleared, {pruned} stale post(s) pruned."
    )
    return {
        "added": added, "switched": switched, "stray_cleared": stray_cleared,
        "messages_scanned": scanned, "reactors": len(member_emojis),
        "counts": counts, "pruned": pruned,
    }


# ═══════════════════════════════════════════════
#  UI
# ═══════════════════════════════════════════════

class RegionRoleHubView(discord.ui.View):
    """The Region Role control hub, opened from the Mod Panel."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def _refresh_hub(self, interaction: discord.Interaction) -> None:
        """Re-renders the hub message (overview embed + reset components)."""
        try:
            embed = await build_hub_embed(interaction.client, interaction.guild)
            await interaction.message.edit(content=None, embed=embed, view=RegionRoleHubView())
        except Exception as e:
            logger.error(f"Region Role: failed to refresh hub: {e}")

    # ── Row 0: Post the reaction message to a channel ──
    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="📢 Post Region Role message → pick a channel",
        min_values=1,
        max_values=1,
        row=0,
        custom_id="im8_region_post",
    )
    async def select_post(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        app_channel = select.values[0]
        target = interaction.guild.get_channel(app_channel.id)
        if target is None:
            try:
                target = await interaction.guild.fetch_channel(app_channel.id)
            except Exception as e:
                return await interaction.response.send_message(f"❌ Could not resolve channel: {e}", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        embed = build_region_embed(interaction.guild)
        try:
            msg = await target.send(
                content="@everyone",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(everyone=True),
            )
        except Exception as e:
            return await interaction.followup.send(
                f"❌ Failed to post to {target.mention}: {e}", ephemeral=True
            )

        # Track the message BEFORE reacting so the listener recognises it.
        await interaction.client.database.execute(
            "INSERT OR REPLACE INTO region_role_messages (message_id, guild_id, channel_id, created_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (msg.id, interaction.guild.id, target.id),
        )
        cog = interaction.client.get_cog("RegionRoleCog")
        if cog is not None:
            cog._tracked.add(msg.id)

        # Seed the reactions so members can click them directly.
        for emoji in REGION_EMOJIS:
            try:
                await msg.add_reaction(emoji)
            except Exception as e:
                logger.warning(f"Region Role: could not add reaction {emoji}: {e}")

        await interaction.followup.send(
            f"✅ **Region Role message** posted in {target.mention} → {msg.jump_url}\n"
            f"Members can now react to pick their region. You can post it to more channels too.",
            ephemeral=True,
        )
        await self._refresh_hub(interaction)

    # ── Row 1: Reconcile reactions / stop tracking ──
    @discord.ui.button(label="Sync Reactions Now", emoji="🔄", style=discord.ButtonStyle.success, row=1, custom_id="im8_region_sync")
    async def btn_sync(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = await interaction.client.database.fetch_all(
            "SELECT message_id FROM region_role_messages WHERE guild_id = ?", (interaction.guild.id,)
        )
        if not rows:
            return await interaction.response.send_message(
                "❌ There are no Region Role posts to sync. Post one first.", ephemeral=True
            )

        # The scan enumerates reactions over the network and is rate-limited,
        # so defer first to keep the interaction alive.
        await interaction.response.defer(ephemeral=True)
        stats = await reconcile_guild(interaction.client, interaction.guild)

        counts = stats["counts"]
        assigned = sum(counts.values())
        dist_lines = ["Region            │ Members", "──────────────────┼────────"]
        for r in REGIONS:
            dist_lines.append(f"{r['emoji']} {r['name']:<14} │ {counts.get(r['role_id'], 0):>5}")

        summary = (
            f"✅ **Reaction sync complete** — scanned **{stats['messages_scanned']}** post(s), "
            f"**{stats['reactors']}** reactor(s).\n"
            f"➕ Roles granted: **{stats['added']}**  •  🔁 Switched to single region: "
            f"**{stats['switched']}**  •  🧹 Stray reactions cleared: **{stats['stray_cleared']}**\n\n"
            "**👥 Current member distribution:**\n"
            "```\n" + "\n".join(dist_lines) + "\n```"
            f"**{assigned}** member(s) hold a region role."
        )

        # Ask whether to publish this as a public report (channel selectable).
        await interaction.followup.send(
            content=summary + "\n\n📤 **Post this as a report?** Pick a channel below, or dismiss this message to skip.",
            view=ReportPromptView(),
            ephemeral=True,
        )
        await self._refresh_hub(interaction)

    @discord.ui.button(label="Remove All Posts", emoji="🗑️", style=discord.ButtonStyle.danger, row=1, custom_id="im8_region_remove")
    async def btn_remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = await interaction.client.database.fetch_all(
            "SELECT message_id FROM region_role_messages WHERE guild_id = ?", (interaction.guild.id,)
        )
        if not rows:
            return await interaction.response.send_message(
                "❌ There are no Region Role posts to remove.", ephemeral=True
            )

        await interaction.client.database.execute(
            "DELETE FROM region_role_messages WHERE guild_id = ?", (interaction.guild.id,)
        )
        cog = interaction.client.get_cog("RegionRoleCog")
        if cog is not None:
            for row in rows:
                cog._tracked.discard(row["message_id"])

        await interaction.response.send_message(
            f"🗑️ Stopped tracking {len(rows)} post(s). The messages were left intact — "
            "delete them manually if you wish.",
            ephemeral=True,
        )
        await self._refresh_hub(interaction)

    # ── Row 2: Publish the distribution report ──
    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="📊 Post Distribution Report → pick a channel",
        min_values=1,
        max_values=1,
        row=2,
        custom_id="im8_region_report",
    )
    async def select_report(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        await _post_distribution_report(interaction, select.values[0].id)

    # ── Row 3: Navigation ──
    @discord.ui.button(label="Back to Main Panel", emoji="🔙", style=discord.ButtonStyle.secondary, row=3, custom_id="im8_region_back")
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.panel import ModPanelView, Panel
        embed = await Panel.build_panel_embed(interaction.client, interaction.guild)
        await interaction.response.edit_message(content=None, embed=embed, view=ModPanelView())


async def _post_distribution_report(interaction: discord.Interaction, channel_id: int) -> None:
    """Publishes the region distribution report to ``channel_id``."""
    target = interaction.guild.get_channel(channel_id)
    if target is None:
        try:
            target = await interaction.guild.fetch_channel(channel_id)
        except Exception as e:
            return await interaction.response.send_message(f"❌ Could not resolve channel: {e}", ephemeral=True)

    embed = build_distribution_embed(interaction.guild)
    try:
        msg = await target.send(embed=embed)
    except Exception as e:
        return await interaction.response.send_message(
            f"❌ Failed to post the report to {target.mention}: {e}", ephemeral=True
        )

    await interaction.response.send_message(
        f"✅ **Region Role Distribution** report posted in {target.mention} → {msg.jump_url}",
        ephemeral=True,
    )


class ReportPromptView(discord.ui.View):
    """Ephemeral prompt asking whether to publish the distribution report."""

    def __init__(self) -> None:
        super().__init__(timeout=300)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="📊 Yes — post the report → pick a channel",
        min_values=1,
        max_values=1,
        row=0,
    )
    async def select_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel_id = select.values[0].id
        target = interaction.guild.get_channel(channel_id)
        if target is None:
            try:
                target = await interaction.guild.fetch_channel(channel_id)
            except Exception as e:
                return await interaction.response.send_message(f"❌ Could not resolve channel: {e}", ephemeral=True)

        try:
            msg = await target.send(embed=build_distribution_embed(interaction.guild))
        except Exception as e:
            return await interaction.response.send_message(
                f"❌ Failed to post the report to {target.mention}: {e}", ephemeral=True
            )

        # Disable the prompt and confirm, in-place on the prompt message.
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"✅ **Region Role Distribution** report posted in {target.mention} → {msg.jump_url}",
            view=self,
        )


# ═══════════════════════════════════════════════
#  Cog
# ═══════════════════════════════════════════════

class RegionRoleCog(commands.Cog):
    """Self-assignable region roles driven by message reactions."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # In-memory set of tracked message IDs so the reaction listener can
        # cheaply ignore reactions on unrelated messages.
        self._tracked: set[int] = set()
        self._startup_done = False
        self._startup_task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        # Persistent hub view for restart resilience.
        self.bot.add_view(RegionRoleHubView())

        # Load tracked Region Role messages from the database.
        try:
            rows = await self.bot.database.fetch_all("SELECT message_id FROM region_role_messages")
            self._tracked = {row["message_id"] for row in rows}
        except Exception as e:
            logger.error(f"Region Role: failed to load tracked messages: {e}")
        logger.info(f"Region Role module loaded; tracking {len(self._tracked)} message(s).")

    async def cog_unload(self) -> None:
        if self._startup_task and not self._startup_task.done():
            self._startup_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Reconcile reactions once per run, in the BACKGROUND so the (slow,
        # rate-limited) reaction scan never blocks startup. This is what catches
        # reactions made while the bot — and the PC hosting it — were offline.
        if self._startup_done:
            return
        self._startup_done = True
        self._startup_task = asyncio.create_task(self._delayed_reconcile())

    async def _delayed_reconcile(self) -> None:
        # Let the gateway settle and the member cache fill before reconciling.
        await asyncio.sleep(15)
        logger.info("Region Role: starting background startup reconciliation...")
        try:
            for guild in self.bot.guilds:
                try:
                    await reconcile_guild(self.bot, guild)
                except Exception as e:
                    logger.error(f"Region Role: reconcile failed for guild {guild.id}: {e}")
            logger.info("Region Role: background startup reconciliation complete.")
        except asyncio.CancelledError:
            raise

    # ── Reaction handling ──
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None:
            return
        if payload.message_id not in self._tracked:
            return
        if self.bot.user and payload.user_id == self.bot.user.id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        member = payload.member or guild.get_member(payload.user_id)
        if member is None or member.bot:
            return

        channel = guild.get_channel(payload.channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(payload.channel_id)
            except Exception:
                return

        emoji = str(payload.emoji)

        # Reactions outside the offered regions are not allowed — strip them.
        if emoji not in EMOJI_TO_ROLE:
            try:
                message = await channel.fetch_message(payload.message_id)
                await message.remove_reaction(payload.emoji, member)
            except Exception:
                pass
            return

        target_role = guild.get_role(EMOJI_TO_ROLE[emoji])
        if target_role is None:
            logger.warning(f"Region Role: role {EMOJI_TO_ROLE[emoji]} for {emoji} not found.")
            return
        if target_role >= guild.me.top_role:
            logger.warning(f"Region Role: cannot assign {target_role.name} (above my top role).")
            return

        # Enforce a single reaction per member: clear their reactions for the
        # other regions on this message.
        try:
            message = await channel.fetch_message(payload.message_id)
            for other in REGION_EMOJIS:
                if other != emoji:
                    try:
                        await message.remove_reaction(other, member)
                    except Exception:
                        pass
        except Exception:
            pass

        # Enforce a single region role: drop any other region role they hold.
        to_remove = [
            guild.get_role(rid)
            for rid in REGION_ROLE_IDS
            if rid != target_role.id
        ]
        to_remove = [r for r in to_remove if r is not None and r in member.roles]
        if to_remove:
            try:
                await member.remove_roles(*to_remove, reason="Region Role: switched region")
            except Exception as e:
                logger.error(f"Region Role: failed to remove old region roles: {e}")

        if target_role not in member.roles:
            try:
                await member.add_roles(target_role, reason="Region Role selection")
            except Exception as e:
                logger.error(f"Region Role: failed to add {target_role.name}: {e}")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None:
            return
        if payload.message_id not in self._tracked:
            return
        if self.bot.user and payload.user_id == self.bot.user.id:
            return

        emoji = str(payload.emoji)
        if emoji not in EMOJI_TO_ROLE:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        member = guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except Exception:
                return
        if member.bot:
            return

        role = guild.get_role(EMOJI_TO_ROLE[emoji])
        if role is not None and role in member.roles:
            try:
                await member.remove_roles(role, reason="Region Role: reaction removed")
            except Exception as e:
                logger.error(f"Region Role: failed to remove {role.name}: {e}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RegionRoleCog(bot))
