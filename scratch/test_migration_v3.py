import asyncio, os, sys, tempfile, shutil

sys.path.insert(0, os.getcwd())
from core.database import Database

async def main():
    tmp = tempfile.mkdtemp()
    src = os.path.join("data", "im8bot.db")
    dbpath = os.path.join(tmp, "test.db")
    copied = False
    if os.path.exists(src):
        shutil.copyfile(src, dbpath)
        copied = True
    db = Database(dbpath)
    await db.connect()
    rows = await db.fetch_all("SELECT version, name FROM schema_migrations ORDER BY version")
    print("migrations:", [(r["version"], r["name"]) for r in rows])
    # engagement_daily exists and accepts the upsert patterns used by the cog
    await db.execute(
        "INSERT INTO engagement_daily (guild_id, channel_id, user_id, day, messages, reactions) "
        "VALUES (1, 2, 3, '2026-06-11', 1, 0) "
        "ON CONFLICT(guild_id, channel_id, user_id, day) DO UPDATE SET "
        "messages = messages + excluded.messages, reactions = reactions + excluded.reactions")
    await db.execute(
        "UPDATE engagement_daily SET messages = MAX(messages + ?, 0), reactions = MAX(reactions + ?, 0) "
        "WHERE guild_id = 1 AND channel_id = 2 AND user_id = 3 AND day = '2026-06-11'", (-5, -1))
    row = await db.fetch_one("SELECT * FROM engagement_daily")
    print("row after floor-test:", dict(row))
    if copied:
        legacy = await db.fetch_all("SELECT * FROM active_leaderboard")
        carried = await db.fetch_all("SELECT * FROM active_leaderboards")
        print("legacy boards:", [dict(r) for r in legacy])
        print("carried boards:", [dict(r) for r in carried])
    await db.close()
    shutil.rmtree(tmp, ignore_errors=True)
    print("MIGRATION TEST OK (copied real db:", copied, ")")

asyncio.run(main())
