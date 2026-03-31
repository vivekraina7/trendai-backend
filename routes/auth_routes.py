"""
Auth routes: register, login, me (get profile).
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from db import get_db
from auth import hash_password, verify_password, create_access_token
from dependencies import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class RegisterBody(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    token: str
    user: dict


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/register", response_model=AuthResponse)
async def register(body: RegisterBody, db=Depends(get_db)):
    cur = await db.execute("SELECT id FROM users WHERE email = ?", (body.email,))
    if await cur.fetchone():
        raise HTTPException(status_code=409, detail="Email already registered")

    pw_hash = hash_password(body.password)
    cur = await db.execute(
        "INSERT INTO users (name, email, password, provider) VALUES (?, ?, ?, 'email') RETURNING id",
        (body.name, body.email, pw_hash),
    )
    row = await cur.fetchone()
    user_id = row[0]
    await db.commit()

    token = create_access_token(user_id, body.email)
    return {
        "token": token,
        "user": {"id": user_id, "name": body.name, "email": body.email},
    }


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginBody, db=Depends(get_db)):
    cur = await db.execute(
        "SELECT id, name, email, password FROM users WHERE email = ?", (body.email,)
    )
    user = await cur.fetchone()

    if not user or not user["password"]:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not verify_password(body.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user["id"], user["email"])
    return {
        "token": token,
        "user": {"id": user["id"], "name": user["name"], "email": user["email"]},
    }


@router.get("/me")
async def me(current_user: dict = Depends(get_current_user), db=Depends(get_db)):
    cur = await db.execute(
        "SELECT id, name, email, avatar_url, created_at FROM users WHERE id = ?",
        (current_user["id"],),
    )
    user = await cur.fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {k: user[k] for k in user.keys()}
