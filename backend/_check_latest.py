import asyncio
from sqlalchemy import text as sa_text
from src.database import AsyncSessionLocal


async def main():
    async with AsyncSessionLocal() as db:
        res = await db.execute(sa_text(
            "SELECT id, status, progress_message, error_code, created_at "
            "FROM tasks ORDER BY created_at DESC LIMIT 5"
        ))
        for r in res.fetchall():
            print(dict(r._mapping))


asyncio.run(main())
