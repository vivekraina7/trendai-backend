"""
AI Mentor routes: chat with Google ADK powered Gemini agent.
Supports session history and streaming.
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from aiosqlite import Connection
from db import get_db
from dependencies import get_current_user, get_optional_user
from agent import get_mentor_response

router = APIRouter(prefix="/mentor", tags=["mentor"])


class ChatBody(BaseModel):
    message: str
    session_id: str | None = None


@router.post("/chat")
async def chat(
    body: ChatBody,
    current_user: dict | None = Depends(get_optional_user),
    db: Connection = Depends(get_db),
):
    """
    Send a message to the AI Mentor. 
    Works for both authenticated users (saves history) and guests.
    """
    session_id = body.session_id or str(uuid.uuid4())
    user_id = current_user["id"] if current_user else None

    # Build conversation history for context
    history = []
    if user_id and session_id:
        async with db.execute(
            """SELECT role, content FROM chat_messages
               WHERE user_id = ? AND session_id = ?
               ORDER BY id DESC LIMIT 20""",
            (user_id, session_id),
        ) as cur:
            rows = await cur.fetchall()
        history = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    # Get AI response from ADK agent (with RAG from ChromaDB if docs uploaded)
    try:
        response_text = await get_mentor_response(body.message, history, user_id=user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI error: {str(e)}")

    # Persist messages for authenticated users
    if user_id:
        await db.execute(
            "INSERT INTO chat_messages (user_id, session_id, role, content) VALUES (?, ?, 'user', ?)",
            (user_id, session_id, body.message),
        )
        await db.execute(
            "INSERT INTO chat_messages (user_id, session_id, role, content) VALUES (?, ?, 'assistant', ?)",
            (user_id, session_id, response_text),
        )
        await db.commit()

    return {
        "session_id": session_id,
        "response": response_text,
    }


@router.get("/history/{session_id}")
async def get_history(
    session_id: str,
    current_user: dict = Depends(get_current_user),
    db: Connection = Depends(get_db),
):
    """Return full chat history for a session."""
    async with db.execute(
        """SELECT role, content, created_at FROM chat_messages
           WHERE user_id = ? AND session_id = ?
           ORDER BY id ASC""",
        (current_user["id"], session_id),
    ) as cur:
        rows = await cur.fetchall()
    return {"session_id": session_id, "messages": [dict(r) for r in rows]}


@router.get("/sessions")
async def get_sessions(
    current_user: dict = Depends(get_current_user),
    db: Connection = Depends(get_db),
):
    """List all chat sessions for the current user."""
    async with db.execute(
        """SELECT session_id, MIN(created_at) as started_at, COUNT(*) as message_count
           FROM chat_messages WHERE user_id = ?
           GROUP BY session_id ORDER BY started_at DESC""",
        (current_user["id"],),
    ) as cur:
        rows = await cur.fetchall()
    return {"sessions": [dict(r) for r in rows]}
