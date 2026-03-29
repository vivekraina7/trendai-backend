"""
Trends routes: return cached trend data from SQLite.
Also triggers a scrape-refresh if data is stale (>24h).
"""
from fastapi import APIRouter, Depends
from aiosqlite import Connection
from db import get_db
from scraper import refresh_trends_if_stale

router = APIRouter(prefix="/trends", tags=["trends"])


@router.get("/")
async def get_trends(db: Connection = Depends(get_db)):
    """Return all trend data, refreshing from web if older than 24 hours."""
    await refresh_trends_if_stale(db)

    async with db.execute(
        "SELECT * FROM trends ORDER BY demand DESC, growth_pct DESC"
    ) as cur:
        rows = await cur.fetchall()

    trends = [dict(r) for r in rows]

    # Group by category for easy frontend consumption
    by_category: dict = {}
    for t in trends:
        cat = t["category"]
        by_category.setdefault(cat, []).append(t)

    return {"trends": trends, "by_category": by_category}


@router.get("/top")
async def get_top_trending(limit: int = 10, db: Connection = Depends(get_db)):
    """Top trending topics sorted by growth."""
    await refresh_trends_if_stale(db)

    async with db.execute(
        "SELECT * FROM trends ORDER BY growth_pct DESC LIMIT ?", (limit,)
    ) as cur:
        rows = await cur.fetchall()
    return {"top_trending": [dict(r) for r in rows]}


@router.post("/refresh")
async def force_refresh(db: Connection = Depends(get_db)):
    """Force a fresh scrape of trend data."""
    from scraper import scrape_and_store_trends
    count = await scrape_and_store_trends(db)
    return {"message": f"Refreshed {count} trend entries"}
