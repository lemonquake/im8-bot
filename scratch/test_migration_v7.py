"""Verifies migration v7 (active_leaderboards re-keyed to (guild_id, message_id)
+ source column). Runs against a COPY of the real DB if present, so production
data is never touched."""
import asyncio, os, sys, tempfile, shutil

sys.path.insert(0, os.getcwd())
from core.database import Database


async def main():
    tmp = tempfile.mkdtemp()
    src = os.path.join("data", "im8bot.db")
    dbpath = os.path.join(tmp, "test.db")
    copied = False
    if os.path.exists(src):
        # Copy the WAL/SHM too so the snapshot includes un-checkpointed writes.
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(src + suffix):
                shutil.copyfile(src + suffix, dbpath + suffix)
        copied = True

    db = Database(dbpath)
    await db.connect()

    # v7 recorded?
    migs = await db.fetch_all("SELECT version, name FROM schema_migrations ORDER BY version")
    print("migrations:", [(r["version"], r["name"]) for r in migs])
    assert any(r["version"] == 7 for r in migs), "v7 not applied!"

    # Schema: source column present, PK is (guild_id, message_id).
    cols = await db.fetch_all("PRAGMA table_info(active_leaderboards)")
    colnames = [c["name"] for c in cols]
    pk = [c["name"] for c in sorted(cols, key=lambda c: c["pk"]) if c["pk"]]
    print("columns:", colnames)
    print("primary key:", pk)
    assert "source" in colnames, "source column missing"
    assert pk == ["guild_id", "message_id"], f"unexpected PK: {pk}"

    # Existing rows carried forward tagged 'post'.
    carried = await db.fetch_all("SELECT timeframe, message_id, source FROM active_leaderboards")
    print("carried boards:", [dict(r) for r in carried])
    assert all(r["source"] == "post" for r in carried), "carried rows should be 'post'"

    # New capability: two boards, SAME timeframe, different message ids coexist.
    G = 999999001
    await db.execute(
        "INSERT OR REPLACE INTO active_leaderboards "
        "(guild_id, timeframe, channel_id, message_id, range_start, range_end, source, updated_at) "
        "VALUES (?, 'weekly', 10, 1001, NULL, NULL, 'post', datetime('now'))", (G,))
    await db.execute(
        "INSERT OR REPLACE INTO active_leaderboards "
        "(guild_id, timeframe, channel_id, message_id, range_start, range_end, source, updated_at) "
        "VALUES (?, 'weekly', 11, 1002, NULL, NULL, 'adopt', datetime('now'))", (G,))
    both = await db.fetch_all("SELECT message_id, source FROM active_leaderboards WHERE guild_id = ?", (G,))
    print("two weekly boards:", [dict(r) for r in both])
    assert len(both) == 2, "PK should allow multiple boards per timeframe now"

    # deploy_board semantics: a new Post of 'weekly' replaces only the prior POST
    # board, leaving the adopted weekly board untouched.
    await db.execute(
        "DELETE FROM active_leaderboards WHERE guild_id = ? AND timeframe = 'weekly' AND source = 'post'", (G,))
    await db.execute(
        "INSERT OR REPLACE INTO active_leaderboards "
        "(guild_id, timeframe, channel_id, message_id, range_start, range_end, source, updated_at) "
        "VALUES (?, 'weekly', 12, 1003, NULL, NULL, 'post', datetime('now'))", (G,))
    after = await db.fetch_all(
        "SELECT message_id, source FROM active_leaderboards WHERE guild_id = ? ORDER BY message_id", (G,))
    print("after re-post:", [dict(r) for r in after])
    ids = {r["message_id"] for r in after}
    assert ids == {1002, 1003}, f"expected adopt 1002 + new post 1003, got {ids}"

    # cleanup test rows
    await db.execute("DELETE FROM active_leaderboards WHERE guild_id = ?", (G,))
    await db.close()
    shutil.rmtree(tmp, ignore_errors=True)
    print("MIGRATION v7 TEST OK (copied real db:", copied, ")")


asyncio.run(main())
