"""
IM8 Bot — Link Sync Module

Watches a single links channel and mirrors every shared URL into a Google Sheet
with three columns: **A = Discord handle**, **B = display name**, **C = link**.

How it stays correct (no duplicates, no gaps)
─────────────────────────────────────────────
There are three ways a link reaches the sheet, and all three funnel through the
same dedup gate so a URL is never written twice:

1. **Live** — an ``on_message`` listener posts links the instant they're sent.
2. **Sweep** — every ``LINKSYNC_SWEEP_MINUTES`` a background job re-scans the
   channel for anything the live path missed (downtime, a failed write, etc.).
3. **Manual** — the "Sweep Links" button on the Mod Panel runs the same sweep.

The ``link_sync_log`` table (migration v6) is the single source of truth: a URL
is recorded there only **after** a successful write to the sheet. Every path
checks the log first (per *normalized* URL, per guild), so the live listener and
the sweep can run concurrently without racing — and a failed write simply stays
out of the log and gets retried on the next sweep.

The "snapshot" of where we left off is a ``linksync_cursor`` marker in
``analytics_meta`` (the id of the newest message scanned). Each sweep only reads
messages *after* the cursor, so routine sweeps are cheap; the very first sweep
has no cursor and scans the whole channel to backfill its history.

Members holding an excluded staff/bot role are skipped entirely.
"""

import re
import asyncio
import logging
import datetime
from urllib.parse import urlparse, parse_qsl, urlencode

import discord
from discord.ext import commands

import config
from core.sheet_sync import SheetSync, SheetSyncError

logger = logging.getLogger("im8bot.cogs.linksync")

# ═══════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════

# The channel the bot listens to (also the one the sweep scans).
LISTEN_CHANNEL_ID: int = config.LINKSYNC_CHANNEL_ID

# Members holding any of these roles do NOT have their links captured.
EXCLUDED_ROLE_IDS: set[int] = {
    1493905370975047790,
    1486421826799407115,
    1494719686913556543,
    1508764439820767263,
}

# Visual confirmation reaction added to a message once its links are synced.
SYNC_EMOJI = "✅"

# Safety caps.
MAX_LINKS_PER_MESSAGE = 10     # links beyond this in one message are ignored
APPEND_CHUNK = 50              # rows per POST to the sheet during a sweep

# Sheet column for the link (used when seeding dedup from existing sheet rows).
LINK_COLUMN = 3  # A=handle, B=display, C=link

# Tracking/query params stripped before computing the dedup key, so the same
# link shared with different campaign tags counts as one.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "gbraid", "wbraid", "igshid", "mc_eid", "mc_cid",
    "si", "feature", "ref", "ref_src", "spm",
}

# Matches http(s):// URLs and bare www. links. Trailing punctuation is trimmed
# separately so a link at the end of a sentence isn't captured with its period.
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>()\[\]{}\"'`]+", re.IGNORECASE)
_TRAILING = ".,;:!?'\"`>)]}"

# Shared sheet writer (inert until LINKSYNC_WEBHOOK_URL is set).
_sheet = SheetSync(
    config.LINKSYNC_WEBHOOK_URL,
    config.LINKSYNC_WEBHOOK_SECRET,
    config.LINKSYNC_SHEET_NAME,
)

# Serializes every append (live + sweep + manual) so concurrent paths can't
# write the same link or interleave their dedup-check/append/log steps.
_append_lock = asyncio.Lock()

# Guards against two sweeps running for the same guild at once (e.g. the startup
# sweep overlapping the scheduled one, or a user mashing "Sweep Now").
_SWEEP_RUNNING: set[int] = set()

# Sweeps only add the ✅ confirmation to messages newer than this, so the first
# full-history backfill doesn't react to the entire channel.
_REACT_MAX_AGE = datetime.timedelta(days=2)

# Keeps fire-and-forget startup tasks from being garbage-collected.
_BG_TASKS: set = set()


def _spawn(coro) -> "asyncio.Task":
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


# ═══════════════════════════════════════════════
#  URL helpers
# ═══════════════════════════════════════════════

def _clean_url(raw: str) -> str:
    """Trims trailing punctuation and ensures an explicit scheme."""
    u = raw.strip().rstrip(_TRAILING)
    if u.lower().startswith("www."):
        u = "https://" + u
    return u


def _url_key(url: str) -> str:
    """Normalizes a URL into a stable dedup key.

    Lowercases the host, drops a leading ``www.``, removes the fragment and
    common tracking params, and ignores a trailing slash — so cosmetically
    different forms of the same link collapse to one key.
    """
    try:
        p = urlparse(url)
    except Exception:
        return url.lower()
    host = (p.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path.rstrip("/")
    params = sorted(
        (k, v) for k, v in parse_qsl(p.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS
    )
    key = f"{host}{path}"
    if params:
        key += "?" + urlencode(params)
    return key.lower()


def _extract_links(content: str) -> list[tuple[str, str]]:
    """Returns ``[(clean_url, url_key), ...]`` for one message, de-duped within
    the message and capped at ``MAX_LINKS_PER_MESSAGE``."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _URL_RE.finditer(content or ""):
        url = _clean_url(match.group(0))
        if len(url) < 8 or "." not in urlparse(url).netloc:
            continue
        key = _url_key(url)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((url, key))
        if len(out) >= MAX_LINKS_PER_MESSAGE:
            break
    return out


def _excluded(member: discord.Member | None) -> bool:
    """True if the member holds one of the excluded staff/bot roles."""
    if member is None:
        return False
    return any(r.id in EXCLUDED_ROLE_IDS for r in getattr(member, "roles", []))


def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


# ═══════════════════════════════════════════════
#  Bookkeeping (analytics_meta key/value store)
# ═══════════════════════════════════════════════

async def _get_meta(bot: commands.Bot, guild_id: int, key: str) -> str | None:
    row = await bot.database.fetch_one(
        "SELECT value FROM analytics_meta WHERE guild_id = ? AND key = ?",
        (guild_id, key),
    )
    return row["value"] if row else None


async def _set_meta(bot: commands.Bot, guild_id: int, key: str, value: str) -> None:
    await bot.database.execute(
        "INSERT OR REPLACE INTO analytics_meta (guild_id, key, value) VALUES (?, ?, ?)",
        (guild_id, key, value),
    )


async def is_paused(bot: commands.Bot, guild_id: int) -> bool:
    return (await _get_meta(bot, guild_id, "linksync_paused")) == "1"


# ═══════════════════════════════════════════════
#  Dedup + write core
# ═══════════════════════════════════════════════

async def _logged_keys(bot: commands.Bot, guild_id: int, keys: list[str]) -> set[str]:
    """Subset of ``keys`` already recorded as synced for this guild."""
    if not keys:
        return set()
    placeholders = ",".join("?" * len(keys))
    rows = await bot.database.fetch_all(
        f"SELECT url_key FROM link_sync_log WHERE guild_id = ? AND url_key IN ({placeholders})",
        (guild_id, *keys),
    )
    return {r["url_key"] for r in rows}


async def _record(bot: commands.Bot, guild_id: int, channel_id: int, message_id: int,
                  user_id: int, url: str, key: str, handle: str | None, display: str | None) -> None:
    await bot.database.execute(
        "INSERT OR IGNORE INTO link_sync_log "
        "(guild_id, channel_id, message_id, user_id, url, url_key, handle, display) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (guild_id, channel_id, message_id, user_id, url, key, handle, display),
    )


async def _sync_message(bot: commands.Bot, message: discord.Message,
                        candidates: list[tuple[str, str]]) -> int:
    """Appends the not-yet-synced links from one message and logs them.

    Returns the number of new links written. Raises ``SheetSyncError`` if the
    sheet write fails (so nothing is logged and it retries on the next sweep).
    """
    if not candidates:
        return 0
    guild_id = message.guild.id
    handle = message.author.name
    display = message.author.display_name

    async with _append_lock:
        already = await _logged_keys(bot, guild_id, [k for _, k in candidates])
        fresh = [(u, k) for (u, k) in candidates if k not in already]
        if not fresh:
            return 0
        await _sheet.append_rows([[handle, display, u] for (u, _) in fresh])
        for (u, k) in fresh:
            await _record(bot, guild_id, message.channel.id, message.id,
                          message.author.id, u, k, handle, display)
        return len(fresh)


# ═══════════════════════════════════════════════
#  Sweep + seeding
# ═══════════════════════════════════════════════

async def seed_from_sheet(bot: commands.Bot, guild: discord.Guild) -> int:
    """Records links already present in the sheet so they're never re-added.

    Returns the number of new keys seeded. Rows get ``message_id = 0`` since
    they don't originate from a tracked Discord message.
    """
    if not _sheet.configured:
        return 0
    urls = await _sheet.list_urls(column=LINK_COLUMN)
    seeded = 0
    async with _append_lock:
        known = await _logged_keys(bot, guild.id, [_url_key(_clean_url(u)) for u in urls])
        for raw in urls:
            url = _clean_url(raw)
            key = _url_key(url)
            if not key or key in known:
                continue
            known.add(key)
            await _record(bot, guild.id, LISTEN_CHANNEL_ID, 0, 0, url, key, None, None)
            seeded += 1
    await _set_meta(bot, guild.id, "linksync_seeded", "1")
    logger.info(f"Link Sync: seeded {seeded} existing sheet link(s) for guild {guild.id}.")
    return seeded


async def sweep(bot: commands.Bot, guild: discord.Guild) -> dict:
    """Scans the listen channel forward from the cursor and appends new links.

    Returns a stats dict. The cursor only advances when there were no write
    errors, so any link that failed to post stays in range for the next sweep.
    """
    stats = {"scanned": 0, "new": 0, "appended": 0, "excluded": 0, "errors": 0, "error": None}

    if not _sheet.configured:
        stats["error"] = "The sheet web app is not configured (LINKSYNC_WEBHOOK_URL is blank)."
        return stats
    if await is_paused(bot, guild.id):
        stats["error"] = "Link Sync is paused."
        return stats
    if guild.id in _SWEEP_RUNNING:
        stats["error"] = "A sweep is already running for this server — give it a moment."
        return stats

    _SWEEP_RUNNING.add(guild.id)
    try:
        channel = guild.get_channel(LISTEN_CHANNEL_ID)
        if channel is None:
            try:
                channel = await guild.fetch_channel(LISTEN_CHANNEL_ID)
            except Exception:
                stats["error"] = f"Listen channel <#{LISTEN_CHANNEL_ID}> could not be resolved."
                return stats

        cursor_raw = await _get_meta(bot, guild.id, "linksync_cursor")
        after = discord.Object(id=int(cursor_raw)) if cursor_raw else None
        newest_id = int(cursor_raw) if cursor_raw else 0

        # Collect candidate (message, url, key) tuples, de-duped within this batch.
        batch: list[tuple[discord.Message, str, str]] = []
        batch_keys: set[str] = set()
        try:
            async for msg in channel.history(limit=None, after=after, oldest_first=True):
                stats["scanned"] += 1
                newest_id = max(newest_id, msg.id)
                if msg.author.bot:
                    continue
                if _excluded(guild.get_member(msg.author.id)):
                    stats["excluded"] += 1
                    continue
                for (url, key) in _extract_links(msg.content):
                    if key in batch_keys:
                        continue
                    batch_keys.add(key)
                    batch.append((msg, url, key))
        except discord.Forbidden:
            stats["error"] = f"Missing read access to <#{LISTEN_CHANNEL_ID}>."
            return stats
        except Exception as e:
            logger.error(f"Link Sync: sweep scan failed for guild {guild.id}: {e}")
            stats["error"] = f"Scan failed: {e}"
            return stats

        if batch:
            now = datetime.datetime.now(datetime.timezone.utc)
            async with _append_lock:
                already = await _logged_keys(bot, guild.id, list(batch_keys))
                fresh = [(m, u, k) for (m, u, k) in batch if k not in already]
                stats["new"] = len(fresh)
                synced_msg_ids: set[int] = set()
                for chunk in _chunks(fresh, APPEND_CHUNK):
                    rows = [[m.author.name, m.author.display_name, u] for (m, u, _) in chunk]
                    try:
                        await _sheet.append_rows(rows)
                    except SheetSyncError as e:
                        logger.warning(f"Link Sync: sweep append failed (will retry): {e}")
                        stats["errors"] += 1
                        continue  # leave unlogged → retried next sweep
                    for (m, u, k) in chunk:
                        await _record(bot, guild.id, m.channel.id, m.id, m.author.id,
                                      u, k, m.author.name, m.author.display_name)
                        stats["appended"] += 1
                        synced_msg_ids.add(m.id)

                # Best-effort ✅, once per freshly-synced *recent* message — the
                # one-time history backfill skips this so it doesn't react to the
                # entire channel.
                reacted: set[int] = set()
                for (m, _, _) in fresh:
                    if m.id in synced_msg_ids and m.id not in reacted:
                        reacted.add(m.id)
                        if now - m.created_at > _REACT_MAX_AGE:
                            continue
                        try:
                            await m.add_reaction(SYNC_EMOJI)
                        except Exception:
                            pass

        # Advance the cursor only on a clean run, so failed writes stay in range.
        if newest_id and stats["errors"] == 0:
            await _set_meta(bot, guild.id, "linksync_cursor", str(newest_id))
        await _set_meta(bot, guild.id, "linksync_last_sweep",
                        datetime.datetime.now(datetime.timezone.utc).isoformat())
        logger.info(
            f"Link Sync: sweep guild {guild.id} — scanned {stats['scanned']}, "
            f"appended {stats['appended']}, errors {stats['errors']}."
        )
        return stats
    finally:
        _SWEEP_RUNNING.discard(guild.id)


# ═══════════════════════════════════════════════
#  Hub embed
# ═══════════════════════════════════════════════

def _sheet_url() -> str:
    return f"https://docs.google.com/spreadsheets/d/{config.LINKSYNC_SPREADSHEET_ID}/edit"


async def build_hub_embed(bot: commands.Bot, guild: discord.Guild) -> discord.Embed:
    """The Link Sync control-hub overview."""
    embed = discord.Embed(
        title="🔗 Link Sync • Channel → Google Sheet",
        description=(
            "Mirrors every link shared in the watched channel into the Google "
            "Sheet — **A:** handle · **B:** display name · **C:** link. New links "
            "post **instantly**; a sweep every "
            f"**{config.LINKSYNC_SWEEP_MINUTES} min** catches anything missed. "
            "The same URL is never written twice."
        ),
        color=config.COLOR_BRAND,
    )

    ch = guild.get_channel(LISTEN_CHANNEL_ID)
    embed.add_field(
        name="📡 Watching",
        value=(ch.mention if ch else f"<#{LISTEN_CHANNEL_ID}>")
              + f"\n[Open the Google Sheet]({_sheet_url()})",
        inline=False,
    )

    # Connection / config status.
    if not _sheet.configured:
        conn = (
            "🔴 **Not configured.** Set `LINKSYNC_WEBHOOK_URL` (and "
            "`LINKSYNC_WEBHOOK_SECRET`) in `.env` — see `docs/LINK_SYNC.md`. "
            "Until then nothing is written."
        )
    else:
        conn = "🟢 Web app configured. Use **Test Connection** to verify access."
    paused = await is_paused(bot, guild.id)
    if paused:
        conn += "\n⏸️ **Paused** — links are not being captured."
    embed.add_field(name="🔌 Status", value=conn, inline=False)

    # Live stats.
    total = await bot.database.fetch_one(
        "SELECT COUNT(*) AS c FROM link_sync_log WHERE guild_id = ?", (guild.id,)
    )
    from_msgs = await bot.database.fetch_one(
        "SELECT COUNT(*) AS c FROM link_sync_log WHERE guild_id = ? AND message_id > 0",
        (guild.id,),
    )
    last_sweep = await _get_meta(bot, guild.id, "linksync_last_sweep")
    when = "never"
    if last_sweep:
        try:
            ts = int(datetime.datetime.fromisoformat(last_sweep).timestamp())
            when = f"<t:{ts}:R>"
        except Exception:
            when = last_sweep
    excluded_str = " ".join(f"<@&{rid}>" for rid in EXCLUDED_ROLE_IDS) or "none"
    embed.add_field(
        name="📊 Synced So Far",
        value=(
            f"```\n"
            f"Total links logged │ {total['c'] if total else 0}\n"
            f"From this channel  │ {from_msgs['c'] if from_msgs else 0}\n"
            f"```"
            f"Last sweep: {when}\n"
            f"Excluded roles (skipped): {excluded_str}"
        ),
        inline=False,
    )

    embed.add_field(
        name="🛠️ Actions",
        value=(
            "**Sweep Now** — scan the channel for any links not yet in the sheet.\n"
            "**Backfill From Sheet** — record links already in the sheet so they're never re-added.\n"
            "**Test Connection** — verify the bot can reach the sheet.\n"
            "**Pause / Resume** — temporarily stop or restart capturing."
        ),
        inline=False,
    )
    embed.set_footer(text="IM8 Health • Link Sync")
    return embed


# ═══════════════════════════════════════════════
#  Hub UI
# ═══════════════════════════════════════════════

def _stats_summary(stats: dict) -> str:
    if stats.get("error"):
        return f"⚠️ {stats['error']}"
    parts = [
        f"📥 Appended **{stats['appended']}** new link(s)",
        f"🔍 Scanned {stats['scanned']} message(s)",
    ]
    if stats.get("excluded"):
        parts.append(f"🚫 Skipped {stats['excluded']} excluded-role message(s)")
    if stats.get("errors"):
        parts.append(f"❌ {stats['errors']} write error(s) — will retry next sweep")
    return "\n".join(parts)


class LinkSyncHubView(discord.ui.View):
    """The Link Sync control hub, opened from the Mod Panel."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def _refresh_hub(self, interaction: discord.Interaction) -> None:
        try:
            embed = await build_hub_embed(interaction.client, interaction.guild)
            await interaction.message.edit(content=None, embed=embed, view=LinkSyncHubView())
        except Exception as e:
            logger.error(f"Link Sync: failed to refresh hub: {e}")

    @discord.ui.button(label="Sweep Now", emoji="🧹", style=discord.ButtonStyle.success,
                       row=0, custom_id="im8_linksync_sweep")
    async def btn_sweep(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _sheet.configured:
            return await interaction.response.send_message(
                "🔴 Not configured — set `LINKSYNC_WEBHOOK_URL` first (see `docs/LINK_SYNC.md`).",
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True)
        stats = await sweep(interaction.client, interaction.guild)
        await interaction.followup.send(_stats_summary(stats), ephemeral=True)
        await self._refresh_hub(interaction)

    @discord.ui.button(label="Backfill From Sheet", emoji="📥", style=discord.ButtonStyle.secondary,
                       row=0, custom_id="im8_linksync_backfill")
    async def btn_backfill(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _sheet.configured:
            return await interaction.response.send_message(
                "🔴 Not configured — set `LINKSYNC_WEBHOOK_URL` first.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        try:
            n = await seed_from_sheet(interaction.client, interaction.guild)
            await interaction.followup.send(
                f"📥 Recorded **{n}** link(s) already in the sheet — they won't be re-added.",
                ephemeral=True,
            )
        except SheetSyncError as e:
            await interaction.followup.send(f"⚠️ Could not read the sheet: {e}", ephemeral=True)
        await self._refresh_hub(interaction)

    @discord.ui.button(label="Test Connection", emoji="🔌", style=discord.ButtonStyle.secondary,
                       row=0, custom_id="im8_linksync_test")
    async def btn_test(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _sheet.configured:
            return await interaction.response.send_message(
                "🔴 Not configured — set `LINKSYNC_WEBHOOK_URL` in `.env`.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        try:
            data = await _sheet.ping()
            await interaction.followup.send(
                f"🟢 Connected — writing to sheet **{data.get('sheet', '?')}**.", ephemeral=True
            )
        except SheetSyncError as e:
            await interaction.followup.send(f"🔴 Connection failed: {e}", ephemeral=True)

    @discord.ui.button(label="Pause / Resume", emoji="⏯️", style=discord.ButtonStyle.secondary,
                       row=1, custom_id="im8_linksync_pause")
    async def btn_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        paused = await is_paused(interaction.client, interaction.guild.id)
        await _set_meta(interaction.client, interaction.guild.id,
                        "linksync_paused", "0" if paused else "1")
        await interaction.response.send_message(
            "▶️ Resumed — capturing links again." if paused else "⏸️ Paused — links won't be captured.",
            ephemeral=True,
        )
        await self._refresh_hub(interaction)

    @discord.ui.button(label="Back to Main Panel", emoji="🔙", style=discord.ButtonStyle.secondary,
                       row=1, custom_id="im8_linksync_back")
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.panel import ModPanelView, Panel
        embed = await Panel.build_panel_embed(interaction.client, interaction.guild)
        await interaction.response.edit_message(content=None, embed=embed, view=ModPanelView())


# ═══════════════════════════════════════════════
#  Cog
# ═══════════════════════════════════════════════

class LinkSyncCog(commands.Cog):
    """Mirrors shared links from the watched channel into a Google Sheet."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._startup_done = False
        self._startup_task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        self.bot.add_view(LinkSyncHubView())
        self.bot.scheduler.add_interval_job(
            self._scheduled_sweep,
            job_id="linksync_sweep",
            minutes=config.LINKSYNC_SWEEP_MINUTES,
        )
        state = "configured" if _sheet.configured else "INERT (LINKSYNC_WEBHOOK_URL unset)"
        logger.info(f"Link Sync loaded; sweep every {config.LINKSYNC_SWEEP_MINUTES}min — {state}.")

    async def cog_unload(self) -> None:
        if self._startup_task and not self._startup_task.done():
            self._startup_task.cancel()

    # ── Live capture ──
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if message.channel.id != LISTEN_CHANNEL_ID:
            return
        if not _sheet.configured:
            return
        if _excluded(message.author):
            return
        candidates = _extract_links(message.content)
        if not candidates:
            return
        if await is_paused(self.bot, message.guild.id):
            return
        try:
            n = await _sync_message(self.bot, message, candidates)
            if n:
                try:
                    await message.add_reaction(SYNC_EMOJI)
                except Exception:
                    pass
        except SheetSyncError as e:
            logger.warning(f"Link Sync: live append failed (will retry on sweep): {e}")
        except Exception as e:
            logger.error(f"Link Sync: on_message error: {e}")

    # ── Startup catch-up ──
    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._startup_done:
            return
        self._startup_done = True
        self._startup_task = _spawn(self._delayed_startup())

    async def _delayed_startup(self) -> None:
        await asyncio.sleep(20)  # let the gateway + member cache settle
        if not _sheet.configured:
            return
        try:
            for guild in self.bot.guilds:
                if guild.get_channel(LISTEN_CHANNEL_ID) is None:
                    continue
                # Seed the dedup set from existing sheet rows once, so the first
                # sweep never re-adds links that are already there.
                if await _get_meta(self.bot, guild.id, "linksync_seeded") != "1":
                    try:
                        await seed_from_sheet(self.bot, guild)
                    except SheetSyncError as e:
                        logger.warning(f"Link Sync: initial seed failed for {guild.id}: {e}")
                try:
                    await sweep(self.bot, guild)
                except Exception as e:
                    logger.error(f"Link Sync: startup sweep failed for {guild.id}: {e}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Link Sync: startup task failed: {e}")

    async def _scheduled_sweep(self) -> None:
        if not _sheet.configured:
            return
        for guild in self.bot.guilds:
            if guild.get_channel(LISTEN_CHANNEL_ID) is None:
                continue
            try:
                await sweep(self.bot, guild)
            except Exception as e:
                logger.error(f"Link Sync: scheduled sweep failed for {guild.id}: {e}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LinkSyncCog(bot))
