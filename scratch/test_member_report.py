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
    
    # Mock daily sums: let's say daily returns 0, so it falls back to weekly sum
    # Let's mock _sum_daily_in_range by mocking bot.database calls or similar.
    # Since _sum_daily_in_range calls bot.database.fetch_one:
    # "SELECT COALESCE(SUM(joins), 0) AS s FROM member_joins WHERE guild_id = ? AND period = 'day' AND period_date BETWEEN ? AND ?"
    # Let's mock fetch_one to return {"s": 0} first, then {"s": 42} for weekly
    
    fetch_one_responses = [
        {"s": 0},  # April daily sum (i = 2)
        {"s": 200}, # April weekly fallback
        {"s": 100}, # May daily sum (i = 1)
        {"s": 0},  # June daily sum (i = 0)
        {"s": 10}, # June weekly fallback
    ]

    async def side_effect_fetch_one(query, params=()):
        if fetch_one_responses:
            return fetch_one_responses.pop(0)
        return {"s": 0}

    bot.database.fetch_one.side_effect = side_effect_fetch_one

    # We want 3 months
    res = await get_monthly_joins(bot, guild_id, 3)
    print("Monthly joins result:", res)
    assert len(res) == 3, f"Expected 3 months, got {len(res)}"
    # Older month: res[0], Current month: res[2]
    assert res[0][1] == 200
    assert res[1][1] == 100
    assert res[2][1] == 10
    print("get_monthly_joins test passed!")

def test_build_joins_field():
    print("Testing build_joins_field...")
    
    daily = [("2026-06-01", 5), ("2026-06-02", 1), ("2026-06-03", 2)]
    weekly = [("2026-05-18", 100), ("2026-05-25", 88)]
    monthly = [("2026-04", 120), ("2026-05", 188)]
    
    # Test Daily
    name, val = build_joins_field("daily", daily, weekly, monthly)
    print("Daily:")
    print("Name:", name)
    print("Value:")
    print(val)
    assert name == "📅 Daily Registration Breakdown"
    assert "• **2026-06-01**: 5 new members" in val
    assert "• **2026-06-02**: 1 new member" in val  # singular polish!
    
    # Test Weekly
    name, val = build_joins_field("weekly", daily, weekly, monthly)
    print("\nWeekly:")
    print("Name:", name)
    print("Value:")
    print(val)
    assert name == "📅 Weekly Registration Breakdown"
    assert "• **Week of 2026-05-18**: 100 new members" in val
    
    # Test Monthly
    name, val = build_joins_field("monthly", daily, weekly, monthly)
    print("\nMonthly:")
    print("Name:", name)
    print("Value:")
    print(val)
    assert name == "📅 Monthly Registration Breakdown"
    assert "• **May 2026**: 188 new members" in val
    
    print("build_joins_field test passed!")

async def main():
    await test_get_monthly_joins()
    test_build_joins_field()

if __name__ == "__main__":
    asyncio.run(main())
