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


class MemorySuggestionGroupApproval(BaseModel):
    suggestion_ids: list[str] = Field(min_length=1, max_length=20)
    content: str = Field(min_length=1, max_length=5000)
    memory_type: Literal["working", "episodic", "semantic"] = "semantic"
    importance: int = Field(default=3, ge=1, le=5)
    sensitivity: Literal["normal", "sensitive", "private"] = "normal"
    expires_at: str | None = None


class MemorySuggestionGroupDismissal(BaseModel):
    suggestion_ids: list[str] = Field(min_length=1, max_length=20)


def pending_normal_memory_suggestions(session_id: str, limit: int = 8) -> list[dict]:
    """Return only normal candidates that may safely guide topic consolidation."""
    with connect() as db:
        rows = db.execute(
            "SELECT id,topic,content,memory_type FROM memory_suggestions "
            "WHERE session_id=? AND status='pending' AND sensitivity='normal' "
            "ORDER BY updated_at DESC,id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def _evidence_refs(raw: str | None, message_id: str, evidence: str) -> list[dict]:
    try:
        refs = json.loads(raw or "[]")
    except json.JSONDecodeError:
        refs = []
    valid = [
        {"message_id": item["message_id"], "quote": item["quote"]}
        for item in refs
        if isinstance(item, dict) and isinstance(item.get("message_id"), str) and isinstance(item.get("quote"), str)
    ]
    return valid or [{"message_id": message_id, "quote": evidence}]


def _append_evidence_ref(refs: list[dict], message_id: str, evidence: str) -> list[dict]:
    if any(item["message_id"] == message_id and item["quote"] == evidence for item in refs):
        return refs
    return [*refs, {"message_id": message_id, "quote": evidence}]


def _evidence_text(refs: list[dict]) -> str:
    return "\n\n".join(item["quote"] for item in refs)


def _candidate_topic(candidate: dict) -> str:
    topic = candidate.get("topic", "")
    return topic.strip()[:120] if isinstance(topic, str) else ""


def _pending_group_rows(suggestion_ids: list[str]) -> list[dict]:
    ids = list(dict.fromkeys(suggestion_ids))
    if not ids or len(ids) != len(suggestion_ids):
        raise HTTPException(422, "主题记忆需要不重复的待确认条目")
    placeholders = ",".join("?" for _ in ids)
    with connect() as db:
        rows = db.execute(
            f"SELECT * FROM memory_suggestions WHERE id IN ({placeholders}) AND status='pending'",
            ids,
        ).fetchall()
    by_id = {row["id"]: dict(row) for row in rows}
    if len(by_id) != len(ids):
        raise HTTPException(404, "待确认的记忆建议不存在")
    ordered = [by_id[suggestion_id] for suggestion_id in ids]
    if len({row["session_id"] for row in ordered}) != 1:
        raise HTTPException(422, "一个主题记忆只能合并同一会话中的候选")
    if len({str(row.get("topic") or "").strip() for row in ordered}) != 1:
        raise HTTPException(422, "一个主题记忆只能合并同一主题的候选")
    return ordered


def _group_source_refs(rows: list[dict]) -> list[dict]:
    refs = []
    seen = set()
    for row in rows:
        for evidence in _evidence_refs(row.get("evidence_refs"), row["message_id"], row["evidence"]):
            key = (row["session_id"], evidence["message_id"], evidence["quote"])
            if key in seen:
                continue
            seen.add(key)
            refs.append({
                "kind": "conversation", "session_id": row["session_id"],
                "message_id": evidence["message_id"], "quote": evidence["quote"],
            })
    return refs


def save_memory_suggestions(session_id: str, message_id: str, candidates: list[dict]) -> list[dict]:
    timestamp = now()
    touched_ids = []
    with connect() as db:
        valid_source = db.execute(
            "SELECT 1 FROM messages WHERE id=? AND session_id=? AND role='user'",
            (message_id, session_id),
        ).fetchone()
        if not valid_source:
            return []
        for candidate in candidates:
            content = candidate["content"].strip()
            topic = _candidate_topic(candidate)
            already_saved = db.execute(
                "SELECT 1 FROM memories WHERE lower(trim(content))=lower(trim(?)) LIMIT 1", (content,),
            ).fetchone()
            if already_saved:
                continue
            merge_target = None
            merge_with = candidate.get("merge_with")
            if candidate["sensitivity"] == "normal" and isinstance(merge_with, str):
                merge_target = db.execute(
                    "SELECT * FROM memory_suggestions WHERE id=? AND session_id=? "
                    "AND status='pending' AND sensitivity='normal'",
                    (merge_with, session_id),
                ).fetchone()
            if not merge_target:
                merge_target = db.execute(
                    "SELECT * FROM memory_suggestions WHERE session_id=? AND status='pending' "
                    "AND lower(trim(content))=lower(trim(?)) LIMIT 1",
                    (session_id, content),
                ).fetchone()
            if merge_target:
                target = dict(merge_target)
                refs = _append_evidence_ref(
                    _evidence_refs(target.get("evidence_refs"), target["message_id"], target["evidence"]),
                    message_id,
                    candidate["evidence"],
                )
                db.execute(
                    "UPDATE memory_suggestions SET message_id=?,topic=?,content=?,memory_type=?,importance=?,sensitivity=?,"
                    "evidence=?,evidence_refs=?,rationale=?,expires_at=?,updated_at=? WHERE id=?",
                    (
                        message_id, topic or target.get("topic", ""), content, candidate["memory_type"],
                        max(target["importance"], candidate["importance"]), candidate["sensitivity"],
                        _evidence_text(refs), json.dumps(refs, ensure_ascii=False), candidate["rationale"],
                        candidate["expires_at"], timestamp, target["id"],
                    ),
                )
                touched_ids.append(target["id"])
                continue
            suggestion_id = str(uuid.uuid4())
            cursor = db.execute(
                "INSERT OR IGNORE INTO memory_suggestions(id,session_id,message_id,content,topic,memory_type,importance,sensitivity,evidence,evidence_refs,rationale,expires_at,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'pending',?,?)",
                (
                    suggestion_id, session_id, message_id, content, topic, candidate["memory_type"],
                    candidate["importance"], candidate["sensitivity"], candidate["evidence"],
                    json.dumps([{"message_id": message_id, "quote": candidate["evidence"]}], ensure_ascii=False),
                    candidate["rationale"], candidate["expires_at"], timestamp, timestamp,
                ),
            )
            if cursor.rowcount:
                touched_ids.append(suggestion_id)
        if not touched_ids:
            return []
        placeholders = ",".join("?" for _ in touched_ids)
        rows = db.execute(
            f"SELECT * FROM memory_suggestions WHERE id IN ({placeholders}) ORDER BY created_at, id",
            touched_ids,
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
    evidence_refs = _evidence_refs(
        suggestion.get("evidence_refs"), suggestion["message_id"], suggestion["evidence"],
    )
    source_refs = [
        {
            "kind": "conversation", "session_id": suggestion["session_id"],
            "message_id": item["message_id"], "quote": item["quote"],
        }
        for item in evidence_refs
    ]
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


def approve_memory_suggestion_group(suggestion_ids: list[str], updates: dict) -> dict:
    rows = _pending_group_rows(suggestion_ids)
    requested_sensitivity = updates.get("sensitivity", "normal")
    sensitivity_rank = {"normal": 0, "sensitive": 1, "private": 2}
    if requested_sensitivity not in sensitivity_rank:
        raise HTTPException(422, "隐私级别无效")
    required_sensitivity = max(rows, key=lambda row: sensitivity_rank[row["sensitivity"]])["sensitivity"]
    if sensitivity_rank[requested_sensitivity] < sensitivity_rank[required_sensitivity]:
        raise HTTPException(422, "主题记忆不能降低任一候选的隐私级别")
    content = str(updates.get("content", "")).strip()
    if not content or len(content) > 5000:
        raise HTTPException(422, "主题记忆内容不能为空且不能超过 5000 字")
    memory_type = updates.get("memory_type")
    if memory_type not in {"working", "episodic", "semantic"}:
        raise HTTPException(422, "记忆类型无效")
    importance = updates.get("importance")
    if isinstance(importance, bool) or not isinstance(importance, int) or not 1 <= importance <= 5:
        raise HTTPException(422, "重要度无效")
    source_refs = _group_source_refs(rows)
    memory = upsert_memory(
        str(uuid.uuid4()),
        MemoryInput(
            content=content, memory_type=memory_type, importance=importance,
            sensitivity=requested_sensitivity, expires_at=updates.get("expires_at"),
        ),
        source="日常对话主题记忆（用户确认）",
        source_refs=source_refs,
    )
    placeholders = ",".join("?" for _ in rows)
    with connect() as db:
        db.execute(
            f"UPDATE memory_suggestions SET status='approved',memory_id=?,updated_at=? "
            f"WHERE id IN ({placeholders}) AND status='pending'",
            (memory["id"], now(), *[row["id"] for row in rows]),
        )
    memory["source_refs"] = source_refs
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


def dismiss_memory_suggestion_group(suggestion_ids: list[str]) -> dict:
    rows = _pending_group_rows(suggestion_ids)
    placeholders = ",".join("?" for _ in rows)
    with connect() as db:
        db.execute(
            f"UPDATE memory_suggestions SET status='dismissed',updated_at=? "
            f"WHERE id IN ({placeholders}) AND status='pending'",
            (now(), *[row["id"] for row in rows]),
        )
    return {"dismissed": len(rows)}


@app.get("/api/memory-suggestions")
def get_memory_suggestions(session_id: str | None = None) -> list[dict]:
    return list_memory_suggestions(session_id)


@app.post("/api/memory-suggestions/{suggestion_id}/approve")
def approve_memory_suggestion_route(
    suggestion_id: str, payload: MemorySuggestionApproval | None = None,
) -> dict:
    updates = payload.model_dump(exclude_unset=True) if payload else None
    return approve_memory_suggestion(suggestion_id, updates)


@app.post("/api/memory-suggestion-groups/approve")
def approve_memory_suggestion_group_route(payload: MemorySuggestionGroupApproval) -> dict:
    return approve_memory_suggestion_group(payload.suggestion_ids, payload.model_dump(exclude={"suggestion_ids"}))


@app.post("/api/memory-suggestion-groups/dismiss")
def dismiss_memory_suggestion_group_route(payload: MemorySuggestionGroupDismissal) -> dict:
    return dismiss_memory_suggestion_group(payload.suggestion_ids)


@app.delete("/api/memory-suggestions/{suggestion_id}")
def dismiss_memory_suggestion_route(suggestion_id: str) -> dict:
    return dismiss_memory_suggestion(suggestion_id)
