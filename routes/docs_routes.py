"""
Document upload & RAG routes.

POST /api/docs/upload  — upload PDF or .txt file → chunk → embed → ChromaDB
GET  /api/docs/        — list uploaded docs for current user
DELETE /api/docs/{doc_id} — delete a doc from ChromaDB
"""
import os
import hashlib
from io import BytesIO
from datetime import datetime

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
import chromadb

from dependencies import get_optional_user

router = APIRouter(prefix="/docs", tags=["docs"])

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")

# ── ChromaDB client (lazy singleton) ─────────────────────────────────────────

_chroma_client = None


def get_chroma():
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
    return _chroma_client


def get_collection(user_id: int | None = None):
    """Return a per-user ChromaDB collection (or shared if guest)."""
    client = get_chroma()
    name = f"user_{user_id}_docs" if user_id else "guest_docs"
    return client.get_or_create_collection(name=name)


# ── Text chunking helper ──────────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100):
    """Split text into overlapping chunks for better embedding coverage."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i : i + chunk_size])
        chunks.append(chunk)
        i += chunk_size - overlap
    return chunks


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    user=Depends(get_optional_user),
):
    """
    Accept PDF or plain-text files.
    Extracts text → chunks → stores in ChromaDB for RAG.
    """
    filename = file.filename or "unknown"
    content_type = file.content_type or ""

    # Read raw bytes
    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:  # 10 MB cap
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")

    # Extract text
    if filename.lower().endswith(".pdf") or "pdf" in content_type:
        try:
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(raw))
            text = "\n\n".join(
                page.extract_text() or "" for page in reader.pages
            ).strip()
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Could not parse PDF: {e}")
    elif filename.lower().endswith((".txt", ".md")):
        text = raw.decode("utf-8", errors="replace")
    else:
        raise HTTPException(
            status_code=415,
            detail="Unsupported file type. Upload PDF, TXT, or MD files.",
        )

    if len(text) < 50:
        raise HTTPException(status_code=422, detail="Extracted text is too short or empty.")

    # Generate stable doc_id from filename + content hash
    doc_id = hashlib.md5(raw).hexdigest()[:16]

    # Chunk and store
    chunks = chunk_text(text)
    user_id = user["id"] if user else None
    collection = get_collection(user_id)

    chunk_ids = [f"{doc_id}_chunk_{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "filename": filename,
            "doc_id": doc_id,
            "chunk": i,
            "uploaded_at": datetime.utcnow().isoformat(),
        }
        for i in range(len(chunks))
    ]

    # Upsert (avoids duplicates on re-upload)
    collection.upsert(
        ids=chunk_ids,
        documents=chunks,
        metadatas=metadatas,
    )

    return {
        "status": "ok",
        "doc_id": doc_id,
        "filename": filename,
        "chunks": len(chunks),
        "characters": len(text),
        "message": f"Processed {len(chunks)} chunks from '{filename}'. Your AI Mentor will now use this document for context.",
    }


@router.get("/")
async def list_docs(user=Depends(get_optional_user)):
    """List all documents stored for this user."""
    try:
        user_id = user["id"] if user else None
        collection = get_collection(user_id)
        results = collection.get(include=["metadatas"])
        if not results["metadatas"]:
            return {"docs": []}

        # Deduplicate by doc_id
        seen = {}
        for meta in results["metadatas"]:
            did = meta.get("doc_id", "")
            if did not in seen:
                seen[did] = {
                    "doc_id": did,
                    "filename": meta.get("filename", "unknown"),
                    "uploaded_at": meta.get("uploaded_at", ""),
                }
        return {"docs": list(seen.values())}
    except Exception:
        return {"docs": []}


@router.delete("/{doc_id}")
async def delete_doc(doc_id: str, user=Depends(get_optional_user)):
    """Delete all chunks of a document from ChromaDB."""
    try:
        user_id = user["id"] if user else None
        collection = get_collection(user_id)
        results = collection.get(where={"doc_id": doc_id})
        if results["ids"]:
            collection.delete(ids=results["ids"])
            return {"status": "deleted", "chunks_removed": len(results["ids"])}
        return {"status": "not_found"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
