"""
EduTrend AI — FastAPI Application Entry Point
"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from db import init_db
from routes.auth_routes import router as auth_router
from routes.mentor_routes import router as mentor_router
from routes.trends_routes import router as trends_router
from routes.progress_routes import router as progress_router
from routes.docs_routes import router as docs_router
from routes.oauth_routes import router as oauth_router
from routes.password_routes import router as password_router

load_dotenv()

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialise database."""
    await init_db()
    yield


app = FastAPI(
    title="EduTrend AI API",
    description="Backend for EduTrend AI — trend detection, AI mentoring, and learning path tracking",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
_origins = [
    FRONTEND_URL,
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
]
# Allow network IP access in local dev (e.g. testing from phone on same WiFi)
# In production FRONTEND_URL will be the Vercel URL — that's all that's needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_origin_regex=r"http://192\.168\.\d+\.\d+:\d+",  # local network
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth_router,     prefix="/api")
app.include_router(oauth_router,    prefix="/api")       # Google + GitHub OAuth
app.include_router(password_router, prefix="/api")       # Forgot / Reset password
app.include_router(mentor_router,   prefix="/api")
app.include_router(trends_router,   prefix="/api")
app.include_router(progress_router, prefix="/api")
app.include_router(docs_router,     prefix="/api")       # PDF/doc RAG upload


@app.get("/")
async def root():
    return {
        "status": "running",
        "service": "EduTrend AI API",
        "version": "1.0.0",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
