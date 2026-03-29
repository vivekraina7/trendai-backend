"""
Forgot / Reset password routes.

POST /api/auth/forgot-password  { email }
  → generates a 6-digit OTP stored in DB, returned in response (local dev mode)
  → in production: send via SMTP

POST /api/auth/reset-password   { token, email, new_password }
  → verifies OTP, sets new hashed password, invalidates token
"""
import os
import random
import string
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from db import get_db
from auth import hash_password

DB_PATH  = os.getenv("DB_PATH", "./edutrend.db")
router   = APIRouter(prefix="/auth", tags=["auth"])

# SMTP config (optional — local dev works without it)
SMTP_HOST  = os.getenv("SMTP_HOST", "")
SMTP_PORT  = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER  = os.getenv("SMTP_USER", "")
SMTP_PASS  = os.getenv("SMTP_PASS", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER)
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


def _generate_otp(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))


def _send_email(to: str, subject: str, body: str) -> bool:
    """Send email via SMTP. Returns False if SMTP not configured."""
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS]):
        return False
    try:
        msg = MIMEText(body, "plain")
        msg["Subject"] = subject
        msg["From"] = FROM_EMAIL
        msg["To"] = to
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(FROM_EMAIL, to, msg.as_string())
        return True
    except Exception:
        return False


# ── Request bodies ────────────────────────────────────────────────────────────

class ForgotBody(BaseModel):
    email: EmailStr


class ResetBody(BaseModel):
    email: EmailStr
    otp: str
    new_password: str


# ── Ensure reset_tokens table exists ─────────────────────────────────────────

async def ensure_reset_table():
    """Table is now created in init_db() — no-op kept for safety."""
    pass  # password_reset_tokens created in db.init_db() on startup


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/forgot-password")
async def forgot_password(body: ForgotBody):
    await ensure_reset_table()

    async with get_db() as db:
        row = await db.execute("SELECT id FROM users WHERE email=?", (body.email,))
        row = await row.fetchone()
        if not row:
            return {"message": "If that email is registered, a reset code will be sent."}

        otp = _generate_otp()
        expires_at = (datetime.utcnow() + timedelta(minutes=15)).isoformat()

        await db.execute("UPDATE password_reset_tokens SET used=1 WHERE email=?", (body.email,))
        await db.execute(
            "INSERT INTO password_reset_tokens (email, otp, expires_at) VALUES (?, ?, ?)",
            (body.email, otp, expires_at),
        )
        await db.commit()

    # Try to send email
    email_sent = _send_email(
        to=body.email,
        subject="EduTrend AI — Password Reset Code",
        body=f"""Hi,

Your password reset code is: {otp}

This code expires in 15 minutes.

If you didn't request this, you can ignore this email.

— EduTrend AI Team
""",
    )

    response: dict = {
        "message": "If that email is registered, a reset code will be sent.",
        "email_sent": email_sent,
    }

    # In local dev mode (no SMTP), return the OTP directly so dev can test
    if not email_sent:
        response["dev_otp"] = otp
        response["dev_note"] = (
            "SMTP not configured — OTP shown here for local development only. "
            "Add SMTP_HOST/SMTP_USER/SMTP_PASS to backend/.env to send real emails."
        )

    return response


@router.post("/reset-password")
async def reset_password(body: ResetBody):
    await ensure_reset_table()

    if len(body.new_password) < 8:
        raise HTTPException(
            status_code=400, detail="Password must be at least 8 characters."
        )

    async with get_db() as db:
        row = await db.execute(
            """SELECT id, expires_at, used FROM password_reset_tokens
               WHERE email=? AND otp=?
               ORDER BY id DESC LIMIT 1""",
            (body.email, body.otp),
        )
        token = await row.fetchone()

        if not token:
            raise HTTPException(status_code=400, detail="Invalid or expired reset code.")
        if token[2]:
            raise HTTPException(status_code=400, detail="This code has already been used.")
        if datetime.utcnow() > datetime.fromisoformat(token[1]):
            raise HTTPException(status_code=400, detail="Reset code has expired.")

        new_hash = hash_password(body.new_password)
        await db.execute("UPDATE users SET password_hash=? WHERE email=?", (new_hash, body.email))
        await db.execute("UPDATE password_reset_tokens SET used=1 WHERE email=? AND otp=?", (body.email, body.otp))
        await db.commit()

    return {"message": "Password updated successfully. You can now log in."}
