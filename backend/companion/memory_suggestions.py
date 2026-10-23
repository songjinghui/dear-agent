"""Persistence and user decisions for memory candidates proposed during chat."""

from __future__ import annotations

import json
import uuid
from typing import Literal

from .common import *  # noqa: F403
from .memory_routes import MemoryInput, upsert_memory
from .memory_protocol import parse_agent_response


class MemorySuggestionApproval(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=5000)
    memory_type: Literal["working", "episodic", "semantic"] | None = None
    importance: int | None = Field(default=None, ge=1, le=5)
    sensitivity: Literal["normal", "sensitive", "private"] | None = None
    expires_at: str | None = None


def save_memory_suggestions(session_id: str, message_id: str, candidates: list[dict]) -> list[dict]:
    timestamp = now()
    created_ids = []
    with connect() as db:
        valid_source = db.execute(
            "SELECT 1 FROM messages WHERE id=? AND session_id=? AND role='user'",
            (message_id, session_id),
        ).fetchone()
        if not valid_source:
            return []
        for candidate in candidates:
            content = candidate["content"].strip()
            already_saved = db.execute(
                "SELECT 1 FROM memories WHERE lower(trim(content))=lower(trim(?)) LIMIT 1", (content,),
            ).fetchone()
            if already_saved:
                continue
            suggestion_id = str(uuid.uuid4())
            cursor = db.execute(
                "INSERT OR IGNORE INTO memory_suggestions(id,session_id,message_id,content,memory_type,importance,sensitivity,evidence,rationale,expires_at,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?, 'pending',?,?)",
                (
                    suggestion_id, session_id, message_id, content, candidate["memory_type"],
                    candidate["importance"], candidate["sensitivity"], candidate["evidence"],
                    candidate["rationale"], candidate["expires_at"], timestamp, timestamp,
                ),
            )
            if cursor.rowcount:
                created_ids.append(suggestion_id)
        if not created_ids:
            return []
        placeholders = ",".join("?" for _ in created_ids)
        rows = db.execute(
            f"SELECT * FROM memory_suggestions WHERE id IN ({placeholders}) ORDER BY created_at, id",
            created_ids,
        ).fetchall()
    return [dict(row) for row in rows]


def process_agent_response(raw: str, user_text: str, session_id: str, message_id: str) -> dict:
    parsed = parse_agent_response(raw, user_text)
    suggestions = save_memory_suggestions(session_id, message_id, parsed["memory_candidates"])
    return {"reply": parsed["reply"], "memory_suggestions": suggestions}


def list_memory_suggestions(session_id: str | None = None) -> list[dict]:
    with connect() as db:
        if session_id:
            rows = db.execute(
                "SELECT * FROM memory_suggestions WHERE status='pending' AND session_id=? ORDER BY created_at DESC",
                (session_id,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM memory_suggestions WHERE status='pending' ORDER BY created_at DESC",
            ).fetchall()
    return [dict(row) for row in rows]


def approve_memory_suggestion(suggestion_id: str, updates: dict | None = None) -> dict:
    with connect() as db:
        row = db.execute(
            "SELECT * FROM memory_suggestions WHERE id=? AND status='pending'", (suggestion_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "待确认的记忆建议不存在")
    suggestion = dict(row)
    suggestion.update(updates or {})
    source_refs = [{
        "kind": "conversation", "session_id": suggestion["session_id"],
        "message_id": suggestion["message_id"], "quote": suggestion["evidence"],
    }]
    memory = upsert_memory(
        str(uuid.uuid4()),
        MemoryInput(
            content=suggestion["content"], memory_type=suggestion["memory_type"],
            importance=suggestion["importance"], sensitivity=suggestion["sensitivity"],
            expires_at=suggestion.get("expires_at"),
        ),
        source="日常对话记忆建议（用户确认）",
        source_refs=source_refs,
    )
    with connect() as db:
        db.execute(
            "UPDATE memory_suggestions SET status='approved', memory_id=?, content=?, memory_type=?, importance=?, sensitivity=?, expires_at=?, updated_at=? WHERE id=? AND status='pending'",
            (
                memory["id"], suggestion["content"], suggestion["memory_type"],
                suggestion["importance"], suggestion["sensitivity"], suggestion.get("expires_at"),
                now(), suggestion_id,
            ),
        )
    memory["source_refs"] = json.loads(memory["source_refs"])
    return memory


def dismiss_memory_suggestion(suggestion_id: str) -> dict:
    with connect() as db:
        cursor = db.execute(
            "UPDATE memory_suggestions SET status='dismissed',updated_at=? WHERE id=? AND status='pending'",
            (now(), suggestion_id),
        )
    if not cursor.rowcount:
        raise HTTPException(404, "待确认的记忆建议不存在")
    return {"dismissed": True}


@app.get("/api/memory-suggestions")
def get_memory_suggestions(session_id: str | None = None) -> list[dict]:
    return list_memory_suggestions(session_id)


@app.post("/api/memory-suggestions/{suggestion_id}/approve")
def approve_memory_suggestion_route(
    suggestion_id: str, payload: MemorySuggestionApproval | None = None,
) -> dict:
    updates = payload.model_dump(exclude_unset=True) if payload else None
    return approve_memory_suggestion(suggestion_id, updates)


@app.delete("/api/memory-suggestions/{suggestion_id}")
def dismiss_memory_suggestion_route(suggestion_id: str) -> dict:
    return dismiss_memory_suggestion(suggestion_id)
