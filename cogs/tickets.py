import discord
from discord.ext import commands
import logging
import json
import asyncio
from pathlib import Path
import os

logger = logging.getLogger("im8bot.cogs.tickets")

class TicketsHubView(discord.ui.View):
    """The central hub for managing the ticket system from the Mod Panel."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="Select target channel to deploy prompt...", min_values=1, max_values=1, row=0, custom_id="im8_tickets_deploy_select")
    async def select_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        app_channel = select.values[0]
        
        # Resolve AppCommandChannel to an actual TextChannel
        target_channel = interaction.guild.get_channel(app_channel.id)
        if not target_channel:
            try:
                target_channel = await interaction.guild.fetch_channel(app_channel.id)
            except Exception as e:
                return await interaction.response.send_message(f"❌ Could not resolve channel: {e}", ephemeral=True)
        
        embed = discord.Embed(
            title="🎫 Support Tickets",
            description="Welcome to the support center. Please select an appropriate category below corresponding to your concern to open a new private ticket.\n\nOur moderation team will assist you as soon as possible.",
            color=0x00C9A7
        )
        view = TicketCreateView()
        
        try:
            await target_channel.send(embed=embed, view=view)
            await interaction.response.send_message(f"✅ Professional ticket system prompt deployed to {target_channel.mention}!", ephemeral=True)
            # Reset selection in UI
            self.children[0].placeholder = "Select target channel to deploy prompt..."
            await interaction.message.edit(view=self)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to deploy to {target_channel.mention}: {e}", ephemeral=True)

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.category], placeholder="Configure target Open Tickets category...", min_values=1, max_values=1, row=1, custom_id="im8_tickets_category_select")
    async def select_cfg_category(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        app_channel = select.values[0]
        db = interaction.client.database
        await db.execute("INSERT OR REPLACE INTO ticket_config (guild_id, category_id) VALUES (?, ?)", (interaction.guild.id, app_channel.id))
        await interaction.response.send_message(f"✅ New open tickets will hereafter be created under the **{app_channel.name}** category.", ephemeral=True)

    @discord.ui.button(label="View Recent Transcripts", emoji="📂", style=discord.ButtonStyle.secondary, row=2, custom_id="im8_tickets_transcripts")
    async def btn_transcripts(self, interaction: discord.Interaction, button: discord.ui.Button):
        records = await interaction.client.database.fetch_all("SELECT * FROM ticket_transcripts ORDER BY closed_at DESC LIMIT 10")
        if not records:
            return await interaction.response.send_message("❌ No closed ticket transcripts found.", ephemeral=True)
            
        files = []
        for r in records:
            if os.path.exists(r["json_path"]):
                files.append(discord.File(r["json_path"]))
        
        if files:
            await interaction.response.send_message(f"📂 Found {len(files)} recent transcripts:", files=files, ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ Transcripts are logged in the database, but the physical files have been moved or deleted.", ephemeral=True)

    @discord.ui.button(label="Back to Main Panel", emoji="🔙", style=discord.ButtonStyle.secondary, row=2, custom_id="im8_tickets_back")
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.panel import ModPanelView
        view = ModPanelView()
        await interaction.response.edit_message(content=None, view=view)


class TicketCreateView(discord.ui.View):
    """Persistent view attached to the public ticket creation prompt."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(placeholder="Select an issue category...", options=[
        discord.SelectOption(label="Shipping Concerns", emoji="📦", value="shipping", description="Questions regarding dispatch times and couriers."),
        discord.SelectOption(label="General Inquiry", emoji="💬", value="general", description="Basic questions about IM8 components."),
        discord.SelectOption(label="Technical Support", emoji="🛠️", value="tech", description="Report a bug or website issue."),
        discord.SelectOption(label="Other", emoji="❓", value="other", description="Something else.")
    ], custom_id="im8_ticket_create_select")
    async def select_category(self, interaction: discord.Interaction, select: discord.ui.Select):
        category = select.values[0]
        guild = interaction.guild
        user = interaction.user
        
        db = interaction.client.database
        existing = await db.fetch_one("SELECT * FROM tickets WHERE user_id = ? AND status = 'open' AND guild_id = ?", (user.id, guild.id))
        
        if existing:
            return await interaction.response.send_message("❌ You already have an open ticket assigned to you.", ephemeral=True)
            
        channel_category = interaction.channel.category
        
        config_record = await db.fetch_one("SELECT category_id FROM ticket_config WHERE guild_id = ?", (guild.id,))
        if config_record:
            cat_id = config_record["category_id"]
            try:
                found_cat = guild.get_channel(cat_id) or await guild.fetch_channel(cat_id)
                if isinstance(found_cat, discord.CategoryChannel):
                    channel_category = found_cat
            except Exception:
                pass

        # Setup precise restrictive overwrites for the private channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, embed_links=True, attach_files=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True, read_message_history=True)
        }
        
        # Note: In a production server, we might want to also grant `manage_messages` role access here, 
        # but for now administrators or the bot owner can access all channels by default due to their high permissions.

        try:
            ticket_channel = await guild.create_text_channel(
                name=f"ticket-{user.name}",
                category=channel_category,
                overwrites=overwrites,
                reason="Auto-created Support Ticket"
            )
        except Exception as e:
            return await interaction.response.send_message(f"❌ Failed to create channel: {e}", ephemeral=True)
            
        await db.execute(
            "INSERT INTO tickets (guild_id, channel_id, user_id, category) VALUES (?, ?, ?, ?)",
            (guild.id, ticket_channel.id, user.id, category)
        )
        
        # Professional Default Message
        embed = discord.Embed(
            title=f"🎫 Ticket Opened: {category.title()}",
            description=f"Welcome {user.mention}! A member of our administrative staff will review your case shortly.\n\nPlease define your issue in as much detail as possible so that we can assist efficiently.",
            color=0x00C9A7
        )
        embed.set_footer(text="Click the red button below to securely close this ticket at any time.")
        
        view = TicketControlView()
        msg = await ticket_channel.send(content=f"{user.mention}", embed=embed, view=view)
        await msg.pin()
        
        await interaction.response.send_message(f"✅ Ticket opened successfully: {ticket_channel.mention}", ephemeral=True)


class TicketControlView(discord.ui.View):
    """Persistent view attached inside the actual ticket channel."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="im8_ticket_close_btn")
    async def btn_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        await interaction.response.edit_message(view=self)
        
        channel = interaction.channel
        guild = interaction.guild
        db = interaction.client.database
        
        ticket_record = await db.fetch_one("SELECT * FROM tickets WHERE channel_id = ? AND status = 'open'", (channel.id,))
        if not ticket_record:
            return await interaction.followup.send("❌ Error: Could not find an active ticket record for this channel.", ephemeral=True)
            
        await interaction.followup.send("🔒 **Locking ticket and generating official transcript...** Please wait.", ephemeral=True)
        
        # Compile messages array for JSON output
        messages = []
        async for msg in channel.history(limit=5000, oldest_first=True):
            entry = {
                "id": msg.id,
                "author": str(msg.author),
                "author_id": msg.author.id,
                "content": msg.content,
                "created_at": msg.created_at.isoformat(),
                "attachments": [a.url for a in msg.attachments]
            }
            if msg.embeds:
                 entry["embeds"] = [e.to_dict() for e in msg.embeds]
            messages.append(entry)
            
        # Secure Path Logic
        data_dir = Path("data/transcripts")
        data_dir.mkdir(parents=True, exist_ok=True)
        filename = f"ticket_{ticket_record['ticket_id']}_{ticket_record['user_id']}.json"
        file_path = data_dir / filename
        
        # Write structure to JSON
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump({
                "ticket_id": ticket_record["ticket_id"],
                "category": ticket_record["category"],
                "user_id": ticket_record["user_id"],
                "closed_by_auth": str(interaction.user),
                "closed_by_id": interaction.user.id,
                "messages": messages
            }, f, indent=4)
            
        # Commit to DB
        await db.execute("UPDATE tickets SET status = 'closed' WHERE ticket_id = ?", (ticket_record['ticket_id'],))
        await db.execute("INSERT INTO ticket_transcripts (ticket_id, json_path, closed_by) VALUES (?, ?, ?)",
                         (ticket_record['ticket_id'], str(file_path), interaction.user.id))
                         
        # Dispatch copy to Ticket Opener
        try:
            opener = guild.get_member(ticket_record['user_id']) or await guild.fetch_member(ticket_record['user_id'])
            if opener:
                await opener.send(
                    f"Your support ticket (`{ticket_record['category']}`) in **{guild.name}** has been securely closed. Your official transcript is attached below.",
                    file=discord.File(str(file_path))
                )
        except Exception as e:
            logger.warning(f"Could not send transcript DM to user {ticket_record['user_id']}: {e}")
            
        # Delete channel after short buffer
        await asyncio.sleep(3)
        try:
            await channel.delete(reason=f"Ticket closed by {interaction.user}")
        except Exception as e:
            logger.error(f"Failed to delete channel: {e}")


class TicketCog(commands.Cog):
    """Core cog for tracking Ticket Systems."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        """Register the views to ensure restart resilience as requested."""
        self.bot.add_view(TicketCreateView())
        self.bot.add_view(TicketControlView())
        logger.info("Ticket System views dynamically registered for persistence.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketCog(bot))
