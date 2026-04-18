"""
IM8 Bot — System Cog
Administrative commands for bot status, diagnostics, and management.
Provides /status, /ping, and /reload slash commands.
"""

import time
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils import embed_builder

logger = logging.getLogger("im8bot.cogs.system")


@app_commands.default_permissions(manage_messages=True)
class System(commands.Cog):
    """System administration and diagnostics commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ═══════════════════════════════════════════════
    #  /status — Premium Dashboard
    # ═══════════════════════════════════════════════

    @app_commands.command(
        name="status",
        description="Display bot system status and health diagnostics.",
    )
    async def status(self, interaction: discord.Interaction) -> None:
        """Sends the premium status dashboard embed with system health indicators."""

        health = self.bot.get_health_summary()
        ws_latency = round(self.bot.latency * 1000)

        embeds = embed_builder.status_embeds(
            bot_name=config.BOT_NAME,
            version=self.bot.version,
            boot_time=self.bot.boot_time,
            latency_ms=ws_latency,
            guild_count=len(self.bot.guilds),
            member_count=self.bot.total_members,
            command_count=self.bot.total_commands,
            cog_count=len(self.bot.cogs),
            health=health,
            scheduler_jobs=self.bot.scheduler.job_count,
        )

        # Build action row with branded buttons
        view = StatusView()

        await interaction.response.send_message(embeds=embeds, view=view)
        logger.info(f"/status executed by {interaction.user} in {interaction.guild}")

    # ═══════════════════════════════════════════════
    #  /ping — Latency Report
    # ═══════════════════════════════════════════════

    @app_commands.command(
        name="ping",
        description="Check bot latency and connection quality.",
    )
    async def ping(self, interaction: discord.Interaction) -> None:
        """Measures WebSocket and API round-trip latency."""

        # WebSocket latency
        ws_latency = round(self.bot.latency * 1000)

        # API round-trip: measure time to send and receive response
        start = time.perf_counter()
        await interaction.response.defer(thinking=True)
        api_latency = round((time.perf_counter() - start) * 1000)

        embed = embed_builder.ping_embed(
            ws_latency_ms=ws_latency,
            api_latency_ms=api_latency,
        )

        await interaction.followup.send(embed=embed)
        logger.info(f"/ping executed by {interaction.user} — WS: {ws_latency}ms, API: {api_latency}ms")

    # ═══════════════════════════════════════════════
    #  /reload — Hot-Reload Cogs (Owner Only)
    # ═══════════════════════════════════════════════

    @app_commands.command(
        name="reload",
        description="Hot-reload a bot module without restarting.",
    )
    @app_commands.describe(module="The cog module name to reload (e.g. system, events).")
    async def reload(self, interaction: discord.Interaction, module: str) -> None:
        """Reloads a specific cog module. Restricted to bot owner."""

        # Owner check
        app_info = await self.bot.application_info()
        if interaction.user.id != app_info.owner.id:
            embed = embed_builder.error_embed(
                title="Access Denied",
                description="This command is restricted to the bot owner.",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        cog_path = f"cogs.{module.lower()}"

        await interaction.response.defer(ephemeral=True)

        try:
            await self.bot.reload_extension(cog_path)
            embed = embed_builder.success_embed(
                title="Module Reloaded",
                description=f"Successfully reloaded `{cog_path}`.",
            )
            logger.info(f"Module reloaded: {cog_path} (by {interaction.user})")
        except commands.ExtensionNotLoaded:
            # Try loading it fresh
            try:
                await self.bot.load_extension(cog_path)
                embed = embed_builder.success_embed(
                    title="Module Loaded",
                    description=f"Module `{cog_path}` was not loaded. Loaded it fresh.",
                )
                logger.info(f"Module loaded fresh: {cog_path} (by {interaction.user})")
            except Exception as e:
                embed = embed_builder.error_embed(
                    title="Load Failed",
                    description=f"Could not load `{cog_path}`.\n```\n{e}\n```",
                )
                logger.error(f"Failed to load {cog_path}: {e}")
        except Exception as e:
            embed = embed_builder.error_embed(
                title="Reload Failed",
                description=f"Error reloading `{cog_path}`.\n```\n{e}\n```",
            )
            logger.error(f"Failed to reload {cog_path}: {e}")

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ═══════════════════════════════════════════════
    #  /reload autocomplete
    # ═══════════════════════════════════════════════

    @reload.autocomplete("module")
    async def reload_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Provides autocomplete for loaded cog names."""
        cog_names = [name.lower() for name in self.bot.cogs.keys()]
        return [
            app_commands.Choice(name=name, value=name)
            for name in cog_names
            if current.lower() in name
        ][:25]


# ═══════════════════════════════════════════════
#  Status View — Action Buttons
# ═══════════════════════════════════════════════

class StatusView(discord.ui.View):
    """Action buttons displayed below the status embed, acting as a Mod Super Panel."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="START",
        emoji="✨",
        style=discord.ButtonStyle.success,
    )
    async def start_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Startup / initialization actions."""
        # Simple ephemeral message for now
        embed = embed_builder.info_embed(
            title="IM8 Health",
            description="Everything is online."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="Quick Guide",
        emoji="📖",
        style=discord.ButtonStyle.primary,
    )
    async def guide_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Sends a quick overview guide."""
        embed = embed_builder.info_embed(
            title="IM8 Quick Guide",
            description=(
                "**Features**:\n"
                "▸ **`/status`**: Main dashboard.\n"
                "▸ **`/ping`**: Connection check.\n"
                "▸ **`/reload`**: Refresh code (Owner only).\n\n"
                "More mod tools coming soon."
            )
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="Server Pulse",
        emoji="❤️",
        style=discord.ButtonStyle.secondary,
    )
    async def pulse_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Quick ping and heartbeat check."""
        bot = interaction.client
        ws_latency = round(bot.latency * 1000)

        start = time.perf_counter()
        await interaction.response.defer(ephemeral=True)
        api_latency = round((time.perf_counter() - start) * 1000)

        embed = embed_builder.ping_embed(
            ws_latency_ms=ws_latency,
            api_latency_ms=api_latency,
        )

        await interaction.followup.send(embed=embed, ephemeral=True)


# ═══════════════════════════════════════════════
#  Cog Setup
# ═══════════════════════════════════════════════

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(System(bot))
