import asyncio
import sys
import datetime
from unittest.mock import AsyncMock, MagicMock

# Add project root to path
sys.path.append(".")

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Mock database and bot
class MockDatabase:
    def __init__(self):
        self.fetch_one = AsyncMock()
        self.fetch_all = AsyncMock()
        self.execute = AsyncMock()

class MockBot:
    def __init__(self):
        self.database = MockDatabase()

# Import the functions to test
from cogs.memberreport import get_monthly_joins, build_joins_field

async def test_get_monthly_joins():
    print("Testing get_monthly_joins...")
    bot = MockBot()
    guild_id = 123456789

    # get_monthly_joins now issues, per month: a COUNT(*) presence query, then
    # either a daily SUM (when daily rows exist) or a weekly-seed SUM fallback.
    # Drive the three months (oldest -> current) accordingly:
    #   month A: no daily rows -> weekly fallback = 200
    #   month B: has daily rows -> daily sum = 100
    #   month C (current): no daily rows -> weekly fallback = 10
    counts = iter([0, 1, 0])
    sums = iter([200, 100, 10])

    async def side_effect_fetch_one(query, params=()):
        if "COUNT(*)" in query:
            return {"c": next(counts)}
        return {"s": next(sums)}

    bot.database.fetch_one.side_effect = side_effect_fetch_one

    res = await get_monthly_joins(bot, guild_id, 3)
    print("Monthly joins result:", res)
    assert len(res) == 3, f"Expected 3 months, got {len(res)}"
    assert res[0][1] == 200
    assert res[1][1] == 100
    assert res[2][1] == 10
    print("get_monthly_joins test passed!")

def test_build_joins_field():
    print("Testing build_joins_field...")

    daily = [
        ("2026-05-25", 1), ("2026-05-26", 2), ("2026-05-27", 3),
        ("2026-05-28", 4), ("2026-05-29", 5), ("2026-05-30", 6), ("2026-05-31", 7),
        ("2026-06-01", 5), ("2026-06-02", 1), ("2026-06-03", 2),
        ("2026-06-04", 0), ("2026-06-05", 10), ("2026-06-06", 0), ("2026-06-07", 0)
    ]
    monthly = [("2026-04", 120), ("2026-05", 188)]

    # Test Daily
    name, val = build_joins_field("daily", daily, monthly)
    print("Daily:")
    print("Name:", name)
    print(val)
    assert name == "🆕 New Members Joined • Daily"
    assert "Jun 01   │    5" in val
    assert "Jun 02   │    1" in val
    assert "Jun 07   │    0  ◀ today" in val
    # closing code fence must be terminated by a newline (not glued to caption)
    assert "```\n*Joins per day" in val

    # Test Weekly (rolling last 7 days, not Monday-anchored)
    name, val = build_joins_field("weekly", daily, monthly)
    print("\nWeekly:")
    print("Name:", name)
    print(val)
    assert name == "🆕 New Members Joined • Last 7 Days"
    assert "Jun 01   │    5" in val
    assert "Jun 07   │    0  ◀ today" in val
    assert "**18** joined in the last 7 days" in val
    assert "📉 10 vs. previous 7 days" in val
    assert "Monday-anchored" not in val

    # Test Monthly (month-to-date, with explicit prior-month-MTD comparison)
    name, val = build_joins_field("monthly", daily, monthly, prev_month_mtd=150)
    print("\nMonthly:")
    print("Name:", name)
    print(val)
    assert name == "🆕 New Members Joined • Monthly"
    assert "Apr 2026     │  120" in val
    assert "May 2026     │  188  ◀ this month (so far)" in val
    assert "**188** joined this month so far" in val
    assert "📈 +38 vs. same point last month" in val

    print("build_joins_field test passed!")

async def main():
    await test_get_monthly_joins()
    test_build_joins_field()

if __name__ == "__main__":
    asyncio.run(main())
