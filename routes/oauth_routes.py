"""
Google & GitHub OAuth 2.0 routes for FastAPI.

Flow:
  1. Frontend sends user to   GET /api/auth/google   (or /github)
  2. FastAPI redirects to     provider's consent page
  3. Provider redirects back  GET /api/auth/google/callback?code=...
  4. FastAPI exchanges code   → gets user info → creates JWT → redirects to frontend
"""
import os
import httpx

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from auth import create_access_token
from db import get_db

router = APIRouter(prefix="/auth", tags=["oauth"])

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

# ── Google OAuth ───────────────────────────────────────────────────────────────

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI  = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")


@router.get("/google")
async def google_login():
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=503,
            detail="Google OAuth not configured. Add GOOGLE_CLIENT_ID to backend/.env",
        )
    url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={GOOGLE_CLIENT_ID}"
        "&response_type=code"
        f"&redirect_uri={GOOGLE_REDIRECT_URI}"
        "&scope=openid email profile"
        "&access_type=offline"
        "&prompt=select_account"
    )
    return RedirectResponse(url)


@router.get("/google/callback")
async def google_callback(code: str):
    if not GOOGLE_CLIENT_ID:
        return RedirectResponse(f"{FRONTEND_URL}/login?error=oauth_not_configured")

    async with httpx.AsyncClient() as client:
        # Exchange code for tokens
        token_res = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
        if token_res.status_code != 200:
            return RedirectResponse(f"{FRONTEND_URL}/login?error=google_token_failed")
        tokens = token_res.json()

        # Get user info
        info_res = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        if info_res.status_code != 200:
            return RedirectResponse(f"{FRONTEND_URL}/login?error=google_userinfo_failed")
        info = info_res.json()

    email = info.get("email", "")
    name  = info.get("name",  email.split("@")[0])

    # Upsert user in SQLite
    async with get_db() as db:
        row = await db.execute("SELECT id FROM users WHERE email=?", (email,))
        row = await row.fetchone()
        if row:
            user_id = row[0]
            await db.execute("UPDATE users SET name=? WHERE id=?", (name, user_id))
        else:
            cur = await db.execute(
                "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                (name, email, ""),
            )
            user_id = cur.lastrowid
        await db.commit()

    token = create_access_token({"sub": str(user_id), "email": email})
    # Redirect to frontend with token in query param (frontend stores it)
    return RedirectResponse(f"{FRONTEND_URL}/auth/callback?token={token}&name={name}&email={email}&id={user_id}")


# ── GitHub OAuth ───────────────────────────────────────────────────────────────

GITHUB_CLIENT_ID     = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
GITHUB_REDIRECT_URI  = os.getenv("GITHUB_REDIRECT_URI", "http://localhost:8000/api/auth/github/callback")


@router.get("/github")
async def github_login():
    if not GITHUB_CLIENT_ID:
        raise HTTPException(
            status_code=503,
            detail="GitHub OAuth not configured. Add GITHUB_CLIENT_ID to backend/.env",
        )
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={GITHUB_REDIRECT_URI}"
        "&scope=read:user user:email"
    )
    return RedirectResponse(url)


@router.get("/github/callback")
async def github_callback(code: str):
    if not GITHUB_CLIENT_ID:
        return RedirectResponse(f"{FRONTEND_URL}/login?error=oauth_not_configured")

    async with httpx.AsyncClient() as client:
        # Exchange code for access token
        token_res = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": GITHUB_REDIRECT_URI,
            },
        )
        if token_res.status_code != 200:
            return RedirectResponse(f"{FRONTEND_URL}/login?error=github_token_failed")
        access_token = token_res.json().get("access_token", "")

        # Get user info
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        info_res   = await client.get("https://api.github.com/user", headers=headers)
        emails_res = await client.get("https://api.github.com/user/emails", headers=headers)

    if info_res.status_code != 200:
        return RedirectResponse(f"{FRONTEND_URL}/login?error=github_userinfo_failed")

    info = info_res.json()
    name = info.get("name") or info.get("login", "GitHub User")

    # Get primary email
    email = ""
    if emails_res.status_code == 200:
        emails = emails_res.json()
        primary = next((e for e in emails if e.get("primary")), None)
        email = primary["email"] if primary else (emails[0]["email"] if emails else "")
    if not email:
        email = f"{info.get('login', 'user')}@github.local"

    # Upsert user in SQLite
    async with get_db() as db:
        row = await db.execute("SELECT id FROM users WHERE email=?", (email,))
        row = await row.fetchone()
        if row:
            user_id = row[0]
            await db.execute("UPDATE users SET name=? WHERE id=?", (name, user_id))
        else:
            cur = await db.execute(
                "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                (name, email, ""),
            )
            user_id = cur.lastrowid
        await db.commit()

    token = create_access_token({"sub": str(user_id), "email": email})
    return RedirectResponse(f"{FRONTEND_URL}/auth/callback?token={token}&name={name}&email={email}&id={user_id}")


# ── OAuth config probe (so frontend knows which providers are enabled) ─────────

@router.get("/oauth-providers")
async def oauth_providers():
    return {
        "google": bool(GOOGLE_CLIENT_ID),
        "github": bool(GITHUB_CLIENT_ID),
    }
