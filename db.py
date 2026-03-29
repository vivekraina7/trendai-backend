"""
Database — dual mode:
  • Local dev  → aiosqlite (SQLite file, zero dependencies)
  • Production → Turso HTTP API via httpx (no Rust/C compiler needed)

Set TURSO_DATABASE_URL + TURSO_AUTH_TOKEN in .env to switch to Turso.
"""
import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

DB_PATH     = os.getenv("DB_PATH", "./edutrend.db")
TURSO_URL   = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")
USE_TURSO   = bool(TURSO_URL and TURSO_TOKEN)


# ── Shared Row helper (index + key access) ─────────────────────────────────────

class _Row:
    """Supports both row[0] (index) and row["col"] (key) access."""
    __slots__ = ("_keys", "_vals", "_map")

    def __init__(self, col_names: list, values: tuple):
        self._keys = col_names
        self._vals = tuple(values)
        self._map  = dict(zip(col_names, self._vals))

    def __getitem__(self, key):
        return self._vals[key] if isinstance(key, int) else self._map[key]

    def __iter__(self):
        return iter(self._vals)

    def __repr__(self):
        return repr(self._map)

    def keys(self):
        return self._keys


# ── Turso HTTP API client (uses httpx — already a project dependency) ──────────

def _turso_http_url() -> str:
    """Convert libsql:// → https:// for the HTTP API."""
    return TURSO_URL.replace("libsql://", "https://") + "/v2/pipeline"


def _encode_arg(v):
    """Encode a Python value into a Turso HTTP API argument."""
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": "1" if v else "0"}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": str(v)}
    return {"type": "text", "value": str(v)}


class _TursoHTTPCursor:
    """Async cursor returned by _TursoHTTPConn.execute()."""

    def __init__(self, result: dict):
        self._cols = [c["name"] for c in result.get("cols", [])]
        self._rows = result.get("rows", [])
        rid = result.get("last_insert_rowid")
        self._lastrowid = int(rid) if rid is not None else None

    def _parse(self, raw):
        vals = []
        for cell in raw:
            t = cell.get("type", "text")
            v = cell.get("value")
            if t == "null" or v is None:
                vals.append(None)
            elif t == "integer":
                vals.append(int(v))
            elif t == "float":
                vals.append(float(v))
            else:
                vals.append(str(v))
        return _Row(self._cols, tuple(vals))

    async def fetchone(self):
        return self._parse(self._rows[0]) if self._rows else None

    async def fetchall(self):
        return [self._parse(r) for r in self._rows]

    @property
    def lastrowid(self):
        return self._lastrowid


class _TursoHTTPConn:
    """
    Async connection to Turso via the HTTP pipeline API.
    No Rust/C compiler required — pure HTTP over httpx.
    """

    def __init__(self):
        self._url     = _turso_http_url()
        self._headers = {
            "Authorization": f"Bearer {TURSO_TOKEN}",
            "Content-Type": "application/json",
        }

    async def _pipeline(self, requests: list) -> list:
        import httpx
        payload = {"requests": requests + [{"type": "close"}]}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(self._url, json=payload, headers=self._headers)
            resp.raise_for_status()
        return resp.json()["results"]

    async def execute(self, sql: str, params=()):
        stmt: dict = {"sql": sql}
        if params:
            stmt["args"] = [_encode_arg(p) for p in params]
        results = await self._pipeline([{"type": "execute", "stmt": stmt}])
        r = results[0]
        if r.get("type") == "error":
            raise Exception(r["error"]["message"])
        return _TursoHTTPCursor(r["response"]["result"])

    async def executemany(self, sql: str, seq):
        for params in seq:
            await self.execute(sql, params)

    async def commit(self):
        pass  # Turso auto-commits each HTTP request

    async def close(self):
        pass  # stateless HTTP — nothing to close


# ── Context managers ───────────────────────────────────────────────────────────

@asynccontextmanager
async def _turso_ctx():
    yield _TursoHTTPConn()


@asynccontextmanager
async def _sqlite_ctx():
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db


async def get_db():
    """FastAPI Depends() — yields a DB connection."""
    ctx = _turso_ctx() if USE_TURSO else _sqlite_ctx()
    async with ctx as db:
        yield db


# ── Schema ─────────────────────────────────────────────────────────────────────

_TABLES = [
    """CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT    NOT NULL,
        email         TEXT    NOT NULL UNIQUE,
        password      TEXT,
        password_hash TEXT,
        provider      TEXT    DEFAULT 'email',
        provider_id   TEXT,
        avatar_url    TEXT,
        created_at    TEXT    DEFAULT (datetime('now')),
        updated_at    TEXT    DEFAULT (datetime('now'))
    )""",

    """CREATE TABLE IF NOT EXISTS user_paths (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        path_id    TEXT    NOT NULL,
        path_title TEXT    NOT NULL,
        started_at TEXT    DEFAULT (datetime('now')),
        UNIQUE(user_id, path_id)
    )""",

    """CREATE TABLE IF NOT EXISTS module_progress (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL,
        path_id      TEXT    NOT NULL,
        module_index INTEGER NOT NULL,
        status       TEXT    NOT NULL DEFAULT 'locked',
        completed_at TEXT,
        UNIQUE(user_id, path_id, module_index)
    )""",

    """CREATE TABLE IF NOT EXISTS chat_messages (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER,
        session_id TEXT    NOT NULL,
        role       TEXT    NOT NULL,
        content    TEXT    NOT NULL,
        created_at TEXT    DEFAULT (datetime('now'))
    )""",

    """CREATE TABLE IF NOT EXISTS trends (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        category   TEXT    NOT NULL,
        name       TEXT    NOT NULL,
        growth_pct REAL,
        demand     REAL,
        source     TEXT,
        fetched_at TEXT    DEFAULT (datetime('now'))
    )""",

    """CREATE TABLE IF NOT EXISTS password_reset_tokens (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        email      TEXT    NOT NULL,
        otp        TEXT    NOT NULL,
        expires_at TEXT    NOT NULL,
        used       INTEGER DEFAULT 0
    )""",
]


async def init_db():
    """Create all tables on startup."""
    async with (_turso_ctx() if USE_TURSO else _sqlite_ctx()) as db:
        if not USE_TURSO:
            await db.execute("PRAGMA journal_mode=WAL")
        for ddl in _TABLES:
            await db.execute(ddl)
        await db.commit()
    mode = f"Turso cloud ({TURSO_URL})" if USE_TURSO else f"local SQLite at {DB_PATH}"
    print(f"✅ Database initialised — {mode}")
