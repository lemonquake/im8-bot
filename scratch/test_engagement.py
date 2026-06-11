import asyncio, os, sys, tempfile, shutil, datetime
sys.path.insert(0, os.getcwd())
from core.database import Database
import cogs.active as active

class FakeGuild:
    id = 999
    def get_member(self, uid):
        return None  # nobody cached -> nobody excluded

async def main():
    tmp = tempfile.mkdtemp()
    db = Database(os.path.join(tmp, "t.db"))
    await db.connect()
    class FakeBot: database = db
    bot = FakeBot()
    g = FakeGuild()
    today = datetime.datetime.now(datetime.timezone.utc).date()
    tracked = active._tracked_ids()[0]
    d = lambda n: (today - datetime.timedelta(days=n)).isoformat()

    # user 1: 10 msgs today; user 2: 3 msgs + 5 reactions 5 days ago;
    # user 3: activity 40 days ago (outside weekly+monthly); untracked channel noise
    await active._bump(bot, g.id, tracked, 1, d(0), d_msg=10)
    await active._bump(bot, g.id, tracked, 2, d(5), d_msg=3, d_react=5)
    await active._bump(bot, g.id, tracked, 3, d(40), d_msg=7)
    await active._bump(bot, g.id, 12345, 4, d(0), d_msg=99)  # untracked channel

    for tf, spec in active.TIMEFRAMES.items():
        ranked = await active.compute_engagement(bot, g, spec["days"])
        print(f"{tf:<8}", [(u, x["points"]) for u, x in ranked])

    stats = await active.get_member_stats(bot, g, 2)
    print("member2 weekly:", stats["timeframes"]["weekly"])
    print("spark:", active._sparkline(stats["series"]))
    ins = await active.get_channel_insights(bot, g)
    print("insights:", ins["channels"], ins["busiest"])
    print("coverage:", await active.get_coverage_start(bot, g.id))
    await db.close()
    shutil.rmtree(tmp, ignore_errors=True)
    print("FUNCTIONAL TEST OK")

asyncio.run(main())
