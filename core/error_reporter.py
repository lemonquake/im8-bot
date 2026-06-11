"""
IM8 Bot — Error Reporter
Surfaces unhandled exceptions to a maintainers' Discord channel so problems are
seen in real time instead of only in the on-disk log. Reporting is throttled by
error signature so a tight error loop can never spam the channel (or get the
bot rate-limited), and every failure path degrades gracefully to logging only.
"""

import logging
import time
import traceback
from datetime import datetime, timezone

import discord

import config

logger = logging.getLogger("im8bot.errors")

# Don't repeat the same error signature to the channel more than once per window.
_THROTTLE_SECONDS = 120
# Discord embed field hard limit is 1024; keep tracebacks comfortably under it.
_MAX_TRACE_CHARS = 1500


class ErrorReporter:
    """Sends throttled, formatted error reports to a Discord channel."""

    def __init__(self, channel_id: int) -> None:
        self.channel_id = channel_id
        # signature -> last-sent monotonic timestamp
        self._last_sent: dict[str, float] = {}

    def _should_send(self, signature: str) -> bool:
        now = time.monotonic()
        last = self._last_sent.get(signature)
        if last is not None and (now - last) < _THROTTLE_SECONDS:
            return False
        self._last_sent[signature] = now
        # Opportunistically drop stale entries so the dict can't grow forever.
        if len(self._last_sent) > 256:
            cutoff = now - _THROTTLE_SECONDS
            self._last_sent = {k: v for k, v in self._last_sent.items() if v >= cutoff}
        return True

    async def report(
        self,
        bot: discord.Client,
        source: str,
        error: BaseException,
        context: str | None = None,
    ) -> None:
        """Formats and sends an error report. Never raises."""
        if not self.channel_id:
            return  # Discord reporting disabled.

        try:
            exc_type = type(error).__name__
            signature = f"{source}:{exc_type}:{error}"
            if not self._should_send(signature):
                return

            channel = bot.get_channel(self.channel_id)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(self.channel_id)
                except Exception:
                    return  # Channel unavailable — log already has the detail.

            tb = "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            )
            if len(tb) > _MAX_TRACE_CHARS:
                tb = "…(truncated)…\n" + tb[-_MAX_TRACE_CHARS:]

            embed = discord.Embed(
                title=f"⚠️ Unhandled Error • {source}",
                description=f"**{exc_type}:** {str(error)[:500] or '(no message)'}",
                color=config.COLOR_ERROR,
                timestamp=datetime.now(timezone.utc),
            )
            if context:
                embed.add_field(name="Context", value=context[:1024], inline=False)
            embed.add_field(name="Traceback", value=f"```py\n{tb}\n```", inline=False)
            embed.set_footer(text="IM8 Health • Error Reporter")

            await channel.send(embed=embed)
        except Exception as e:
            # Reporting must never become a source of errors itself.
            logger.error(f"Error reporter failed to send report: {e}")
