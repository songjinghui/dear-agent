"""Local, inspectable records of requests sent to configured model providers."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import json
import uuid

from fastapi import HTTPException

from .common import app, connect, now


_call_context: ContextVar[dict] = ContextVar("model_call_context", default={})
MODEL_CALL_LOG_RETENTION_DAYS = 30


@contextmanager
def model_call_context(purpose: str, session_id: str | None = None):
    token = _call_context.set({"purpose": purpose, "session_id": session_id})
    try:
        yield
    finally:
        _call_context.reset(token)


def current_call_context() -> dict:
    return dict(_call_context.get())


def prune_expired_model_call_logs(reference_time: datetime | None = None) -> int:
    """Delete model-call copies that have outlived the local retention period."""
    current_time = reference_time or datetime.now(timezone.utc)
    cutoff = current_time - timedelta(days=MODEL_CALL_LOG_RETENTION_DAYS)
    with connect() as db:
        cursor = db.execute(
            "DELETE FROM model_call_logs WHERE created_at < ?",
            (cutoff.isoformat(timespec="microseconds"),),
        )
    return cursor.rowcount


async def _run_model_call_log_retention() -> None:
    while True:
        await asyncio.sleep(24 * 60 * 60)
        prune_expired_model_call_logs()


@app.on_event("startup")
async def start_model_call_log_retention() -> None:
    prune_expired_model_call_logs()
    app.state.model_call_log_retention_task = asyncio.create_task(_run_model_call_log_retention())


@app.on_event("shutdown")
async def stop_model_call_log_retention() -> None:
    task = getattr(app.state, "model_call_log_retention_task", None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def record_model_call(
    *, provider: str, model: str, purpose: str, request: dict,
    session_id: str | None = None, status: str = "processing",
) -> str:
    log_id = str(uuid.uuid4())
    timestamp = now()
    with connect() as db:
        db.execute(
            "INSERT INTO model_call_logs(id,session_id,provider,model,purpose,request_json,status,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (log_id, session_id, provider, model, purpose,
             json.dumps(request, ensure_ascii=False), status, timestamp, timestamp),
        )
    return log_id


def update_model_call_request(log_id: str, request: dict, model: str | None = None) -> None:
    with connect() as db:
        if model:
            db.execute(
                "UPDATE model_call_logs SET request_json=?,model=?,updated_at=? WHERE id=?",
                (json.dumps(request, ensure_ascii=False), model, now(), log_id),
            )
        else:
            db.execute(
                "UPDATE model_call_logs SET request_json=?,updated_at=? WHERE id=?",
                (json.dumps(request, ensure_ascii=False), now(), log_id),
            )


def finish_model_call(
    log_id: str, *, status: str, response_preview: str | None = None,
    error: str | None = None, duration_ms: int | None = None,
) -> None:
    with connect() as db:
        db.execute(
            "UPDATE model_call_logs SET status=?,response_preview=?,error=?,duration_ms=?,updated_at=? WHERE id=?",
            (status, response_preview, error, duration_ms, now(), log_id),
        )


def _list_item(row) -> dict:
    request = json.loads(row["request_json"])
    messages = request.get("messages", []) if isinstance(request, dict) else []
    if not messages and isinstance(request, dict):
        messages = request.get("input", [])
    preview = ""
    if isinstance(messages, str):
        preview = messages[:180].replace("\n", " ")
    elif isinstance(messages, list):
        for item in reversed(messages):
            if isinstance(item, dict) and isinstance(item.get("content"), str):
                preview = item["content"][:180].replace("\n", " ")
                break
    return {
        "id": row["id"], "session_id": row["session_id"], "provider": row["provider"],
        "model": row["model"], "purpose": row["purpose"], "status": row["status"],
        "created_at": row["created_at"], "duration_ms": row["duration_ms"],
        "error": row["error"], "preview": preview,
    }


@app.get("/api/model-call-logs")
def list_model_call_logs(limit: int = 50, offset: int = 0) -> dict:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    with connect() as db:
        rows = db.execute(
            "SELECT id,session_id,provider,model,purpose,status,request_json,created_at,updated_at,duration_ms,error "
            "FROM model_call_logs ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset),
        ).fetchall()
        total = db.execute("SELECT COUNT(*) FROM model_call_logs").fetchone()[0]
    return {"items": [_list_item(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@app.get("/api/model-call-logs/{log_id}")
def get_model_call_log(log_id: str) -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM model_call_logs WHERE id=?", (log_id,)).fetchone()
    if not row:
        raise HTTPException(404, "模型调用记录不存在。")
    result = dict(row)
    result["request"] = json.loads(result.pop("request_json"))
    return result


@app.delete("/api/model-call-logs/{log_id}")
def delete_model_call_log(log_id: str) -> dict:
    with connect() as db:
        cursor = db.execute("DELETE FROM model_call_logs WHERE id=?", (log_id,))
    if cursor.rowcount == 0:
        raise HTTPException(404, "模型调用记录不存在。")
    return {"deleted": True}


@app.delete("/api/model-call-logs")
def clear_model_call_logs() -> dict:
    with connect() as db:
        cursor = db.execute("DELETE FROM model_call_logs")
    return {"deleted": cursor.rowcount}
