"""
IM8 Bot — Global Components Cog
Handles persistent interactions for Embed Role Buttons.
"""

import discord
from discord.ext import commands
import logging

logger = logging.getLogger("im8bot.cogs.components")

class GlobalComponents(commands.Cog):
    """Global listener for interactive message components (Buttons)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Global listener for button interactions."""
        if interaction.type != discord.InteractionType.component:
            return

        custom_id = interaction.data.get("custom_id")
        if not custom_id or not custom_id.startswith("im8_role_"):
            return

        # Format: im8_role_<role_id>
        try:
            role_id = int(custom_id.replace("im8_role_", ""))
            role = interaction.guild.get_role(role_id)
            
            if not role:
                await interaction.response.send_message("❌ This role no longer exists.", ephemeral=True)
                return

            member = interaction.user
            if role in member.roles:
                await member.remove_roles(role, reason="IM8 Self-Role Interaction")
                await interaction.response.send_message(f"✅ Removed role: **{role.name}**", ephemeral=True)
            else:
                # Check bot hierarchy
                if role >= interaction.guild.me.top_role:
                    await interaction.response.send_message("❌ I cannot assign this role (it is higher than mine).", ephemeral=True)
                    return
                
                await member.add_roles(role, reason="IM8 Self-Role Interaction")
                await interaction.response.send_message(f"✅ Added role: **{role.name}**", ephemeral=True)
        
        except Exception as e:
            logger.error(f"Error handling role button: {e}")
            await interaction.response.send_message(f"❌ An error occurred: `{e}`", ephemeral=True)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GlobalComponents(bot))
