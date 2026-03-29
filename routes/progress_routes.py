"""
Progress routes: manage user learning path and module progress.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from aiosqlite import Connection
from db import get_db
from dependencies import get_current_user

router = APIRouter(prefix="/progress", tags=["progress"])

# Default module structure for each path (mirrors frontend data)
DEFAULT_PATHS = {
    "ai-ml": {
        "title": "AI & Machine Learning Engineer",
        "modules": [
            {"title": "Python & Math Foundations", "lessons": 12, "hours": 15},
            {"title": "Machine Learning Fundamentals", "lessons": 18, "hours": 24},
            {"title": "Deep Learning & Neural Nets", "lessons": 15, "hours": 22},
            {"title": "NLP & Large Language Models", "lessons": 14, "hours": 20},
            {"title": "RAG & AI Agents", "lessons": 10, "hours": 16},
            {"title": "MLOps & Deployment", "lessons": 8, "hours": 12},
        ],
    },
    "fullstack": {
        "title": "Full-Stack AI Developer",
        "modules": [
            {"title": "HTML, CSS & JavaScript", "lessons": 14, "hours": 18},
            {"title": "React & Next.js", "lessons": 16, "hours": 22},
            {"title": "Node.js & FastAPI Backend", "lessons": 12, "hours": 18},
            {"title": "Database Design (SQL & NoSQL)", "lessons": 10, "hours": 14},
            {"title": "LLM Integration & APIs", "lessons": 8, "hours": 12},
            {"title": "Deployment & DevOps", "lessons": 6, "hours": 10},
        ],
    },
    "data": {
        "title": "Data Science & Analytics",
        "modules": [
            {"title": "Statistics & Probability", "lessons": 10, "hours": 14},
            {"title": "Python & Pandas for Data", "lessons": 14, "hours": 20},
            {"title": "Data Visualization with Plotly", "lessons": 8, "hours": 12},
            {"title": "SQL & Data Warehousing", "lessons": 10, "hours": 15},
            {"title": "Machine Learning for Analytics", "lessons": 12, "hours": 18},
            {"title": "Business Intelligence Projects", "lessons": 6, "hours": 10},
        ],
    },
    "cloud": {
        "title": "Cloud & DevOps Engineer",
        "modules": [
            {"title": "Linux & Networking Basics", "lessons": 10, "hours": 14},
            {"title": "Docker & Containers", "lessons": 8, "hours": 12},
            {"title": "Kubernetes Orchestration", "lessons": 10, "hours": 16},
            {"title": "AWS / Azure / GCP", "lessons": 14, "hours": 22},
            {"title": "CI/CD Pipelines", "lessons": 6, "hours": 10},
            {"title": "Infrastructure as Code", "lessons": 8, "hours": 12},
        ],
    },
    "security": {
        "title": "Cybersecurity Specialist",
        "modules": [
            {"title": "Security Fundamentals", "lessons": 12, "hours": 16},
            {"title": "Network Security", "lessons": 10, "hours": 14},
            {"title": "Ethical Hacking & Pentesting", "lessons": 14, "hours": 22},
            {"title": "Cloud Security", "lessons": 8, "hours": 12},
            {"title": "Incident Response & SIEM", "lessons": 10, "hours": 16},
            {"title": "Zero Trust Architecture", "lessons": 6, "hours": 10},
        ],
    },
}


class EnrollBody(BaseModel):
    path_id: str


class UpdateProgressBody(BaseModel):
    path_id: str
    module_index: int
    status: str  # 'completed' | 'current' | 'locked'


@router.post("/enroll")
async def enroll_path(
    body: EnrollBody,
    current_user: dict = Depends(get_current_user),
    db: Connection = Depends(get_db),
):
    """Enroll user in a learning path and initialise module progress rows."""
    if body.path_id not in DEFAULT_PATHS:
        raise HTTPException(status_code=404, detail="Unknown path")

    path = DEFAULT_PATHS[body.path_id]
    user_id = current_user["id"]

    # Insert or ignore the path enrolment
    await db.execute(
        "INSERT OR IGNORE INTO user_paths (user_id, path_id, path_title) VALUES (?, ?, ?)",
        (user_id, body.path_id, path["title"]),
    )

    # Init module progress rows (first module current, rest locked)
    for idx, mod in enumerate(path["modules"]):
        status = "current" if idx == 0 else "locked"
        await db.execute(
            """INSERT OR IGNORE INTO module_progress
               (user_id, path_id, module_index, status)
               VALUES (?, ?, ?, ?)""",
            (user_id, body.path_id, idx, status),
        )

    await db.commit()
    return {"message": f"Enrolled in {path['title']}"}


@router.get("/{path_id}")
async def get_path_progress(
    path_id: str,
    current_user: dict = Depends(get_current_user),
    db: Connection = Depends(get_db),
):
    """Return progress for a specific path."""
    if path_id not in DEFAULT_PATHS:
        raise HTTPException(status_code=404, detail="Unknown path")

    user_id = current_user["id"]
    path_def = DEFAULT_PATHS[path_id]

    async with db.execute(
        "SELECT module_index, status, completed_at FROM module_progress "
        "WHERE user_id = ? AND path_id = ? ORDER BY module_index",
        (user_id, path_id),
    ) as cur:
        rows = await cur.fetchall()

    if not rows:
        # Not enrolled yet – return default (all locked)
        modules = [
            {**mod, "status": "locked", "completed_at": None, "index": i}
            for i, mod in enumerate(path_def["modules"])
        ]
        return {"enrolled": False, "path_id": path_id, "modules": modules, "progress_pct": 0}

    progress_map = {r["module_index"]: dict(r) for r in rows}
    modules = []
    for i, mod in enumerate(path_def["modules"]):
        p = progress_map.get(i, {"status": "locked", "completed_at": None})
        modules.append({**mod, "index": i, "status": p["status"], "completed_at": p["completed_at"]})

    completed = sum(1 for m in modules if m["status"] == "completed")
    pct = round(completed / len(modules) * 100)

    return {
        "enrolled": True,
        "path_id": path_id,
        "path_title": path_def["title"],
        "modules": modules,
        "progress_pct": pct,
        "completed_count": completed,
        "total_count": len(modules),
    }


@router.patch("/update")
async def update_module_status(
    body: UpdateProgressBody,
    current_user: dict = Depends(get_current_user),
    db: Connection = Depends(get_db),
):
    """Mark a module as completed/current/locked."""
    user_id = current_user["id"]
    completed_at = "datetime('now')" if body.status == "completed" else "NULL"

    await db.execute(
        f"""UPDATE module_progress
            SET status = ?, completed_at = {completed_at}
            WHERE user_id = ? AND path_id = ? AND module_index = ?""",
        (body.status, user_id, body.path_id, body.module_index),
    )

    # If completing a module, auto-unlock the next one
    if body.status == "completed":
        next_idx = body.module_index + 1
        await db.execute(
            """UPDATE module_progress
               SET status = 'current'
               WHERE user_id = ? AND path_id = ? AND module_index = ? AND status = 'locked'""",
            (user_id, body.path_id, next_idx),
        )

    await db.commit()
    return {"message": "Progress updated"}


@router.get("/")
async def get_all_progress(
    current_user: dict = Depends(get_current_user),
    db: Connection = Depends(get_db),
):
    """Get a summary of all enrolled paths for the user."""
    user_id = current_user["id"]
    async with db.execute(
        "SELECT path_id, path_title, started_at FROM user_paths WHERE user_id = ?",
        (user_id,),
    ) as cur:
        paths = await cur.fetchall()

    result = []
    for p in paths:
        async with db.execute(
            "SELECT status FROM module_progress WHERE user_id = ? AND path_id = ?",
            (user_id, p["path_id"]),
        ) as cur:
            mods = await cur.fetchall()
        total = len(mods)
        completed = sum(1 for m in mods if m["status"] == "completed")
        result.append({
            "path_id": p["path_id"],
            "path_title": p["path_title"],
            "started_at": p["started_at"],
            "progress_pct": round(completed / total * 100) if total else 0,
            "completed": completed,
            "total": total,
        })

    return {"paths": result}
