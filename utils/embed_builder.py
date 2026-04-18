"""
IM8 Bot — Embed Builder
Reusable embed factory with consistent IM8 Health branding.
All embeds share a unified visual language: color palette,
footer format, and emoji conventions.
"""

from datetime import datetime, timezone

import discord

import config


# ═══════════════════════════════════════════════
#  Emoji Conventions (Uniform Set)
# ═══════════════════════════════════════════════
#
#  ◈  Section header / key item
#  ▸  List item / sub-point
#  ━  Horizontal separator (use SEPARATOR constant)
#
#  Status Indicators:
#    ✅  Healthy / Connected / Active
#    ⚠️  Degraded / Warning
#    ❌  Failed / Offline / Error
#
#  Metrics:
#    🏛  Guilds
#    👥  Members
#    ⚡  Commands
#    📡  Gateway / Ping
#    🗃  Database
#    ⏱  Scheduler / Uptime
#    ⚙  System / Config
#

SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
SEPARATOR_THIN = "─────────────────────────────────────"


def _footer_text() -> str:
    """Generates the standard footer text."""
    return f"{config.BOT_NAME}  •  {config.BOT_TAGLINE}"


def _timestamp() -> datetime:
    """Returns the current UTC timestamp for embed timestamps."""
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════
#  Base Embed
# ═══════════════════════════════════════════════

def base_embed(
    title: str = "",
    description: str = "",
    color: int = config.COLOR_BRAND,
) -> discord.Embed:
    """Creates a base embed with IM8 branding."""
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=_timestamp(),
    )
    embed.set_footer(text=_footer_text())
    return embed


# ═══════════════════════════════════════════════
#  Typed Embeds
# ═══════════════════════════════════════════════

def success_embed(
    title: str = "Operation Successful",
    description: str = "",
) -> discord.Embed:
    """Green embed for successful operations."""
    return base_embed(
        title=f"✅  {title}",
        description=description,
        color=config.COLOR_SUCCESS,
    )


def error_embed(
    title: str = "An Error Occurred",
    description: str = "",
) -> discord.Embed:
    """Red embed for errors and failures."""
    return base_embed(
        title=f"❌  {title}",
        description=description,
        color=config.COLOR_ERROR,
    )


def warning_embed(
    title: str = "Warning",
    description: str = "",
) -> discord.Embed:
    """Amber embed for warnings and cautions."""
    return base_embed(
        title=f"⚠️  {title}",
        description=description,
        color=config.COLOR_WARNING,
    )


def info_embed(
    title: str = "Information",
    description: str = "",
) -> discord.Embed:
    """Blue embed for informational messages."""
    return base_embed(
        title=f"◈  {title}",
        description=description,
        color=config.COLOR_INFO,
    )


# ═══════════════════════════════════════════════
#  Status Embed (Premium /status command)
# ═══════════════════════════════════════════════

def status_embeds(
    bot_name: str,
    version: str,
    boot_time: datetime,
    latency_ms: int,
    guild_count: int,
    member_count: int,
    command_count: int,
    cog_count: int,
    health: dict[str, str],
    scheduler_jobs: int = 0,
) -> list[discord.Embed]:
    """Builds the premium status dashboard as two separate embeds.
    First embed: Main info and stats.
    Second embed: System health checks.
    """

    # ── Status Line ──────────────────────────────
    status_icon = "🟢" if health.get("gateway") == "Connected" else "🟡"
    status_text = "Online & Ready" if health.get("gateway") == "Connected" else "Degraded"

    # ── Boot Time (Discord Timestamp) ────────────
    boot_ts = int(boot_time.timestamp())

    # ── Embed 1: Main Overview ───────────────────
    desc_lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"# ✨ {bot_name}",
        f"*{config.BOT_TAGLINE}*",
        "",
        f"{status_icon} **Status:** {status_text}",
        f"📦 **Version:** `{version}`",
        f"⏰ **Boot Time:** <t:{boot_ts}:F>",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    ]

    embed1 = discord.Embed(
        description="\n".join(desc_lines),
        color=config.COLOR_BRAND,
    )

    # ── Statistics Row ───────────────────────────
    embed1.add_field(
        name="📡 Connected Guilds",
        value=f"```\n{guild_count}\n```",
        inline=True,
    )
    embed1.add_field(
        name="👥 Total Members",
        value=f"```\n{member_count}\n```",
        inline=True,
    )
    embed1.add_field(
        name="⚡ Commands Loaded",
        value=f"```\n{command_count}\n```",
        inline=True,
    )
    
    embed1.set_footer(text=f"✨ {bot_name} • {config.BOT_TAGLINE} • {_timestamp().strftime('%d/%m/%Y %I:%M %p')}")

    # ── Embed 2: System Health ───────────────────
    health_lines = ["**🩺 System Health**", "", "```ansi"]
    
    components = {
        "gateway":   "Discord Gateway",
        "database":  "Database",
        "scheduler": "Scheduler",
        "commands":  "Command Handlers",
        "events":    "Event Listeners",
    }

    for key, label in components.items():
        state = health.get(key, "Unknown")
        # Ensure padding logic matches the graphic nicely
        padded_label = f"{label:<22}"
        
        # ANSI color format: yellow text for the state
        ansi_yellow = "\u001b[0;33m"
        ansi_reset = "\u001b[0m"
        
        # Simple checkmark prefix
        prefix = "✓" if state in {"Connected", "Active", "Synced", "Bound", "Initialized", "Running"} else "✗"
        
        health_lines.append(f"{prefix} {padded_label} {ansi_yellow}{state}{ansi_reset}")

    health_lines.append("```")

    embed2 = discord.Embed(
        description="\n".join(health_lines),
        color=config.COLOR_SUCCESS,
    )

    return [embed1, embed2]


def _health_indicator(state: str) -> str:
    """Maps a health state string to a status indicator emoji."""
    healthy_states = {"Connected", "Active", "Synced", "Bound", "Running"}
    degraded_states = {"Connecting", "Pending", "Sync Failed"}

    if state in healthy_states:
        return "✅"
    elif state in degraded_states:
        return "⚠️"
    else:
        return "❌"


# ═══════════════════════════════════════════════
#  Ping Embed
# ═══════════════════════════════════════════════

def ping_embed(ws_latency_ms: int, api_latency_ms: int) -> discord.Embed:
    """Builds the latency report embed for /ping."""

    # Determine quality indicator
    avg = (ws_latency_ms + api_latency_ms) / 2
    if avg < 100:
        quality = "🟢  Excellent"
    elif avg < 200:
        quality = "🟡  Moderate"
    else:
        quality = "🔴  High Latency"

    desc_lines = [
        f"**Connection Quality:**  {quality}",
        "",
        f"📡  **WebSocket:**  `{ws_latency_ms}ms`",
        f"🌐  **API Round-Trip:**  `{api_latency_ms}ms`",
    ]

    embed = base_embed(
        title="◈  Latency Report",
        description="\n".join(desc_lines),
        color=config.COLOR_BRAND,
    )

    return embed
