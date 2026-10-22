"""HTTP endpoints and handlers for the session domain."""

from .common import *  # noqa: F403


def get_session_chat_status(session_id: str) -> dict:
    """Report whether the latest locally sent message still needs a reply."""
    with connect() as db:
        session = db.execute("SELECT origin FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not session:
            raise HTTPException(404, "对话不存在")
        if session["origin"] not in {"native", "continued"}:
            raise HTTPException(403, "历史会话请在「历史档案」中查看。")
        latest = db.execute(
            "SELECT role,source_message_id,created_at FROM messages WHERE session_id=? "
            "ORDER BY sequence DESC,created_at DESC,rowid DESC LIMIT 1", (session_id,),
        ).fetchone()
        if not latest or latest["role"] != "user" or latest["source_message_id"] is not None:
            return {"status": "idle", "error": None}
        call = db.execute(
            "SELECT status,error,created_at,updated_at FROM model_call_logs "
            "WHERE session_id=? AND created_at>=? ORDER BY created_at DESC LIMIT 1",
            (session_id, latest["created_at"]),
        ).fetchone()

    timestamp = datetime.fromisoformat(latest["created_at"])
    age_seconds = (datetime.now(timestamp.tzinfo) - timestamp).total_seconds()
    if call is None:
        if age_seconds < 300:
            return {"status": "processing", "error": None}
        return {"status": "interrupted", "error": "这条消息暂时没有生成回复。你的消息仍保留在会话中。"}
    if call["status"] == "processing":
        return {"status": "processing", "error": None}
    if call["status"] == "complete":
        call_updated = datetime.fromisoformat(call["updated_at"])
        if (datetime.now(call_updated.tzinfo) - call_updated).total_seconds() < 15:
            return {"status": "processing", "error": None}
        return {"status": "interrupted", "error": "模型已完成，但回复没有保存到会话。你的消息仍保留在会话中。"}
    return {"status": "interrupted", "error": call["error"] or "这条消息没有完成回复。你的消息仍保留在会话中。"}


@app.get("/api/sessions/{session_id}/chat-status")
def session_chat_status(session_id: str) -> dict:
    return get_session_chat_status(session_id)

@app.get("/api/sessions")
def list_sessions() -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT * FROM sessions WHERE origin IN ('native','continued') ORDER BY updated_at DESC LIMIT 200").fetchall()
    return hydrate_message_attachments(rows)


class SessionInput(BaseModel):
    title: str = "新的对话"


@app.post("/api/sessions")
def create_session(payload: SessionInput = SessionInput()) -> dict:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT s.* FROM sessions s WHERE s.origin='native' "
            "AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.session_id=s.id) "
            "ORDER BY s.updated_at DESC LIMIT 1"
        ).fetchone()
        if existing:
            return dict(existing)
        mode_row = db.execute("SELECT value FROM settings WHERE key='default_mode'").fetchone()
        default_mode = mode_row["value"] if mode_row and mode_row["value"] in {"chat", "manager"} else "chat"
        session = {"id": str(uuid.uuid4()), "title": payload.title[:80], "created_at": now(), "updated_at": now(), "mode": default_mode}
        db.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at,mode,origin) VALUES (?,?,?,?,?, 'native')",
            (session["id"], session["title"], session["created_at"], session["updated_at"], session["mode"]),
        )
    session["origin"] = "native"
    return session


class SessionModeInput(BaseModel):
    mode: Literal["chat", "manager"]


@app.patch("/api/sessions/{session_id}/mode")
def set_session_mode(session_id: str, payload: SessionModeInput) -> dict:
    with connect() as db:
        session = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not session:
            raise HTTPException(404, "对话不存在")
        if session["origin"] not in {"native", "continued"}:
            raise HTTPException(403, "历史档案请先选择「继续此会话」。")
        db.execute("UPDATE sessions SET mode=?,updated_at=? WHERE id=?", (payload.mode, now(), session_id))
        updated = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    return dict(updated)


@app.get("/api/sessions/{session_id}/messages")
def list_messages(session_id: str) -> list[dict]:
    with connect() as db:
        session = db.execute("SELECT origin FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not session:
            raise HTTPException(404, "对话不存在")
        if session["origin"] not in {"native", "continued"}:
            raise HTTPException(403, "历史会话请在「历史档案」中查看。")
        message_query = (
            "SELECT id, role, content, created_at, speaker, speaker_label, sequence,source_message_id,"
            "CASE WHEN source_message_id IS NOT NULL THEN 'imported' ELSE ? END AS origin "
            "FROM messages WHERE session_id=? ORDER BY sequence, created_at, rowid"
        )
        if session["origin"] == "continued":
            message_query = (
                "SELECT * FROM (" + message_query.replace("ORDER BY sequence, created_at, rowid", "ORDER BY sequence DESC, created_at DESC, rowid DESC LIMIT 100") + ") "
                "ORDER BY sequence,created_at"
            )
        rows = db.execute(message_query, (session["origin"], session_id)).fetchall()
    return [dict(row) for row in rows]
