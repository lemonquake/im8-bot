import discord
from discord.ext import commands
import logging
from collections import defaultdict
import datetime
import asyncio

logger = logging.getLogger("im8bot.cogs.onboarding")

DEFAULT_MESSAGE = """👋 Please welcome our New members! 
Welcome to IM8 Health!

We’re excited to have you here in our IM8 affiliate community.
You can introduce yourselves here!"""

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
        
        # Tag members
        mentions = " ".join([m.mention for m in self.target_members])
        full_msg = f"{mentions}\n\n{self.message_content}"
        
        try:
            await real_channel.send(full_msg)
            # Insert into DB
            now_str = discord.utils.utcnow().isoformat()
            db = interaction.client.database
            for m in self.target_members:
                try:
                    await db.execute(
                        "INSERT OR IGNORE INTO onboarded_members (user_id, guild_id, onboarded_at) VALUES (?, ?, ?)",
                        (m.id, interaction.guild.id, now_str)
                    )
                except Exception:
                    pass
            await interaction.followup.send(f"✅ Successfully sent message in {real_channel.mention} and marked members as onboarded.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error sending message: {e}", ephemeral=True)
            
    @discord.ui.button(label="Back", emoji="🔙", style=discord.ButtonStyle.secondary, row=1)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=self.parent_view)

class OnboardingDeliveryView(discord.ui.View):
    def __init__(self, target_members: list[discord.Member], message_content: str):
        super().__init__(timeout=300)
        self.target_members = target_members
        self.message_content = message_content

    @discord.ui.button(label="Send to Channel", emoji="💬", style=discord.ButtonStyle.primary, row=0)
    async def btn_send_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = OnboardingChannelSelectView(self.target_members, self.message_content, self)
        await interaction.response.edit_message(view=view)

    @discord.ui.button(label="Send as DMs", emoji="✉️", style=discord.ButtonStyle.danger, row=0)
    async def btn_send_dm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        success_count = 0
        fail_count = 0
        
        for m in self.target_members:
            try:
                await m.send(self.message_content)
                success_count += 1
                await asyncio.sleep(1) # delay to prevent rate limits
            except Exception:
                fail_count += 1
        
        await self._mark_onboarded(interaction)
        await interaction.followup.send(f"✅ Finished DMing members.\n**Success:** {success_count}\n**Failed:** {fail_count} (DMs might be closed)\nMembers have been marked as onboarded.", ephemeral=True)

    async def _mark_onboarded(self, interaction: discord.Interaction):
        # Insert into DB
        now_str = discord.utils.utcnow().isoformat()
        db = interaction.client.database
        
        for m in self.target_members:
            try:
                await db.execute(
                    "INSERT OR IGNORE INTO onboarded_members (user_id, guild_id, onboarded_at) VALUES (?, ?, ?)",
                    (m.id, interaction.guild.id, now_str)
                )
            except Exception as e:
                logger.error(f"Failed to insert onboarded member {m.id}: {e}")

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
        
        preview_text = f"**Target Members:** {len(self.target_members)}\n**Message Preview:**\n```\n{content}\n```\nHow would you like to send this?"
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
