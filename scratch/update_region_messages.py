"""
One-off: refresh all live Region Role messages after adding Oceania.

Edits each tracked message's embed (regenerated from the current REGIONS
config) and seeds the new region's reaction. Safe to re-run.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import sqlite3

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from cogs.regionrole import REGIONS  # noqa: E402

load_dotenv(r"A:\Python\im8-bot\.env")
TOKEN = os.getenv("DISCORD_TOKEN")
API = "https://discord.com/api/v10"
HEADERS = {
    "Authorization": f"Bot {TOKEN}",
    "User-Agent": "DiscordBot (https://github.com/im8, 1.0)",
}

GUILD_ID = 1484912406743220347
TARGET_MESSAGE_ID = 1515735750237229087
NEW_EMOJIS = ["🦘"]  # reactions to seed on existing posts


def request(method: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = dict(HEADERS)
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{API}{path}", data=data, method=method, headers=headers)
    while True:
        try:
            with urllib.request.urlopen(req) as resp:
                body = resp.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry = json.loads(e.read()).get("retry_after", 1)
                print(f"  rate limited, sleeping {retry}s")
                time.sleep(float(retry) + 0.5)
                continue
            raise


def build_embed() -> dict:
    """Mirror of cogs.regionrole.build_region_embed, as raw JSON."""
    lines = [f"{r['emoji']}  →  <@&{r['role_id']}>" for r in REGIONS]
    return {
        "title": "🌍 Select Your Region",
        "description": (
            "React with the emoji for the part of the world you're in to receive "
            "your region role.\n\n"
            "• You can only pick **one** region — reacting with a different one "
            "switches you over automatically.\n"
            "• Remove your reaction to clear your region role.\n"
            "• Any other reactions are removed automatically.\n\n"
            + "\n".join(lines)
        ),
        "color": 0x00C9A7,
        "footer": {"text": "IM8 Health • Region Roles"},
    }


def find_message_channel(guild_id: int, message_id: int) -> int | None:
    """Fetches all channels in the guild and scans them for the specified message."""
    print(f"Fetching channels for guild {guild_id}...")
    try:
        channels = request("GET", f"/guilds/{guild_id}/channels")
    except Exception as e:
        print(f"Failed to fetch channels from Discord API: {e}")
        return None

    # Filter text channels (type 0)
    text_channels = [c for c in channels if c.get("type") == 0]
    print(f"Scanning {len(text_channels)} text channels for message {message_id}...")

    for c in text_channels:
        channel_id = int(c["id"])
        channel_name = c.get("name", "")
        try:
            msg = request("GET", f"/channels/{channel_id}/messages/{message_id}")
            if msg:
                print(f"  -> Found message in #{channel_name} ({channel_id})!")
                return channel_id
        except Exception:
            pass
        time.sleep(0.1)
    return None


def main() -> None:
    db_path = os.path.join(os.path.dirname(__file__), "..", "data", "im8bot.db")
    
    # 1. Fetch currently tracked posts from database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id, message_id FROM region_role_messages WHERE guild_id = ?", (GUILD_ID,))
    tracked = cursor.fetchall()
    
    # Map messages to channel
    messages_to_update = {msg_id: ch_id for ch_id, msg_id in tracked}
    
    # 2. Check if TARGET_MESSAGE_ID is already tracked. If not, discover its channel.
    if TARGET_MESSAGE_ID not in messages_to_update:
        print(f"Target message {TARGET_MESSAGE_ID} is not in database. Attempting discovery...")
        channel_id = find_message_channel(GUILD_ID, TARGET_MESSAGE_ID)
        if channel_id:
            messages_to_update[TARGET_MESSAGE_ID] = channel_id
            # Save it to the database so the bot tracks reactions on it
            cursor.execute(
                "INSERT OR REPLACE INTO region_role_messages (message_id, guild_id, channel_id, created_at) "
                "VALUES (?, ?, ?, datetime('now'))",
                (TARGET_MESSAGE_ID, GUILD_ID, channel_id)
            )
            conn.commit()
            print(f"Successfully added message {TARGET_MESSAGE_ID} in channel {channel_id} to database tracking.")
        else:
            print(f"Could not find channel for message {TARGET_MESSAGE_ID}. It will not be updated.")
    
    conn.close()

    if not messages_to_update:
        print("No messages found to update.")
        return

    # 3. Update the messages
    embed = build_embed()
    for message_id, channel_id in messages_to_update.items():
        print(f"Updating message {message_id} in channel {channel_id}...")
        try:
            request("PATCH", f"/channels/{channel_id}/messages/{message_id}", {"embeds": [embed]})
            print("  embed updated successfully")
            for emoji in NEW_EMOJIS:
                quoted = urllib.parse.quote(emoji)
                request("PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{quoted}/@me")
                print(f"  reaction {emoji} added")
                time.sleep(0.5)
        except Exception as e:
            print(f"  Failed to update message {message_id} in channel {channel_id}: {e}")
        time.sleep(0.5)

    print("Done.")


if __name__ == "__main__":
    main()

