"""Deterministic test for migration v7.

Builds a pre-v7 ``active_leaderboards`` (the v3+v4 schema: PK (guild, timeframe)
+ range columns, no ``source``) from scratch, applies the real v7 statements
from the migration registry, and asserts the transform. Self-contained — it does
NOT read the live DB, so it can't be thrown off by boards mods adopt at runtime.

Covers:
  • PK re-keyed to (guild_id, message_id) and a 'source' column added.
  • Pre-v7 rows carried forward tagged 'post', custom ranges preserved.
  • Multiple boards of the SAME timeframe coexist (different channels/messages).
  • deploy_board's channel-scoped replace: re-posting a timeframe to one channel
    replaces only that channel's post board; other channels + adopted boards live.
"""
import asyncio, os, sys, tempfile, shutil

sys.path.insert(0, os.getcwd())
import aiosqlite
from core import migrations

# active_leaderboards as it stood after migrations v3 + v4 (the pre-v7 shape).
PRE_V7 = """
    CREATE TABLE active_leaderboards (
        guild_id    INTEGER NOT NULL,
        timeframe   TEXT NOT NULL,
        channel_id  INTEGER NOT NULL,
        message_id  INTEGER NOT NULL,
        updated_at  TEXT DEFAULT (datetime('now')),
        range_start TEXT,
        range_end   TEXT,
        PRIMARY KEY (guild_id, timeframe)
    )
"""


def v7_statements():
    for version, name, stmts in migrations.MIGRATIONS:
        if version == 7:
            assert name == "active_leaderboards_multi_board", name
            return stmts
    raise SystemExit("v7 migration not found in registry")


async def main():
    tmp = tempfile.mkdtemp()
    db = await aiosqlite.connect(os.path.join(tmp, "t.db"))
    db.row_factory = aiosqlite.Row

    # Seed a pre-v7 DB: the old PK only allowed one board per timeframe.
    await db.execute(PRE_V7)
    await db.execute(
        "INSERT INTO active_leaderboards (guild_id, timeframe, channel_id, message_id, range_start, range_end) "
        "VALUES (1, 'weekly', 10, 100, NULL, NULL)")
    await db.execute(
        "INSERT INTO active_leaderboards (guild_id, timeframe, channel_id, message_id, range_start, range_end) "
        "VALUES (1, 'custom', 11, 101, '2026-05-01', '2026-05-31')")
    await db.commit()

    # Apply v7 exactly as Database._run_migrations would.
    for sql in v7_statements():
        await db.execute(sql)
    await db.commit()

    # Schema: source column present, PK is (guild_id, message_id).
    cols = await (await db.execute("PRAGMA table_info(active_leaderboards)")).fetchall()
    colnames = [c["name"] for c in cols]
    pk = [c["name"] for c in sorted(cols, key=lambda c: c["pk"]) if c["pk"]]
    print("columns:", colnames, "| pk:", pk)
    assert "source" in colnames, "source column missing"
    assert pk == ["guild_id", "message_id"], f"unexpected PK: {pk}"

    # Carry-forward: both pre-v7 rows preserved, tagged 'post', ranges intact.
    carried = [dict(r) for r in await (await db.execute(
        "SELECT timeframe, message_id, source, range_start FROM active_leaderboards ORDER BY message_id")).fetchall()]
    print("carried:", carried)
    assert len(carried) == 2, "both pre-v7 rows should carry forward"
    assert all(r["source"] == "post" for r in carried), "carried rows should be tagged 'post'"
    assert carried[1]["range_start"] == "2026-05-01", "custom range should be preserved"

    # New capability: multiple boards, SAME timeframe, different channels coexist.
    await db.execute("INSERT INTO active_leaderboards (guild_id, timeframe, channel_id, message_id, source) "
                     "VALUES (1, 'weekly', 20, 200, 'post')")
    await db.execute("INSERT INTO active_leaderboards (guild_id, timeframe, channel_id, message_id, source) "
                     "VALUES (1, 'weekly', 21, 201, 'adopt')")
    await db.commit()
    c = (await (await db.execute(
        "SELECT COUNT(*) AS c FROM active_leaderboards WHERE guild_id=1 AND timeframe='weekly'")).fetchone())["c"]
    assert c == 3, f"expected 3 weekly boards (100/200/201), got {c}"

    # deploy_board channel-scoped replace: re-post weekly to channel 20 replaces
    # ONLY channel 20's post board (200 -> 202); 100 (ch10) and 201 (adopt) live.
    await db.execute("DELETE FROM active_leaderboards "
                     "WHERE guild_id=1 AND timeframe='weekly' AND channel_id=20 AND source='post'")
    await db.execute("INSERT INTO active_leaderboards (guild_id, timeframe, channel_id, message_id, source) "
                     "VALUES (1, 'weekly', 20, 202, 'post')")
    await db.commit()
    ids = {r["message_id"] for r in await (await db.execute(
        "SELECT message_id FROM active_leaderboards WHERE guild_id=1 AND timeframe='weekly'")).fetchall()}
    print("weekly ids after channel-scoped re-post:", ids)
    assert ids == {100, 201, 202}, f"expected 200 replaced by 202, others intact; got {ids}"

    await db.close()
    shutil.rmtree(tmp, ignore_errors=True)
    print("MIGRATION v7 TEST OK")


asyncio.run(main())
