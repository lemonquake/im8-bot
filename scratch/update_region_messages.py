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

# (channel_id, message_id) of every live Region Role post, from the
# region_role_messages table.
MESSAGES = [
    (1494711570113233157, 1513936248467755221),
    (1484912407447994443, 1513936351870062614),
    (1512410695109578814, 1513983986026811412),
    (1512410695109578814, 1514284418079133757),
    (1484912407447994439, 1514342448384446634),
    (1484912407447994439, 1514398570038562926),
    (1512410695109578814, 1514656366323564625),
    (1484912407447994439, 1514764120564699271),
]

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


def main() -> None:
    embed = build_embed()
    for channel_id, message_id in MESSAGES:
        print(f"Message {message_id} in #{channel_id}:")
        request("PATCH", f"/channels/{channel_id}/messages/{message_id}", {"embeds": [embed]})
        print("  embed updated")
        for emoji in NEW_EMOJIS:
            quoted = urllib.parse.quote(emoji)
            request("PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{quoted}/@me")
            print(f"  reaction {emoji} added")
            time.sleep(0.5)
        time.sleep(0.5)
    print("Done.")


if __name__ == "__main__":
    main()
