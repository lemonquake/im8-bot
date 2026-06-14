import sys, os, datetime
sys.path.insert(0, os.getcwd())

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import cogs.active as active

class FakeGuild:
    id = 999
    icon = None
    def get_member(self, uid): return None
    def get_channel(self, cid): return None

# View layout validation (raises if rows overflow / weights invalid)
hub = active.MostActiveHubView()
print("hub components:", len(hub.children))
picker = active.MemberStatsView()
print("picker components:", len(picker.children))

g = FakeGuild()
ranked = [(i, {"points": 100 - i, "messages": 5, "reactions": 3}) for i in range(10)]
e = active.build_leaderboard_embed(g, ranked, "alltime", coverage="2026-03-01")
print("leaderboard field len:", len(e.fields[0].value), "desc:", e.description.splitlines()[0])
e2 = active.build_leaderboard_embed(g, [], "daily")
print("empty board ok:", e2.fields[0].name)

stats = {
    "timeframes": {"daily": None,
                   "weekly": {"rank": 2, "of": 40, "points": 1250, "messages": 100, "reactions": 50},
                   "monthly": {"rank": 1, "of": 90, "points": 99999, "messages": 9999, "reactions": 999},
                   "alltime": {"rank": 1, "of": 120, "points": 123456, "messages": 20000, "reactions": 5000}},
    "series": [(f"2026-06-{i:02d}", i * 10) for i in range(1, 15)],
}
class FakeAvatar: url = "https://example.com/a.png"
class FakeUser:
    id = 7; mention = "<@7>"; display_avatar = FakeAvatar()
e3 = active.build_member_stats_embed(g, FakeUser(), stats)
print("stats table:")
print(e3.fields[0].value)
print("table field len:", len(e3.fields[0].value))

ins = {"days": 30, "channels": [{"channel_id": 1, "m": 500, "r": 200, "members": 33}], "busiest": {"day": "2026-06-01", "m": 80}}
e4 = active.build_channel_insights_embed(g, ins)
print("insights ok:", e4.fields[0].value[:60])
print("EMBED SMOKE OK")
