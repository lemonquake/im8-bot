import discord
from discord.ext import commands
import logging
from collections import defaultdict
import datetime
import asyncio

import config
from utils import embed_builder

logger = logging.getLogger("im8bot.cogs.onboarding")

# Channel mentions render as clickable #channel links inside the guild.
ONBOARDING_CHANNEL_MENTION = f"<#{config.ONBOARDING_CHANNEL_ID}>"
INTRODUCTIONS_CHANNEL_MENTION = f"<#{config.INTRODUCTIONS_CHANNEL_ID}>"

DEFAULT_MESSAGE = (
    "👋 **Please give a warm welcome to our newest members!**\n\n"
    "Welcome to the **IM8 Health** affiliate community — we're thrilled to have you here! 🎉\n\n"
    f"**◈  Start here →** {ONBOARDING_CHANNEL_MENTION}\n"
    "Everything you need to hit the ground running lives there — guides, key resources, "
    "and all the essentials.\n\n"
    f"**◈  Say hello →** {INTRODUCTIONS_CHANNEL_MENTION}\n"
    "Pop in to introduce yourself! Tell us a little about you, drop your social media "
    "handles, and share your @s so the community can connect with you. 💚"
)


def _build_welcome_dm(member: discord.Member) -> discord.Embed:
    """Builds the warm, branded welcome DM that guides a member to #onboarding."""
    guild = member.guild
    # Direct deep-links so each channel is one tap away even from a DM.
    onboarding_url = f"https://discord.com/channels/{guild.id}/{config.ONBOARDING_CHANNEL_ID}"
    intros_url = f"https://discord.com/channels/{guild.id}/{config.INTRODUCTIONS_CHANNEL_ID}"

    embed = embed_builder.base_embed(
        title="👋  Welcome to IM8 Health!",
        description=(
            f"Hi {member.mention}, we're so glad you're here! 🎉\n\n"
            f"You've just joined the **{guild.name}** affiliate community, and we want "
            "to make sure you have the smoothest possible start.\n\n"
            f"**◈  Start here →** [Open the #onboarding channel]({onboarding_url})\n"
            "That's your one-stop hub — guides, key resources, and answers to the most "
            "common questions all live there.\n\n"
            f"**◈  Introduce yourself →** [Head to #introductions]({intros_url})\n"
            "Don't be shy — say hello, tell us a bit about yourself, and feel free to "
            "share your social media handles and @s so the community can connect with you.\n\n"
            "Our team is always just a message away. Welcome aboard! 💚"
        ),
        color=config.COLOR_BRAND,
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    return embed


async def _dm_welcome(members: list[discord.Member]) -> tuple[int, int]:
    """DMs the warm welcome embed to each member. Returns (success, fail) counts."""
    success = 0
    fail = 0
    for m in members:
        try:
            await m.send(embed=_build_welcome_dm(m))
            success += 1
            await asyncio.sleep(1)  # gentle pacing to avoid rate limits
        except Exception:
            fail += 1  # DMs closed or member blocked the bot
    return success, fail


async def _mark_onboarded(db, members: list[discord.Member], guild_id: int) -> None:
    """Records the given members as onboarded in the database."""
    now_str = discord.utils.utcnow().isoformat()
    for m in members:
        try:
            await db.execute(
                "INSERT OR IGNORE INTO onboarded_members (user_id, guild_id, onboarded_at) VALUES (?, ?, ?)",
                (m.id, guild_id, now_str)
            )
        except Exception as e:
            logger.error(f"Failed to insert onboarded member {m.id}: {e}")

class OnboardingChannelSelectView(discord.ui.View):
    def __init__(self, target_members: list[discord.Member], message_content: str, parent_view: discord.ui.View):
        super().__init__(timeout=300)
        self.target_members = target_members
        self.message_content = message_content
        self.parent_view = parent_view
        
    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="Select target channel...", max_values=1, row=0)
    async def select_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        await interaction.response.defer(ephemeral=True)
        selected_channel = select.values[0]
        
        real_channel = interaction.guild.get_channel(selected_channel.id)
        if not real_channel:
            try:
                real_channel = await interaction.guild.fetch_channel(selected_channel.id)
            except Exception:
                await interaction.followup.send("❌ Error: Could not resolve channel.", ephemeral=True)
                return
        
        # Tag members in the public welcome post
        mentions = " ".join([m.mention for m in self.target_members])
        full_msg = f"{mentions}\n\n{self.message_content}"

        # 1. Public post in the chosen channel
        try:
            await real_channel.send(full_msg)
        except Exception as e:
            await interaction.followup.send(f"❌ Error sending message: {e}", ephemeral=True)
            return

        # 2. Warm welcome DM to each tagged member
        dm_success, dm_fail = await _dm_welcome(self.target_members)

        # 3. Record them as onboarded
        await _mark_onboarded(interaction.client.database, self.target_members, interaction.guild.id)

        await interaction.followup.send(
            f"✅ Posted the public welcome in {real_channel.mention} and reached out to new members.\n"
            f"**Welcome DMs delivered:** {dm_success}\n"
            f"**Welcome DMs failed:** {dm_fail} (DMs closed)\n"
            f"Members have been marked as onboarded.",
            ephemeral=True
        )
            
    @discord.ui.button(label="Back", emoji="🔙", style=discord.ButtonStyle.secondary, row=1)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=self.parent_view)

class OnboardingDeliveryView(discord.ui.View):
    def __init__(self, target_members: list[discord.Member], message_content: str):
        super().__init__(timeout=300)
        self.target_members = target_members
        self.message_content = message_content

    @discord.ui.button(label="Post Publicly + DM Welcome", emoji="📣", style=discord.ButtonStyle.primary, row=0)
    async def btn_send_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = OnboardingChannelSelectView(self.target_members, self.message_content, self)
        await interaction.response.edit_message(view=view)

    @discord.ui.button(label="DM Welcome Only", emoji="✉️", style=discord.ButtonStyle.secondary, row=0)
    async def btn_send_dm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        success_count, fail_count = await _dm_welcome(self.target_members)

        await _mark_onboarded(interaction.client.database, self.target_members, interaction.guild.id)
        await interaction.followup.send(
            f"✅ Finished sending warm welcome DMs.\n"
            f"**Delivered:** {success_count}\n"
            f"**Failed:** {fail_count} (DMs might be closed)\n"
            f"Members have been marked as onboarded.",
            ephemeral=True
        )

class OnboardingComposerModal(discord.ui.Modal, title="Compose Onboarding Message"):
    def __init__(self, target_members: list[discord.Member]):
        super().__init__()
        self.target_members = target_members
        
        self.msg_content = discord.ui.TextInput(
            label="Message Content",
            style=discord.TextStyle.paragraph,
            default=DEFAULT_MESSAGE,
            required=True,
            max_length=2000
        )
        self.add_item(self.msg_content)

    async def on_submit(self, interaction: discord.Interaction):
        content = self.msg_content.value
        view = OnboardingDeliveryView(self.target_members, content)
        
        preview_text = (
            f"**Target Members:** {len(self.target_members)}\n"
            f"**Public Message Preview:**\n```\n{content}\n```\n"
            f"💡 *Members also receive a warm welcome DM guiding them to {ONBOARDING_CHANNEL_MENTION}.*\n\n"
            "How would you like to send this?"
        )
        await interaction.response.send_message(content=preview_text, view=view, ephemeral=True)

class OnboardingGroupSelect(discord.ui.Select):
    def __init__(self, groups: dict, all_members_map: dict):
        self.groups_data = groups
        self.all_members_map = all_members_map
        
        options = []
        for days_ago, members in sorted(groups.items()):
            if days_ago == 0:
                label = "Joined Today"
            elif days_ago == 1:
                label = "Joined 1 day ago"
            else:
                label = f"Joined {days_ago} days ago"
                
            options.append(discord.SelectOption(
                label=label,
                description=f"{len(members)} members",
                value=str(days_ago)
            ))
            
            # Limit options to max 25 for discord select menu
            if len(options) == 25:
                break
                
        super().__init__(placeholder="Select a group to onboard...", min_values=1, max_values=1, options=options, custom_id="im8_onboard_group_select")

    async def callback(self, interaction: discord.Interaction):
        selected_days = int(self.values[0])
        members_in_group = self.groups_data[selected_days]
        
        # Open composer modal
        await interaction.response.send_modal(OnboardingComposerModal(members_in_group))

class OnboardingHubView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Scan for New Members", emoji="🔍", style=discord.ButtonStyle.primary, row=0, custom_id="im8_onboard_scan")
    async def btn_scan(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        db = interaction.client.database
        guild = interaction.guild
        now = discord.utils.utcnow()
        
        # Fetch already onboarded
        rows = await db.fetch_all("SELECT user_id FROM onboarded_members WHERE guild_id = ?", (guild.id,))
        onboarded_ids = {row['user_id'] for row in rows}
        
        groups = defaultdict(list)
        
        for m in guild.members:
            if m.bot: continue
            if m.id in onboarded_ids: continue
            
            if m.joined_at:
                days_ago = (now - m.joined_at).days
                if 0 <= days_ago <= 30:
                    groups[days_ago].append(m)
                    
        if not groups:
            await interaction.followup.send("✅ No new members found to onboard! Everyone is up to date.", ephemeral=True)
            return
            
        view = discord.ui.View(timeout=300)
        view.add_item(OnboardingGroupSelect(groups, {}))
        
        total_unonboarded = sum(len(g) for g in groups.values())
        
        await interaction.followup.send(f"Found **{total_unonboarded}** members needing onboarding (within the last 30 days).\nSelect a group to compose a message.", view=view, ephemeral=True)

class OnboardingCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(OnboardingHubView())

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OnboardingCog(bot))
