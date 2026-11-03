"""Conversation history persisted in SQLite.

Why persist: a customer says "my order is 8" and later asks "and when does it
arrive?". Within a session the agent keeps the context in memory; SQLite lets a
conversation survive a restart and be resumed by its session id (as a WhatsApp
integration would key it by phone number).

Messages are stored one row per API message, in order, and only appended:
the stored history is exactly what was sent to the model.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[2] / "var" / "conversations.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    session_id TEXT NOT NULL,
    position   INTEGER NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,  -- JSON (string or list of content blocks)
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, position)
);
"""


def new_session_id() -> str:
    return uuid.uuid4().hex[:8]


class ConversationStore:
    def __init__(self, db_path: Path | str = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def load(self, session_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY position",
                (session_id,),
            ).fetchall()
        return [{"role": role, "content": json.loads(content)} for role, content in rows]

    def save_new(self, session_id: str, messages: list[dict]) -> int:
        """Append the messages not stored yet. Returns how many were written."""
        with self._connect() as conn:
            (stored,) = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
            ).fetchone()
            now = datetime.now().isoformat(timespec="seconds")
            new = messages[stored:]
            conn.executemany(
                "INSERT INTO messages (session_id, position, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                [
                    (session_id, stored + i, m["role"], json.dumps(m["content"], ensure_ascii=False), now)
                    for i, m in enumerate(new)
                ],
            )
        return len(new)

    def sessions(self) -> list[tuple[str, str, int]]:
        """(session_id, last activity, message count), most recent first."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT session_id, MAX(created_at), COUNT(*) FROM messages "
                "GROUP BY session_id ORDER BY MAX(created_at) DESC"
            ).fetchall()
