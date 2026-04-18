"""
IM8 Bot — Hook Editor Cog
High-fidelity Hook Editor Hub with custom identity support.
Supports:
  1. Webhook Name & Avatar
  2. Normal Message (Content Only)
  3. Optional Embedded Message
  4. Multi-Channel Broadcasting
  5. Persistent Scheduling
"""

import json
import discord
from discord import app_commands
from discord.ext import commands
import logging
from typing import List, Optional

from core.hook_script import HookScript
from cogs.embed import (
    ContentModal, AuthorSelectionView, ImageModal, 
    ColorSelectView, FieldManagerView, FooterModal, 
    PingSelectionView, ButtonModal, TemplateSelectView,
    ChannelSelectView
)

logger = logging.getLogger("im8bot.cogs.hooks")

# ═══════════════════════════════════════════════════════════
#  IDENTITY MODALS
# ═══════════════════════════════════════════════════════════

class IdentityModal(discord.ui.Modal, title="🪝 Edit Hook Identity"):
    """Webhook Name + Avatar editor."""

    def __init__(self, script: HookScript, editor_view: "HookEditorView"):
        super().__init__()
        self.script = script
        self.editor_view = editor_view

        self.hook_name = discord.ui.TextInput(
            label="Webhook Name",
            style=discord.TextStyle.short,
            placeholder="e.g. IM8 Security, Announcements, etc.",
            default=self.script.hook_name,
            required=False,
            max_length=80,
        )
        self.hook_avatar = discord.ui.TextInput(
            label="Webhook Avatar URL",
            style=discord.TextStyle.short,
            placeholder="https://i.imgur.com/...",
            default=self.script.hook_avatar_url,
            required=False,
        )
        self.add_item(self.hook_name)
        self.add_item(self.hook_avatar)

    async def on_submit(self, interaction: discord.Interaction):
        self.script.hook_name = self.hook_name.value.strip() or "IM8 Hook"
        self.script.hook_avatar_url = self.hook_avatar.value.strip() or None
        await self.editor_view.refresh(interaction)

class IdentitySaveModal(discord.ui.Modal, title="💾 Save Identity Preset"):
    """Modal to name and save the current identity."""

    def __init__(self, script: HookScript, editor_view: "HookEditorView"):
        super().__init__()
        self.script = script
        self.editor_view = editor_view

        self.preset_name = discord.ui.TextInput(
            label="Preset Nickname",
            style=discord.TextStyle.short,
            placeholder="e.g. Announcements, News Bot, Security...",
            required=True,
            max_length=50,
        )
        self.add_item(self.preset_name)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.client.database.execute(
            "INSERT INTO hook_identities (preset_name, guild_id, hook_name, avatar_url, created_by) VALUES (?, ?, ?, ?, ?)",
            (self.preset_name.value.strip(), interaction.guild_id, self.script.hook_name, self.script.hook_avatar_url, interaction.user.id)
        )
        await interaction.response.send_message(f"✅ Identity preset '**{self.preset_name.value}**' saved!", ephemeral=True)


class IdentitySelectView(discord.ui.View):
    """View to select and load a saved identity preset."""

    def __init__(self, identities: list, script: HookScript, editor_view: "HookEditorView"):
        super().__init__(timeout=180)
        self.identities = identities
        self.script = script
        self.editor_view = editor_view

        options = []
        for iden in identities[:25]:
            options.append(discord.SelectOption(
                label=iden['preset_name'],
                description=f"Name: {iden['hook_name']}",
                value=str(iden['id'])
            ))
        
        self.select = discord.ui.Select(placeholder="Choose an identity to load...", options=options)
        self.select.callback = self._select_callback
        self.add_item(self.select)

    async def _select_callback(self, interaction: discord.Interaction):
        iden_id = int(self.select.values[0])
        iden = next((i for i in self.identities if i['id'] == iden_id), None)
        if iden:
            self.script.hook_name = iden['hook_name']
            self.script.hook_avatar_url = iden['avatar_url']
            await interaction.response.send_message(f"✅ Loaded identity: **{iden['preset_name']}**", ephemeral=True)
            await self.editor_view.refresh(interaction)

    @discord.ui.button(label="Back", emoji="🔙", style=discord.ButtonStyle.secondary)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.editor_view.refresh(interaction)


class IdentityHubView(discord.ui.View):
    """Sub-menu for Identity management."""

    def __init__(self, script: HookScript, editor_view: "HookEditorView"):
        super().__init__(timeout=300)
        self.script = script
        self.editor_view = editor_view

    @discord.ui.button(label="Edit Manual", emoji="✍️", style=discord.ButtonStyle.primary)
    async def btn_edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(IdentityModal(self.script, self.editor_view))

    @discord.ui.button(label="Save Current", emoji="💾", style=discord.ButtonStyle.success)
    async def btn_save(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(IdentitySaveModal(self.script, self.editor_view))

    @discord.ui.button(label="Load Preset", emoji="📂", style=discord.ButtonStyle.secondary)
    async def btn_load(self, interaction: discord.Interaction, button: discord.ui.Button):
        identities = await interaction.client.database.fetch_all(
            "SELECT * FROM hook_identities WHERE guild_id = ?",
            (interaction.guild_id,)
        )
        if not identities:
            await interaction.response.send_message("📭 No saved identities found for this server.", ephemeral=True)
            return
        
        view = IdentitySelectView(identities, self.script, self.editor_view)
        await interaction.response.edit_message(
            content="**📂 Load Identity Preset**\nSelect an identity from the list below to apply it.",
            view=view
        )

    @discord.ui.button(label="Back to Hub", emoji="🔙", style=discord.ButtonStyle.secondary)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.editor_view.refresh(interaction)

# ═══════════════════════════════════════════════════════════
#  WEBHOOK MANAGER
# ═══════════════════════════════════════════════════════════

class WebhookManager:
    """Utility to manage webhook creation and delivery."""

    @staticmethod
    async def get_or_create_webhook(channel: discord.TextChannel) -> Optional[discord.Webhook]:
        """Finds an existing webhook for the bot or creates a new one."""
        try:
            webhooks = await channel.webhooks()
            for wh in webhooks:
                if wh.user.id == channel.guild.me.id:
                    return wh
            return await channel.create_webhook(name="IM8 Hook Connector")
        except Exception as e:
            logger.error(f"Failed to get/create webhook in #{channel.name}: {e}")
            return None

# ═══════════════════════════════════════════════════════════
#  SCHEDULING SUPPORT
# ═══════════════════════════════════════════════════════════

class HookScheduleView(discord.ui.View):
    """Sub-view for configuring a future hook broadcast time."""

    def __init__(self, script: HookScript, editor_view: "HookEditorView"):
        super().__init__(timeout=300)
        self.script = script
        self.editor_view = editor_view
        
        # State
        self.day_offset = 0 
        self.hour = 12
        self.minute = 0
        self.ampm = "PM"

    @discord.ui.select(
        placeholder="Pick a Day...",
        options=[
            discord.SelectOption(label="Today", value="0"),
            discord.SelectOption(label="Tomorrow", value="1"),
            discord.SelectOption(label="In 2 Days", value="2"),
        ],
        row=0
    )
    async def select_day(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.day_offset = int(select.values[0])
        await interaction.response.defer()

    @discord.ui.select(
        placeholder="Hour...",
        options=[discord.SelectOption(label=f"{h}", value=f"{h}") for h in range(1, 13)],
        row=1
    )
    async def select_hour(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.hour = int(select.values[0])
        await interaction.response.defer()

    @discord.ui.select(
        placeholder="Minute...",
        options=[
            discord.SelectOption(label="00", value="0"),
            discord.SelectOption(label="15", value="15"),
            discord.SelectOption(label="30", value="30"),
            discord.SelectOption(label="45", value="45"),
        ],
        row=2
    )
    async def select_minute(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.minute = int(select.values[0])
        await interaction.response.defer()

    @discord.ui.button(label="AM", style=discord.ButtonStyle.secondary, row=3)
    async def btn_am(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.ampm = "AM"
        await interaction.response.edit_message(content=self._build_prompt())

    @discord.ui.button(label="PM", style=discord.ButtonStyle.secondary, row=3)
    async def btn_pm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.ampm = "PM"
        await interaction.response.edit_message(content=self._build_prompt())

    @discord.ui.button(label="Confirm & Schedule Hook", emoji="⏳", style=discord.ButtonStyle.success, row=4)
    async def btn_confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.script.channels:
            await interaction.response.send_message("❌ Pick at least one channel first!", ephemeral=True)
            return

        from datetime import datetime, timedelta
        now = datetime.now()
        run_date = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=self.day_offset)
        
        h = self.hour
        if self.ampm == "PM" and h < 12: h += 12
        if self.ampm == "AM" and h == 12: h = 0
        run_date = run_date.replace(hour=h, minute=self.minute)

        if run_date <= now:
            await interaction.response.send_message("❌ Cannot schedule in the past!", ephemeral=True)
            return

        cog = interaction.client.get_cog("HookEditor")
        await cog.schedule_task(
            guild_id=interaction.guild_id,
            channel_ids=[c.id for c in self.script.channels],
            payload=self.script.to_dict(),
            run_at=run_date,
            created_by=interaction.user.id
        )

        await interaction.response.edit_message(
            content=f"✅ **Hook Scheduled!**\nSent to **{len(self.script.channels)}** channel(s) at `{run_date.strftime('%Y-%m-%d %I:%M %p')}`.",
            embed=None,
            view=None
        )

    @discord.ui.button(label="Back", emoji="🔙", style=discord.ButtonStyle.secondary, row=4)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.editor_view.refresh(interaction)

    def _build_prompt(self):
        day_text = ["Today", "Tomorrow", "In 2 Days"][self.day_offset]
        return f"**⏳ Scheduling Hook**\nSelected: `{day_text} @ {self.hour:02}:{self.minute:02} {self.ampm}`"


class HookManageScheduledView(discord.ui.View):
    """View and manage pending hook tasks."""

    def __init__(self, tasks: list, bot: discord.Client, editor_view: "HookEditorView"):
        super().__init__(timeout=180)
        self.tasks = tasks
        self.bot = bot
        self.editor_view = editor_view

        if tasks:
            options = []
            for t in tasks[:25]:
                dt = t['run_at']
                options.append(discord.SelectOption(
                    label=f"Hook #{t['id']} @ {dt}",
                    description=f"Channels: {len(json.loads(t['target_channels']))}",
                    value=str(t['id'])
                ))
            self.task_select = discord.ui.Select(placeholder="Select a hook to cancel...", options=options)
            self.task_select.callback = self._cancel_callback
            self.add_item(self.task_select)

    async def _cancel_callback(self, interaction: discord.Interaction):
        task_id = self.task_select.values[0]
        cog = self.bot.get_cog("HookEditor")
        await cog.cancel_task(int(task_id))
        await interaction.response.send_message(f"🗑️ Hook **#{task_id}** cancelled.", ephemeral=True)
        await self.editor_view.refresh(interaction)

    @discord.ui.button(label="Back to Hub", emoji="🔙", style=discord.ButtonStyle.secondary)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.editor_view.refresh(interaction)

# ═══════════════════════════════════════════════════════════
#  THE HOOK HUB VIEW
# ═══════════════════════════════════════════════════════════

class HookEditorView(discord.ui.View):
    """The central Hub for the Hook Editor."""

    def __init__(self, script: HookScript = None):
        super().__init__(timeout=None)
        self.script = script
        self._original_interaction: Optional[discord.Interaction] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # State Hydration
        if not self.script:
            row = await interaction.client.database.fetch_one(
                "SELECT payload FROM editor_sessions WHERE message_id = ?",
                (interaction.message.id,)
            )
            if row:
                self.script = HookScript.from_dict(json.loads(row['payload']), interaction.user.id)
            else:
                await interaction.response.send_message("❌ Session expired.", ephemeral=True)
                return False
        return True

    async def refresh(self, interaction: discord.Interaction):
        """Updates the Hub preview and saves session to DB."""
        self._original_interaction = interaction
        
        # Save session to DB
        await interaction.client.database.execute(
            "REPLACE INTO editor_sessions (message_id, user_id, session_type, payload) VALUES (?, ?, ?, ?)",
            (interaction.message.id, self.script.user_id, "hook", self.script.to_json())
        )

        embed = self.script.to_embed(preview=True)
        content = self.script.status_summary()
        if self.script.content:
            content = f"**Preview Content:**\n{self.script.content}\n\n{content}"

        try:
            await interaction.response.edit_message(content=content, embed=embed, view=self)
        except Exception:
            try:
                msg = await interaction.original_response()
                await msg.edit(content=content, embed=embed, view=self)
            except Exception:
                pass

    # ── ROW 0: Identity & Targeting ──────────────────

    @discord.ui.button(label="Identity", emoji="🪝", style=discord.ButtonStyle.primary, row=0, custom_id="im8_hook_btn_identity")
    async def btn_identity(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = IdentityHubView(self.script, self)
        await interaction.response.edit_message(
            content="**🪝 Webhook Identity Management**\nConfigure how the bot appears when sending this hook.",
            view=view
        )

    @discord.ui.button(label="Select Channels", emoji="📍", style=discord.ButtonStyle.primary, row=0, custom_id="im8_hook_btn_channels")
    async def btn_channels(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ChannelSelectView(self.script, self)
        await interaction.response.edit_message(
            content="**📍 Target Channel Selection**\n*Choose where this hook will post.*",
            view=view
        )

    # ── ROW 1: Content ───────────────────────────────

    @discord.ui.button(label="Message Text", emoji="📝", style=discord.ButtonStyle.secondary, row=1, custom_id="im8_hook_btn_msg")
    async def btn_msg_text(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.embed import PingContentModal
        await interaction.response.send_modal(PingContentModal(self.script, self))

    @discord.ui.button(label="Embed Details", emoji="📄", style=discord.ButtonStyle.secondary, row=1, custom_id="im8_hook_btn_details")
    async def btn_embed_details(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ContentModal(self.script, self))

    @discord.ui.button(label="Embed Color", emoji="🎨", style=discord.ButtonStyle.secondary, row=1, custom_id="im8_hook_btn_color")
    async def btn_color(self, interaction: discord.Interaction, button: discord.ui.Button):
        color_view = ColorSelectView(self.script, self)
        await interaction.response.edit_message(view=color_view)

    # ── ROW 2: Media & Fields ───────────────────────

    @discord.ui.button(label="Images", emoji="🖼️", style=discord.ButtonStyle.secondary, row=2, custom_id="im8_hook_btn_images")
    async def btn_images(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ImageModal(self.script, self))

    @discord.ui.button(label="Fields", emoji="📋", style=discord.ButtonStyle.secondary, row=2, custom_id="im8_hook_btn_fields")
    async def btn_fields(self, interaction: discord.Interaction, button: discord.ui.Button):
        field_view = FieldManagerView(self.script, self)
        await interaction.response.edit_message(view=field_view)

    @discord.ui.button(label="Footer", emoji="📎", style=discord.ButtonStyle.secondary, row=2, custom_id="im8_hook_btn_footer")
    async def btn_footer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FooterModal(self.script, self))

    # ── ROW 3: Scheduling ───────────────────────────

    @discord.ui.button(label="Schedule Hook", emoji="⏳", style=discord.ButtonStyle.success, row=3, custom_id="im8_hook_btn_schedule")
    async def btn_schedule(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = HookScheduleView(self.script, self)
        await interaction.response.edit_message(content=view._build_prompt(), view=view)

    @discord.ui.button(label="Manage Scheduled", emoji="🗓️", style=discord.ButtonStyle.secondary, row=3, custom_id="im8_hook_btn_manage")
    async def btn_manage(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("HookEditor")
        tasks = await cog.get_pending_tasks(interaction.guild_id)
        if not tasks:
            await interaction.response.send_message("📭 No pending hook schedules.", ephemeral=True)
            return
        view = HookManageScheduledView(tasks, interaction.client, self)
        await interaction.response.edit_message(view=view)

    # ── ROW 4: Send Now ─────────────────────────────

    @discord.ui.button(label="SEND HOOK NOW", emoji="🚀", style=discord.ButtonStyle.danger, row=4, custom_id="im8_hook_btn_send")
    async def btn_send(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.script.channels:
            await interaction.response.send_message("❌ Pick at least one channel first!", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        sent_to = []
        failed = []
        embed = self.script.to_embed()
        
        # Check if the first embed is actually empty
        first_embed = self.script.embeds[0]
        if not first_embed.get("title") and not first_embed.get("description"):
            embed = None

        for channel in self.script.channels:
            wh = await WebhookManager.get_or_create_webhook(channel)
            if not wh:
                failed.append(f"#{channel.name} (Webhook failed)")
                continue
            try:
                await wh.send(
                    content=self.script.content,
                    embed=embed,
                    username=self.script.hook_name,
                    avatar_url=self.script.hook_avatar_url,
                    wait=True
                )
                sent_to.append(f"#{channel.name}")
            except Exception as e:
                failed.append(f"#{channel.name}: {e}")

        msg = f"**✅ Hook dispatched to {len(sent_to)} channel(s)**"
        if failed:
            msg += f"\n**❌ Failed ({len(failed)}):**\n" + "\n".join(failed)
        await interaction.followup.send(msg, ephemeral=True)

# ═══════════════════════════════════════════════════════════
#  COG REGISTRATION
# ═══════════════════════════════════════════════════════════

@app_commands.default_permissions(manage_messages=True)
class HookEditor(commands.Cog):
    """Advanced interactive Hook Editor Hub."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.loop.create_task(self._reload_tasks())
        # Register persistent Hub
        self.bot.add_view(HookEditorView())

    async def _reload_tasks(self):
        await self.bot.wait_until_ready()
        tasks = await self.bot.database.fetch_all("SELECT * FROM scheduled_tasks WHERE status = 'pending' AND task_type = 'webhook_broadcast'")
        from datetime import datetime
        for t in tasks:
            run_at = datetime.fromisoformat(t['run_at'])
            if run_at < datetime.now():
                if (datetime.now() - run_at).total_seconds() < 3600:
                    run_at = datetime.now()
                else:
                    await self.bot.database.execute("UPDATE scheduled_tasks SET status = 'failed' WHERE id = ?", (t['id'],))
                    continue
            self.bot.scheduler.add_date_job(
                func=self.execute_scheduled_hook,
                job_id=f"hook_{t['id']}",
                run_date=run_at,
                kwargs={"task_id": t['id']}
            )

    async def schedule_task(self, guild_id, channel_ids, payload, run_at, created_by):
        cursor = await self.bot.database.execute(
            "INSERT INTO scheduled_tasks (guild_id, target_channels, task_type, payload, run_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, json.dumps(channel_ids), "webhook_broadcast", json.dumps(payload), run_at.isoformat(), created_by)
        )
        task_id = cursor.lastrowid
        self.bot.scheduler.add_date_job(
            func=self.execute_scheduled_hook,
            job_id=f"hook_{task_id}",
            run_date=run_at,
            kwargs={"task_id": task_id}
        )

    async def cancel_task(self, task_id: int):
        await self.bot.database.execute("UPDATE scheduled_tasks SET status = 'cancelled' WHERE id = ?", (task_id,))
        self.bot.scheduler.remove_job(f"hook_{task_id}")

    async def get_pending_tasks(self, guild_id: int):
        return await self.bot.database.fetch_all("SELECT * FROM scheduled_tasks WHERE guild_id = ? AND status = 'pending' AND task_type = 'webhook_broadcast'", (guild_id,))

    async def execute_scheduled_hook(self, task_id: int):
        task = await self.bot.database.fetch_one("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,))
        if not task or task['status'] != 'pending': return
        try:
            payload = json.loads(task['payload'])
            channel_ids = json.loads(task['target_channels'])
            script = HookScript.from_dict(payload, task['created_by'])
            embed = script.to_embed()
            
            # Check if the first embed is actually empty
            first_embed = script.embeds[0]
            if not first_embed.get("title") and not first_embed.get("description"):
                embed = None
            sent_count = 0
            for cid in channel_ids:
                channel = self.bot.get_channel(cid)
                if not channel: channel = await self.bot.fetch_channel(cid)
                if channel:
                    wh = await WebhookManager.get_or_create_webhook(channel)
                    if wh:
                        await wh.send(content=script.content, embed=embed, username=script.hook_name, avatar_url=script.hook_avatar_url)
                        sent_count += 1
            await self.bot.database.execute("UPDATE scheduled_tasks SET status = 'sent' WHERE id = ?", (task_id,))
        except Exception as e:
            await self.bot.database.execute("UPDATE scheduled_tasks SET status = 'failed', last_error = ? WHERE id = ?", (str(e), task_id))

    @app_commands.command(name="hook", description="Launch the interactive Hook Editor Hub.")
    async def hook_cmd(self, interaction: discord.Interaction):
        script = HookScript(user_id=interaction.user.id)
        view = HookEditorView(script)
        await interaction.response.send_message(
            content=script.status_summary(), 
            embed=script.to_embed(preview=True), 
            view=view, 
            ephemeral=True
        )
        # Save session
        msg = await interaction.original_response()
        await interaction.client.database.execute(
            "INSERT INTO editor_sessions (message_id, user_id, session_type, payload) VALUES (?, ?, ?, ?)",
            (msg.id, interaction.user.id, "hook", script.to_json())
        )

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HookEditor(bot))
