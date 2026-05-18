from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.models import CallContext, CallStatus, utc_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    caller_number TEXT,
    dialed_number TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS call_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    recording_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    format TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    confidence REAL NOT NULL,
    explanation TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_candidate_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    text TEXT NOT NULL,
    language TEXT NOT NULL,
    model TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_call(self, call: CallContext) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO calls (id, channel_id, caller_number, dialed_number, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    channel_id=excluded.channel_id,
                    caller_number=excluded.caller_number,
                    dialed_number=excluded.dialed_number,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (
                    call.call_id,
                    call.channel_id,
                    call.caller_number,
                    call.dialed_number,
                    call.status.value,
                    now,
                    now,
                ),
            )

    def add_event(self, call_id: str, event_type: str, payload: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO call_events (call_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
                (call_id, event_type, payload, utc_now()),
            )

    def add_recording(self, call_id: str, recording_name: str, file_path: str, fmt: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO recordings (call_id, recording_name, file_path, format, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (call_id, recording_name, file_path, fmt, utc_now()),
            )

    def add_decision(self, call_id: str, decision: str, confidence: float, explanation: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO decisions (call_id, decision, confidence, explanation, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (call_id, decision, confidence, explanation, utc_now()),
            )

    def add_transcript(self, call_id: str, file_path: str, text: str, language: str, model: str, source: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO transcripts (call_id, file_path, text, language, model, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (call_id, file_path, text, language, model, source, utc_now()),
            )

    def list_calls(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM calls ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def get_call(self, call_id: str) -> dict | None:
        with self.connect() as conn:
            call = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
            if call is None:
                return None
            events = conn.execute(
                "SELECT event_type, payload, created_at FROM call_events WHERE call_id = ? ORDER BY id ASC",
                (call_id,),
            ).fetchall()
            recordings = conn.execute(
                "SELECT recording_name, file_path, format, created_at FROM recordings WHERE call_id = ? ORDER BY id ASC",
                (call_id,),
            ).fetchall()
            decisions = conn.execute(
                "SELECT decision, confidence, explanation, created_at FROM decisions WHERE call_id = ? ORDER BY id ASC",
                (call_id,),
            ).fetchall()
            transcripts = conn.execute(
                "SELECT file_path, text, language, model, source, created_at FROM transcripts WHERE call_id = ? ORDER BY id ASC",
                (call_id,),
            ).fetchall()
        payload = dict(call)
        payload["events"] = [dict(row) for row in events]
        payload["recordings"] = [dict(row) for row in recordings]
        payload["decisions"] = [dict(row) for row in decisions]
        payload["transcripts"] = [dict(row) for row in transcripts]
        return payload

    def update_call_status(self, call_id: str, status: CallStatus) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE calls SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, utc_now(), call_id),
            )
