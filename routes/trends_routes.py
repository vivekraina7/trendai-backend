"""
Trends routes: return cached trend data from SQLite/Turso.
Falls back to curated in-memory data if DB is empty.
"""
from fastapi import APIRouter, Depends
from db import get_db
from scraper import refresh_trends_if_stale, _curated_trends

router = APIRouter(prefix="/trends", tags=["trends"])


def _row_to_dict(r):
    return {k: r[k] for k in r.keys()}


def _curated_response():
    """Fallback: return curated trends when DB is empty."""
    trends = _curated_trends()
    by_category: dict = {}
    for t in trends:
        by_category.setdefault(t["category"], []).append(t)
    return {"trends": trends, "by_category": by_category}


@router.get("/")
async def get_trends(db=Depends(get_db)):
    """Return all trend data, refreshing from web if older than 24 hours."""
    await refresh_trends_if_stale(db)

    cur = await db.execute(
        "SELECT * FROM trends ORDER BY demand DESC, growth_pct DESC"
    )
    rows = await cur.fetchall()

    if not rows:
        return _curated_response()

    trends = [_row_to_dict(r) for r in rows]
    by_category: dict = {}
    for t in trends:
        by_category.setdefault(t["category"], []).append(t)

    return {"trends": trends, "by_category": by_category}


@router.get("/top")
async def get_top_trending(limit: int = 10, db=Depends(get_db)):
    """Top trending topics sorted by growth."""
    await refresh_trends_if_stale(db)

    cur = await db.execute(
        "SELECT * FROM trends ORDER BY growth_pct DESC LIMIT ?", (limit,)
    )
    rows = await cur.fetchall()

    if not rows:
        curated = sorted(_curated_trends(), key=lambda x: x["growth_pct"], reverse=True)
        return {"top_trending": curated[:limit]}

    return {"top_trending": [_row_to_dict(r) for r in rows]}


@router.post("/refresh")
async def force_refresh(db=Depends(get_db)):
    """Force a fresh scrape of trend data."""
    from scraper import scrape_and_store_trends
    count = await scrape_and_store_trends(db)
    return {"message": f"Refreshed {count} trend entries"}
