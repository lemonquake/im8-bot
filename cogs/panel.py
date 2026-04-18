import discord
from discord import app_commands
from discord.ext import commands
import logging
import time

logger = logging.getLogger("im8bot.cogs.panel")

@app_commands.default_permissions(manage_messages=True)
class Panel(commands.Cog):
    """The Master Control Panel interface."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # Register persistent Control Panel Hubs
        self.bot.add_view(ModPanelView())
        self.bot.add_view(RolesHubView())

    @app_commands.command(
        name="panel",
        description="Access the Administrative Control Panel.",
    )
    async def panel_cmd(self, interaction: discord.Interaction) -> None:
        """Sends the professional Mod Panel dashboard."""

        embed = discord.Embed(
            title="IM8 Health • Control Panel",
            description="Select a module below to start. Operations are logged.",
            color=0x00C9A7
        )

        embed.add_field(
            name="Modules",
            value=(
                "**Channels:** Open/close channels or view schedules.\n"
                "**Content:** Create embeds or send basic messages.\n"
                "**Analytics:** Refresh or post member stats.\n"
                "**System:** Manage webhooks or hubs."
            ),
            inline=False
        )
        
        embed.set_footer(text="IM8 Health")

        view = ModPanelView()
        await interaction.response.send_message(embed=embed, view=view)


class ModPanelView(discord.ui.View):
    """The advanced multi-row UI for the Mod Panel."""
    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def _send_stub(self, interaction: discord.Interaction, module_name: str):
        """Sends a professional placeholder for modules in development."""
        embed = discord.Embed(
            title=f"Module Offline: {module_name}",
            description="Under development.",
            color=0x2b2d31
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── ROW 1: Channels & Locks ──
    @discord.ui.button(label="Auto-Open Channels", emoji="🔓", style=discord.ButtonStyle.success, row=0, custom_id="im8_panel_open")
    async def btn_open(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_stub(interaction, "Channel Synchronization")

    @discord.ui.button(label="Auto-Close Channels", emoji="🔒", style=discord.ButtonStyle.danger, row=0, custom_id="im8_panel_close")
    async def btn_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_stub(interaction, "Channel Synchronization")

    @discord.ui.button(label="List Automations", emoji="⚙️", style=discord.ButtonStyle.secondary, row=0, custom_id="im8_panel_list")
    async def btn_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_stub(interaction, "Automation Registry")


    # ── ROW 2: Messaging & Content ──
    @discord.ui.button(label="Embed Editor", emoji="📢", style=discord.ButtonStyle.primary, row=1, custom_id="im8_panel_embed")
    async def btn_embed(self, interaction: discord.Interaction, button: discord.ui.Button):
        from core.embed_script import EmbedScript
        from cogs.embed import EmbedEditorView
        
        script = EmbedScript(user_id=interaction.user.id)
        # For persistence, we need to save the session when the Hub is first sent
        view = EmbedEditorView(script)
        
        preview_embeds = script.build_embeds(preview=True)
        # Not ephemeral here because we want persistence across restarts
        await interaction.response.send_message(
            content=script.status_summary(),
            embeds=preview_embeds,
            view=view,
            ephemeral=True
        )
        
        # Save session to DB
        msg = await interaction.original_response()
        await interaction.client.database.execute(
            "INSERT INTO editor_sessions (message_id, user_id, session_type, payload) VALUES (?, ?, ?, ?)",
            (msg.id, interaction.user.id, "embed", script.to_json())
        )

    @discord.ui.button(label="Roles", emoji="👤", style=discord.ButtonStyle.primary, row=1, custom_id="im8_panel_roles")
    async def btn_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = RolesHubView()
        await interaction.response.send_message(
            content="**👤 IM8 Roles Management Hub**\n*Select a tool below to manage server roles.*",
            view=view,
            ephemeral=True
        )

    @discord.ui.button(label="Create Schedule", emoji="📅", style=discord.ButtonStyle.primary, row=1, custom_id="im8_panel_schedule")
    async def btn_schedule(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_stub(interaction, "Content Scheduling")

    @discord.ui.button(label="View Schedules", emoji="📋", style=discord.ButtonStyle.secondary, row=1, custom_id="im8_panel_vschedules")
    async def btn_vschedules(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_stub(interaction, "Content Scheduling")


    # ── ROW 3: Stats ──
    @discord.ui.button(label="Refresh Live Stats", emoji="🔄", style=discord.ButtonStyle.success, row=2, custom_id="im8_panel_stats")
    async def btn_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_stub(interaction, "Live Telemetry")

    @discord.ui.button(label="Post Daily Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=2, custom_id="im8_panel_pstats")
    async def btn_pstats(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_stub(interaction, "Data Reporting")

    @discord.ui.button(label="Hub Maker", emoji="🎟️", style=discord.ButtonStyle.secondary, row=2, custom_id="im8_panel_hub")
    async def btn_hub(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_stub(interaction, "Environment Provisioning")


    # ── ROW 4: Utils ──
    @discord.ui.button(label="Basic Message", emoji="📝", style=discord.ButtonStyle.primary, row=3, custom_id="im8_panel_msg")
    async def btn_msg(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_stub(interaction, "Standard Broadcasts")

    @discord.ui.button(label="Manage Webhooks", emoji="🪝", style=discord.ButtonStyle.secondary, row=3, custom_id="im8_panel_hooks")
    async def btn_web(self, interaction: discord.Interaction, button: discord.ui.Button):
        from core.hook_script import HookScript
        from cogs.hooks import HookEditorView
        
        script = HookScript(user_id=interaction.user.id)
        view = HookEditorView(script)
        
        await interaction.response.send_message(
            content=script.status_summary(),
            embed=script.to_embed(preview=True),
            view=view,
            ephemeral=True
        )

        # Save session to DB
        msg = await interaction.original_response()
        await interaction.client.database.execute(
            "INSERT INTO editor_sessions (message_id, user_id, session_type, payload) VALUES (?, ?, ?, ?)",
            (msg.id, interaction.user.id, "hook", script.to_json())
        )

class RolesHubView(discord.ui.View):
    """The category hub for role-related management tools."""
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Mass Role tool", emoji="🔥", style=discord.ButtonStyle.danger, row=0, custom_id="im8_roles_mass")
    async def btn_mass_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = MassRoleView(self)
        await interaction.response.edit_message(content="**🔥 Mass Role Administrative Tool**", view=view)

    @discord.ui.button(label="Role Member Counts", emoji="🔢", style=discord.ButtonStyle.secondary, row=0, custom_id="im8_roles_counts")
    async def btn_counts(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        roles = sorted(guild.roles, key=lambda r: len(r.members), reverse=True)
        
        lines = ["**🔢 Role Member Populations**"]
        for r in roles:
            if r.is_default(): continue # Skip @everyone
            lines.append(f"• {r.mention}: `{len(r.members)}` members")
            if len(lines) >= 20: break
            
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @discord.ui.button(label="Create Role Button", emoji="🔘", style=discord.ButtonStyle.primary, row=0, custom_id="im8_roles_create_btn")
    async def btn_create_role_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(StandaloneRoleButtonModal())

    @discord.ui.button(label="Reaction Roles", emoji="🎭", style=discord.ButtonStyle.secondary, row=1, custom_id="im8_roles_rxn")
    async def btn_rxn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Stub for future feature
        embed = discord.Embed(title="Module Offline: Reaction Roles", description="Under development.", color=0x2b2d31)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Back to Main Panel", emoji="🔙", style=discord.ButtonStyle.secondary, row=1, custom_id="im8_roles_back")
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ModPanelView()
        await interaction.response.edit_message(content=None, view=view)


class MassRoleView(discord.ui.View):
    """Admin tool to assign a role to all members, skipping exclusions."""

    def __init__(self, parent_view: discord.ui.View):
        super().__init__(timeout=300)
        self.parent_view = parent_view
        self.target_role: discord.Role | None = None
        self.exclusion_roles: list[discord.Role] = []

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="Select Target Role (to add)...", min_values=1, max_values=1, row=0)
    async def select_target(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        self.target_role = select.values[0]
        await interaction.response.send_message(f"🎯 Target role set to: **{self.target_role.name}**", ephemeral=True)

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="Select Exclusion Roles (to skip)...", min_values=1, max_values=5, row=1)
    async def select_exclusions(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        self.exclusion_roles = select.values
        roles_str = ", ".join(r.name for r in self.exclusion_roles)
        await interaction.response.send_message(f"🚫 Exclusion roles: {roles_str}", ephemeral=True)

    @discord.ui.button(label="Execute Mass Role", emoji="🔥", style=discord.ButtonStyle.danger, row=2)
    async def btn_execute(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.target_role:
            await interaction.response.send_message("❌ Please select a target role first.", ephemeral=True)
            return

        # Check bot permissions
        my_top_role = interaction.guild.me.top_role
        if self.target_role >= my_top_role:
            await interaction.response.send_message("❌ I cannot assign this role (it is higher than or equal to my top role).", ephemeral=True)
            return

        await interaction.response.send_message("⚙️ Starting mass role assignment (skipping Bots & Admins)...", ephemeral=True)
        
        # Run the assignment logic
        target = self.target_role
        exclusions = set(self.exclusion_roles or [])
        
        count_success = 0
        count_skipped = 0
        count_failed = 0
        
        async for member in interaction.guild.fetch_members(limit=None):
            if member.bot: continue
            
            # 1. Skip if already has role
            if target in member.roles: 
                count_skipped += 1
                continue
            
            # 2. Skip based on explicit exclusion roles
            if any(r in exclusions for r in member.roles):
                count_skipped += 1
                continue
            
            # 3. Skip based on ADMINISTRATOR permission
            if member.guild_permissions.administrator:
                count_skipped += 1
                continue
                
            try:
                await member.add_roles(target, reason=f"IM8 Mass Role Tool (Requested by {interaction.user})")
                count_success += 1
                import asyncio
                if count_success % 10 == 0: await asyncio.sleep(1)
            except Exception:
                count_failed += 1

        try:
            await interaction.followup.send(
                f"✅ **Mass Role Finished!**\n"
                f"👤 Members Processed: {count_success + count_skipped + count_failed}\n"
                f"➕ Roles Added: {count_success}\n"
                f"⏭️ Skipped: {count_skipped}\n"
                f"❌ Failed: {count_failed}",
                ephemeral=True
            )
        except Exception:
            pass

    @discord.ui.button(label="Back", emoji="🔙", style=discord.ButtonStyle.secondary, row=2)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="**👤 IM8 Roles Management Hub**", view=self.parent_view)


class StandaloneRoleButtonModal(discord.ui.Modal, title="🔘 Create Role Button Post"):
    """Modal to create a simple message with a role-assignment button."""
    def __init__(self):
        super().__init__()
        self.msg_content = discord.ui.TextInput(
            label="Message Content",
            style=discord.TextStyle.paragraph,
            placeholder="e.g. Click the button below to get the 🔔 Notifications role!",
            required=True
        )
        self.btn_label = discord.ui.TextInput(
            label="Button Label",
            style=discord.TextStyle.short,
            placeholder="Notifications",
            required=True
        )
        self.role_id = discord.ui.TextInput(
            label="Role ID",
            style=discord.TextStyle.short,
            placeholder="Paste role ID here...",
            required=True
        )
        self.add_item(self.msg_content)
        self.add_item(self.btn_label)
        self.add_item(self.role_id)

    async def on_submit(self, interaction: discord.Interaction):
        role_id_str = self.role_id.value.strip()
        if not role_id_str.isdigit():
            await interaction.response.send_message("❌ Invalid Role ID provided.", ephemeral=True)
            return
        
        role_id = int(role_id_str)
        role = interaction.guild.get_role(role_id)
        if not role:
            await interaction.response.send_message(f"❌ Role ID `{role_id}` not found in this server.", ephemeral=True)
            return

        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(
            label=self.btn_label.value.strip(),
            style=discord.ButtonStyle.primary,
            custom_id=f"im8_role_{role_id}"
        ))

        await interaction.response.send_message(
            content=self.msg_content.value.strip(),
            view=view
        )

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Panel(bot))
