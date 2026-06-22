"""
One-off: cross-check affiliate "Discord username" values from the IM8 SS Export
against the live Discord server roster.

Read-only against Discord (REST fetch_members), touches nothing in the DB.
Safe to run while the main bot is up — a second gateway login is allowed.

Outputs:
  - console summary (matched / not matched / junk)
  - CSV: A:\\Discord Mod\\IM8\\discord_crosscheck_results.csv
"""

import asyncio
import csv
import sys

import discord
import openpyxl

sys.path.insert(0, ".")
import config

GUILD_ID = 1484912406743220347
XLSX = r"A:\Discord Mod\IM8\SS Export_ 16-Jun.xlsx"
OUT_CSV = r"A:\Discord Mod\IM8\discord_crosscheck_results.csv"
DISCORD_COL = "Custom property - Your Discord username"


def norm(s) -> str:
    """Normalize a handle for comparison: strip, drop leading @, trailing /, casefold."""
    if s is None:
        return ""
    s = str(s).strip().lstrip("@").rstrip("/").strip()
    return s.casefold()


def looks_junk(v: str) -> bool:
    """Heuristic: the cell isn't a plausible Discord handle (email/url/sentence/none)."""
    vl = v.lower().strip()
    if vl in ("none", "n/a", "na", "-", "/", "tbd", "no", "nil", "x", "."):
        return True
    if "@" in v and "." in v and " " not in v:  # email
        return True
    if any(t in vl for t in ("http", "substack", "instagram.com", "tiktok.com", ".com", "www.")):
        return True
    if len(v) > 40:  # free-text sentence
        return True
    return False


# ── Load affiliate rows with a Discord-username value ─────────────────
wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
ws = wb.active
rows = list(ws.iter_rows(values_only=True))
hdr = rows[0]
di = hdr.index(DISCORD_COL)

affiliates = []
for r in rows[1:]:
    val = r[di] if di < len(r) else None
    if val in (None, ""):
        continue
    affiliates.append(
        {
            "first": r[1] or "",
            "last": r[2] or "",
            "email": r[3] or "",
            "discord_raw": str(val).strip(),
        }
    )

print(f"Affiliate rows with a Discord-username value: {len(affiliates)}")

# ── Connect, fetch roster, match ──────────────────────────────────────
intents = discord.Intents.none()
intents.guilds = True
intents.members = True
client = discord.Client(intents=intents)

results = []


@client.event
async def on_ready():
    try:
        guild = client.get_guild(GUILD_ID)
        if guild is None:
            print("GUILD NOT FOUND. Bot is in:", [(g.id, g.name) for g in client.guilds])
            return
        print(f"Guild: {guild.name} ({guild.id}) — member_count={guild.member_count}")

        members = [m async for m in guild.fetch_members(limit=None)]
        print(f"Fetched {len(members)} members from Discord")

        by_user: dict[str, list] = {}   # exact handle (strong)
        by_other: dict[str, list] = {}  # global_name / nick / display_name (weak)
        for m in members:
            by_user.setdefault(norm(m.name), []).append(m)
            for cand in (getattr(m, "global_name", None), m.nick, m.display_name):
                if cand:
                    by_other.setdefault(norm(cand), []).append(m)

        for a in affiliates:
            raw = a["discord_raw"]
            a["junk"] = looks_junk(raw)
            key = norm(raw)
            key_nd = key.split("#")[0]  # tolerate legacy name#1234

            hit = by_user.get(key) or by_user.get(key_nd)
            field = "username"
            if not hit:
                hit = by_other.get(key) or by_other.get(key_nd)
                field = "display/nick"

            if hit:
                m = hit[0]
                a.update(
                    matched=True,
                    match_field=field,
                    match_user=m.name,
                    match_display=m.display_name,
                    match_id=str(m.id),
                    ambiguous=(len(hit) > 1),
                )
            else:
                a.update(
                    matched=False, match_field="", match_user="",
                    match_display="", match_id="", ambiguous=False,
                )
            results.append(a)
    finally:
        await client.close()


async def main():
    async with client:
        await client.start(config.DISCORD_TOKEN)


asyncio.run(main())

# ── Report ────────────────────────────────────────────────────────────
matched = [a for a in results if a["matched"]]
strong = [a for a in matched if a["match_field"] == "username"]
weak = [a for a in matched if a["match_field"] == "display/nick"]
not_matched = [a for a in results if not a["matched"] and not a["junk"]]
junk = [a for a in results if not a["matched"] and a["junk"]]

print("\n" + "=" * 70)
print(f"RESULTS — {len(results)} affiliate Discord values checked")
print("=" * 70)
print(f"  IN SERVER (handle match) ...... {len(strong)}")
print(f"  IN SERVER (display/nick only) . {len(weak)}  (verify — weaker)")
print(f"  NOT in server ................. {len(not_matched)}")
print(f"  Unusable / not a handle ....... {len(junk)}")

print("\n--- IN SERVER (strong handle matches) ---")
for a in sorted(strong, key=lambda x: x["discord_raw"].lower()):
    amb = "  ⚠ multiple matches" if a["ambiguous"] else ""
    print(f"  {a['discord_raw']:<28} -> @{a['match_user']} (display: {a['match_display']}) "
          f"| {a['first']} {a['last']}{amb}")

print("\n--- IN SERVER (display-name / nickname only — verify) ---")
for a in sorted(weak, key=lambda x: x["discord_raw"].lower()):
    amb = "  ⚠ multiple matches" if a["ambiguous"] else ""
    print(f"  {a['discord_raw']:<28} -> @{a['match_user']} (display: {a['match_display']}) "
          f"| {a['first']} {a['last']}{amb}")

# ── CSV ───────────────────────────────────────────────────────────────
with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["discord_raw", "in_server", "match_confidence", "matched_username",
                "matched_display", "matched_id", "ambiguous",
                "affiliate_first", "affiliate_last", "affiliate_email", "unusable_value"])
    for a in results:
        conf = "" if not a["matched"] else ("handle" if a["match_field"] == "username" else "display/nick")
        w.writerow([
            a["discord_raw"], "YES" if a["matched"] else "NO", conf,
            a["match_user"], a["match_display"], a["match_id"],
            "YES" if a["ambiguous"] else "", a["first"], a["last"], a["email"],
            "YES" if a["junk"] else "",
        ])
print(f"\nFull results written to: {OUT_CSV}")
