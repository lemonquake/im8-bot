"""
IM8 Bot — Events Cog
Global event handlers for connection lifecycle, guild tracking,
and comprehensive error handling.
"""

import logging
import traceback

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils import embed_builder

logger = logging.getLogger("im8bot.cogs.events")


class Events(commands.Cog):
    """Global event listeners and error handlers."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # Register the global app command error handler
        self.bot.tree.on_error = self.on_app_command_error

    # ═══════════════════════════════════════════════
    #  Connection Lifecycle
    # ═══════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_connect(self) -> None:
        logger.info("◈  Connected to Discord gateway.")

    @commands.Cog.listener()
    async def on_disconnect(self) -> None:
        logger.warning("◈  Disconnected from Discord gateway.")

    @commands.Cog.listener()
    async def on_resumed(self) -> None:
        logger.info("◈  Session resumed successfully.")

    # ═══════════════════════════════════════════════
    #  Guild Tracking
    # ═══════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        logger.info(
            f"◈  Joined guild: {guild.name} (ID: {guild.id}) "
            f"— {guild.member_count} members"
        )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        logger.info(
            f"◈  Left guild: {guild.name} (ID: {guild.id})"
        )

    # ═══════════════════════════════════════════════
    #  Error Handling — Slash Commands
    # ═══════════════════════════════════════════════

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Global handler for slash command errors.
        Sends a clean error embed to the user and logs the full traceback."""

        # Unwrap the original exception if wrapped
        original = getattr(error, "original", error)

        # ── Permission Errors ────────────────────
        if isinstance(original, app_commands.MissingPermissions):
            embed = embed_builder.error_embed(
                title="Insufficient Permissions",
                description=(
                    "You do not have the required permissions to execute this command.\n\n"
                    f"**Required:** {', '.join(original.missing_permissions)}"
                ),
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # ── Bot Missing Permissions ──────────────
        if isinstance(original, app_commands.BotMissingPermissions):
            embed = embed_builder.error_embed(
                title="Bot Missing Permissions",
                description=(
                    "The bot lacks the required permissions to perform this action.\n\n"
                    f"**Required:** {', '.join(original.missing_permissions)}"
                ),
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # ── Cooldown ─────────────────────────────
        if isinstance(original, app_commands.CommandOnCooldown):
            embed = embed_builder.warning_embed(
                title="Command on Cooldown",
                description=f"Please wait **{original.retry_after:.1f}s** before using this command again.",
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # ── Unexpected Errors ────────────────────
        error_id = f"{interaction.id}"
        logger.error(
            f"Unhandled error in /{interaction.command.name if interaction.command else 'unknown'} "
            f"(Error ID: {error_id}): {original}",
        )
        traceback.print_exception(type(original), original, original.__traceback__)

        embed = embed_builder.error_embed(
            title="Unexpected Error",
            description=(
                "An unexpected error occurred while processing this command.\n"
                "The issue has been logged for review.\n\n"
                f"**Error Reference:** `{error_id}`"
            ),
        )

        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            # If all response methods fail, log it
            logger.error(f"Failed to send error response for Error ID: {error_id}")

    # ═══════════════════════════════════════════════
    #  Error Handling — Prefix Commands
    # ═══════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        """Handler for traditional prefix command errors."""

        # Ignore commands that have local error handlers
        if hasattr(ctx.command, "on_error"):
            return

        original = getattr(error, "original", error)

        if isinstance(error, commands.CommandNotFound):
            return  # Silently ignore unknown prefix commands

        if isinstance(error, commands.MissingPermissions):
            embed = embed_builder.error_embed(
                title="Insufficient Permissions",
                description="You do not have permission to use this command.",
            )
            await ctx.send(embed=embed, delete_after=15)
            return

        # Log unexpected errors
        logger.error(f"Prefix command error in {ctx.command}: {original}")
        traceback.print_exception(type(original), original, original.__traceback__)


# ═══════════════════════════════════════════════
#  Cog Setup
# ═══════════════════════════════════════════════

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Events(bot))
