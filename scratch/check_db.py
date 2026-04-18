import asyncio
import aiosqlite
import json
import sys

async def check_db():
    db_path = "data/im8bot.db"
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        
        print("--- Tables ---")
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cursor:
            tables = await cursor.fetchall()
            for t in tables:
                print(f"Table: {t['name']}")
        
        print("\n--- editor_sessions Schema ---")
        try:
            async with db.execute("PRAGMA table_info(editor_sessions)") as cursor:
                columns = await cursor.fetchall()
                for c in columns:
                    print(f"Col: {c['name']} ({c['type']})")
        except Exception as e:
            print(f"Error checking editor_sessions: {e}")

if __name__ == "__main__":
    asyncio.run(check_db())
