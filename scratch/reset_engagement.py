"""
One-off: reset the Most Active engagement period to a fresh zero.

Wipes every row from ``engagement_daily`` (all members → 0 points) and stamps
``engagement_period_start`` at the exact reset instant so the startup catch-up
and deep backfill scans can never re-import anything from before now. June 15,
2026 becomes day 1 of the new engagement period.

Scope: Most Active engagement only. Retention cohorts (member_activity_weeks /
cohort_start) and member-growth/join data are intentionally left untouched.

Run with the bot stopped.
"""

import asyncio
import datetime
import sqlite3

DB_PATH = "data/im8bot.db"


def main() -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    period_start = now.isoformat()          # full instant — scans floor on this
    today = now.date().isoformat()          # 'YYYY-MM-DD'

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    cur = db.cursor()

    before = cur.execute(
        "SELECT COUNT(*) c, COUNT(DISTINCT user_id) u, MIN(day) mn, MAX(day) mx "
        "FROM engagement_daily"
    ).fetchone()
    print(f"BEFORE  rows={before['c']}  users={before['u']}  days={before['mn']}..{before['mx']}")

    # Guilds to stamp: any with engagement rows or existing analytics_meta.
    guilds = {r[0] for r in cur.execute("SELECT DISTINCT guild_id FROM engagement_daily")}
    guilds |= {r[0] for r in cur.execute("SELECT DISTINCT guild_id FROM analytics_meta")}

    # 1) Wipe all engagement to a true zero.
    cur.execute("DELETE FROM engagement_daily")

    # 2) Mark the new period start + reset the live/catch-up bookkeeping so the
    #    next boot's scans start from this instant, not the old history.
    for gid in guilds:
        for key, value in (
            ("engagement_period_start", period_start),
            ("engagement_live_since", today),
            ("engagement_catchup_through", today),
        ):
            cur.execute(
                "INSERT OR REPLACE INTO analytics_meta (guild_id, key, value) VALUES (?, ?, ?)",
                (gid, key, value),
            )

    db.commit()
    # Fold the WAL back into the main db file so the backup/copy is self-contained.
    cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.commit()

    after = cur.execute("SELECT COUNT(*) c FROM engagement_daily").fetchone()
    print(f"AFTER   rows={after['c']}  (stamped {len(guilds)} guild(s))")
    print(f"period_start = {period_start}")
    print(f"live_since / catchup_through = {today}")
    for r in cur.execute(
        "SELECT guild_id, key, value FROM analytics_meta "
        "WHERE key LIKE 'engagement_%' ORDER BY guild_id, key"
    ):
        v = r["value"]
        if v and len(v) > 80:
            v = v[:80] + "..."
        print(f"  meta g={r['guild_id']}  {r['key']} = {v}")

    db.close()


if __name__ == "__main__":
    # asyncio import kept for parity with sibling scripts; logic is sync sqlite.
    _ = asyncio
    main()
