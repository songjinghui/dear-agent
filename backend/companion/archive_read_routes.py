"""HTTP endpoints and handlers for the archive read domain."""

from .common import *  # noqa: F403
from . import common as app_state


class ArchiveSessionTitleInput(BaseModel):
    title: str = Field(min_length=1, max_length=80)


@app.get("/api/archives/imports")
def list_archive_imports() -> list[dict]:
    with connect() as db:
        rows = db.execute(
            "SELECT a.id AS archive_id,a.source_platform,a.expected_parts,a.created_at,a.updated_at,COUNT(DISTINCT p.part_id) AS imported_parts,"
            "(SELECT COUNT(*) FROM messages m JOIN sessions s ON s.id=m.session_id WHERE s.import_id=a.id AND m.source_message_id IS NOT NULL) AS message_count,"
            "(SELECT COUNT(*) FROM archive_summaries x WHERE x.archive_id=a.id) AS summary_count,"
            "(SELECT COUNT(*) FROM archive_attachments x WHERE x.archive_id=a.id) AS image_count "
            "FROM archive_imports a LEFT JOIN archive_import_parts p ON p.archive_id=a.id GROUP BY a.id ORDER BY a.updated_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


@app.delete("/api/archives/{archive_id}")
def delete_archive(archive_id: str) -> dict:
    storage_names: set[str] = set()
    with connect() as db:
        exists = db.execute("SELECT 1 FROM archive_imports WHERE id=?", (archive_id,)).fetchone()
        if not exists:
            raise HTTPException(404, "导入档案不存在。")
        storage_names = {row["storage_name"] for row in db.execute("SELECT DISTINCT storage_name FROM archive_attachments WHERE archive_id=?", (archive_id,)).fetchall()}
        sessions = db.execute("SELECT id FROM sessions WHERE import_id=?", (archive_id,)).fetchall()
        deleted_sessions = 0
        preserved_sessions = 0
        for session in sessions:
            session_id = session["id"]
            db.execute("DELETE FROM session_context_summaries WHERE session_id=?", (session_id,))
            continuations = db.execute(
                "SELECT id FROM messages WHERE session_id=? AND source_message_id IS NULL ORDER BY sequence",
                (session_id,),
            ).fetchall()
            imported = db.execute(
                "SELECT id FROM messages WHERE session_id=? AND source_message_id IS NOT NULL",
                (session_id,),
            ).fetchall()
            try:
                db.executemany("DELETE FROM messages_fts WHERE message_id=?", [(row["id"],) for row in imported])
            except sqlite3.OperationalError:
                pass
            if continuations:
                db.execute("DELETE FROM messages WHERE session_id=? AND source_message_id IS NOT NULL", (session_id,))
                # The source archive is going away. Keep the user's new turns as
                # a normal local conversation and make its sequence contiguous.
                db.execute("UPDATE messages SET sequence=0 WHERE session_id=?", (session_id,))
                for sequence, message in enumerate(continuations, start=1):
                    db.execute("UPDATE messages SET sequence=? WHERE id=?", (sequence, message["id"]))
                db.execute(
                    "UPDATE sessions SET origin='native',import_id=NULL,source_conversation_id=NULL,updated_at=? WHERE id=?",
                    (now(), session_id),
                )
                preserved_sessions += 1
            else:
                db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
                deleted_sessions += 1
        db.execute("DELETE FROM archive_summaries WHERE archive_id=?", (archive_id,))
        db.execute("DELETE FROM archive_analysis_jobs WHERE archive_id=?", (archive_id,))
        for memory in db.execute("SELECT id,source_refs FROM memories WHERE source='导入档案审核采纳'").fetchall():
            try:
                refs = json.loads(memory["source_refs"] or "[]")
            except (TypeError, json.JSONDecodeError):
                continue
            changed = False
            for reference in refs:
                if reference.get("archive_id") == archive_id:
                    reference["source_deleted"] = True
                    changed = True
            if changed:
                db.execute("UPDATE memories SET source_refs=? WHERE id=?", (json.dumps(refs, ensure_ascii=False), memory["id"]))
        db.execute("DELETE FROM archive_imports WHERE id=?", (archive_id,))
    for storage_name in storage_names:
        with connect() as db:
            referenced = db.execute("SELECT 1 FROM archive_attachments WHERE storage_name=? LIMIT 1", (storage_name,)).fetchone()
        if not referenced:
            (app_state.DATA_DIR / "attachments" / storage_name).unlink(missing_ok=True)
    return {"deleted": True, "deleted_sessions": deleted_sessions, "preserved_sessions": preserved_sessions}


@app.delete("/api/archives/sessions/{session_id}")
def delete_archive_session(session_id: str) -> dict:
    storage_names: set[str] = set()
    archive_id: str | None = None
    with connect() as db:
        session = db.execute(
            "SELECT id,import_id FROM sessions WHERE id=? AND origin IN ('imported','continued')",
            (session_id,),
        ).fetchone()
        if not session:
            raise HTTPException(404, "历史会话不存在。")
        archive_id = session["import_id"]
        message_ids = [row["id"] for row in db.execute("SELECT id FROM messages WHERE session_id=?", (session_id,)).fetchall()]
        if message_ids:
            storage_names = {row["storage_name"] for row in db.execute(
                "SELECT DISTINCT a.storage_name FROM archive_attachments a JOIN messages m ON m.id=a.message_id WHERE m.session_id=?",
                (session_id,),
            ).fetchall()}
            try:
                db.executemany("DELETE FROM messages_fts WHERE message_id=?", [(message_id,) for message_id in message_ids])
            except sqlite3.OperationalError:
                pass
        db.execute("DELETE FROM archive_analysis_jobs WHERE session_id=?", (session_id,))
        db.execute("DELETE FROM archive_summaries WHERE conversation_id=?", (session_id,))
        db.execute("DELETE FROM sessions WHERE id=?", (session_id,))

        archive_remaining = 0
        if archive_id:
            archive_remaining = db.execute("SELECT COUNT(*) FROM sessions WHERE import_id=?", (archive_id,)).fetchone()[0]
        for memory in db.execute("SELECT id,source_refs FROM memories WHERE source='导入档案审核采纳'").fetchall():
            try:
                refs = json.loads(memory["source_refs"] or "[]")
            except (TypeError, json.JSONDecodeError):
                continue
            changed = False
            for reference in refs:
                if reference.get("session_id") == session_id or (
                    archive_id and archive_remaining == 0 and reference.get("archive_id") == archive_id
                ):
                    reference["source_deleted"] = True
                    changed = True
            if changed:
                db.execute("UPDATE memories SET source_refs=? WHERE id=?", (json.dumps(refs, ensure_ascii=False), memory["id"]))

        deleted_archive = bool(archive_id and archive_remaining == 0)
        if deleted_archive:
            db.execute("DELETE FROM archive_imports WHERE id=?", (archive_id,))

    for storage_name in storage_names:
        with connect() as db:
            referenced = db.execute("SELECT 1 FROM archive_attachments WHERE storage_name=? LIMIT 1", (storage_name,)).fetchone()
        if not referenced:
            (app_state.DATA_DIR / "attachments" / storage_name).unlink(missing_ok=True)
    return {"deleted": True, "deleted_archive": deleted_archive}


@app.get("/api/archives/sessions")
def list_archive_sessions(q: str = "", limit: int = 50, offset: int = 0) -> dict:
    limit = max(1, min(limit, 100))
    with connect() as db:
        if q.strip():
            rows = db.execute(
                "SELECT s.id,s.title,s.source_platform,s.import_id,s.created_at,s.updated_at,COUNT(m.id) AS message_count "
                "FROM sessions s JOIN messages m ON m.session_id=s.id WHERE s.origin IN ('imported','continued') AND s.title LIKE ? "
                "GROUP BY s.id ORDER BY s.created_at DESC LIMIT ? OFFSET ?",
                (f"%{q.strip()}%", limit, max(0, offset)),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT s.id,s.title,s.source_platform,s.import_id,s.created_at,s.updated_at,COUNT(m.id) AS message_count "
                "FROM sessions s LEFT JOIN messages m ON m.session_id=s.id WHERE s.origin IN ('imported','continued') "
                "GROUP BY s.id ORDER BY s.created_at DESC LIMIT ? OFFSET ?",
                (limit, max(0, offset)),
            ).fetchall()
        total = db.execute("SELECT COUNT(*) FROM sessions WHERE origin IN ('imported','continued')").fetchone()[0]
    return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": max(0, offset)}


@app.patch("/api/archives/sessions/{session_id}/title")
def rename_archive_session(session_id: str, payload: ArchiveSessionTitleInput) -> dict:
    title = payload.title.strip()
    if not title:
        raise HTTPException(422, "会话名称不能为空。")
    if len(title) > 80:
        raise HTTPException(422, "会话名称不能超过 80 个字符。")
    with connect() as db:
        cursor = db.execute(
            "UPDATE sessions SET title=?,updated_at=? WHERE id=? AND origin IN ('imported','continued')",
            (title, now(), session_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "历史会话不存在。")
        row = db.execute(
            "SELECT id,title,source_platform,import_id,created_at,updated_at FROM sessions WHERE id=?",
            (session_id,),
        ).fetchone()
    return dict(row)


@app.get("/api/archives/sessions/{session_id}/messages")
def list_archive_messages(session_id: str, limit: int = 100, offset: int = 0) -> dict:
    limit = max(1, min(limit, 200))
    with connect() as db:
        session = db.execute(
            "SELECT s.id,s.title,s.source_platform,s.import_id,s.origin,s.created_at,s.updated_at,COUNT(m.id) AS message_count,"
            "CASE WHEN s.import_id IS NULL THEN 1 WHEN (SELECT COUNT(*) FROM archive_import_parts p WHERE p.archive_id=s.import_id) >= "
            "(SELECT expected_parts FROM archive_imports a WHERE a.id=s.import_id) THEN 1 ELSE 0 END AS continuable "
            "FROM sessions s LEFT JOIN messages m ON m.session_id=s.id WHERE s.id=? AND s.origin IN ('imported','continued') GROUP BY s.id",
            (session_id,),
        ).fetchone()
        if not session:
            raise HTTPException(404, "历史会话不存在。")
        rows = db.execute(
            "SELECT id,role,content,created_at,speaker,speaker_label,sequence,source_timestamp FROM messages WHERE session_id=? ORDER BY sequence LIMIT ? OFFSET ?",
            (session_id, limit, max(0, offset)),
        ).fetchall()
        summaries = db.execute(
            "SELECT source_summary_id,content,based_on_message_ids FROM archive_summaries WHERE conversation_id=? ORDER BY created_at",
            (session_id,),
        ).fetchall()
        total = db.execute("SELECT COUNT(*) FROM messages WHERE session_id=?", (session_id,)).fetchone()[0]
    return {"session": dict(session), "items": hydrate_message_attachments(rows), "summaries": [dict(row) for row in summaries], "total": total, "limit": limit, "offset": max(0, offset)}


@app.post("/api/archives/sessions/{session_id}/continue")
def continue_archive_session(session_id: str) -> dict:
    with connect() as db:
        session = db.execute(
            "SELECT s.id,s.title,s.origin,s.import_id,a.expected_parts,"
            "(SELECT COUNT(*) FROM archive_import_parts p WHERE p.archive_id=s.import_id) AS imported_parts "
            "FROM sessions s LEFT JOIN archive_imports a ON a.id=s.import_id WHERE s.id=? AND s.origin IN ('imported','continued')",
            (session_id,),
        ).fetchone()
        if not session:
            raise HTTPException(404, "历史会话不存在。")
        if session["import_id"] and session["imported_parts"] < session["expected_parts"]:
            raise HTTPException(409, "档案分片尚未全部导入，完成导入后才能继续此会话。")
        if session["origin"] == "imported":
            db.execute("UPDATE sessions SET origin='continued',updated_at=? WHERE id=?", (now(), session_id))
    return {"id": session["id"], "title": session["title"], "origin": "continued"}


def search_archive_messages(query: str, limit: int = 8) -> list[dict]:
    terms = [token.strip() for token in jieba.cut(query) if re.fullmatch(r"[\w\u3400-\u9fff]+", token.strip())]
    segmented = " OR ".join(f'"{term}"' for term in terms[:12])
    with connect() as db:
        try:
            rows = db.execute(
                "SELECT m.id,m.session_id,m.role,m.content,m.created_at,m.speaker,m.speaker_label,m.sequence,s.title,s.source_platform "
                ",m.source_timestamp "
                "FROM messages_fts f JOIN messages m ON m.id=f.message_id JOIN sessions s ON s.id=m.session_id "
                "WHERE messages_fts MATCH ? AND s.origin IN ('imported','continued') ORDER BY bm25(messages_fts) LIMIT ?",
                (segmented, limit),
            ).fetchall() if segmented else []
        except sqlite3.OperationalError:
            rows = []
        if not rows:
            fallback_terms = [item for item in re.findall(r"[\w\u3400-\u9fff]+", query) if len(item) > 1][:5]
            if fallback_terms:
                where = " OR ".join("m.content LIKE ?" for _ in fallback_terms)
                rows = db.execute(
                    f"SELECT m.id,m.session_id,m.role,m.content,m.created_at,m.speaker,m.speaker_label,m.sequence,s.title,s.source_platform,m.source_timestamp FROM messages m JOIN sessions s ON s.id=m.session_id WHERE s.origin IN ('imported','continued') AND ({where}) ORDER BY m.created_at DESC LIMIT ?",
                    (*[f"%{term}%" for term in fallback_terms], limit),
                ).fetchall()
    return [dict(row) for row in rows]


def search_history_context(query: str, exclude_session_id: str, limit: int = 5) -> list[dict]:
    terms = [token.strip() for token in jieba.cut(query) if re.fullmatch(r"[\w\u3400-\u9fff]+", token.strip())]
    segmented = " OR ".join(f'"{term}"' for term in terms[:12])
    with connect() as db:
        try:
            hits = [dict(row) for row in db.execute(
                "SELECT m.id,m.session_id,m.content,m.created_at,m.speaker,m.speaker_label,m.sequence,m.source_timestamp,s.title,s.source_platform,s.origin "
                "FROM messages_fts f JOIN messages m ON m.id=f.message_id JOIN sessions s ON s.id=m.session_id "
                "WHERE messages_fts MATCH ? AND s.id<>? AND (s.origin='native' OR s.import_id IS NULL OR "
                "(SELECT COUNT(*) FROM archive_import_parts p WHERE p.archive_id=s.import_id) >= "
                "(SELECT expected_parts FROM archive_imports a WHERE a.id=s.import_id)) ORDER BY bm25(messages_fts) LIMIT ?",
                (segmented, exclude_session_id, limit),
            ).fetchall()] if segmented else []
        except sqlite3.OperationalError:
            hits = []
        if not hits:
            fallback_terms = [item for item in re.findall(r"[\w\u3400-\u9fff]+", query) if len(item) > 1][:5]
            if fallback_terms:
                where = " OR ".join("m.content LIKE ?" for _ in fallback_terms)
                hits = [dict(row) for row in db.execute(
                    f"SELECT m.id,m.session_id,m.content,m.created_at,m.speaker,m.speaker_label,m.sequence,m.source_timestamp,s.title,s.source_platform,s.origin FROM messages m JOIN sessions s ON s.id=m.session_id WHERE s.id<>? AND (s.origin='native' OR s.import_id IS NULL OR (SELECT COUNT(*) FROM archive_import_parts p WHERE p.archive_id=s.import_id) >= (SELECT expected_parts FROM archive_imports a WHERE a.id=s.import_id)) AND ({where}) ORDER BY m.created_at DESC LIMIT ?",
                    (exclude_session_id, *[f"%{term}%" for term in fallback_terms], limit),
                ).fetchall()]
    if not hits:
        return []
    hit_ids = {item["id"] for item in hits}
    selected: dict[tuple[str, int], dict] = {}
    with connect() as db:
        for hit in hits:
            rows = db.execute(
                "SELECT m.id,m.session_id,m.role,m.content,m.created_at,m.speaker,m.speaker_label,m.sequence,m.source_timestamp,s.title,s.source_platform,s.origin "
                "FROM messages m JOIN sessions s ON s.id=m.session_id WHERE m.session_id=? AND m.sequence BETWEEN ? AND ? ORDER BY m.sequence",
                (hit["session_id"], max(1, hit["sequence"] - 1), hit["sequence"] + 1),
            ).fetchall()
            for row in rows:
                item = dict(row)
                selected[(item["session_id"], item["sequence"])] = item
    result = sorted(selected.values(), key=lambda item: (item["session_id"], item["sequence"]))
    for item in result:
        item["matched"] = item["id"] in hit_ids
    return result[:15]


def search_session_context(query: str, session_id: str, through_sequence: int, limit: int = 5) -> list[dict]:
    if through_sequence <= 0 or limit <= 0:
        return []
    terms = [token.strip() for token in jieba.cut(query) if re.fullmatch(r"[\w\u3400-\u9fff]+", token.strip())]
    segmented = " OR ".join(f'"{term}"' for term in terms[:12])
    with connect() as db:
        try:
            rows = db.execute(
                "SELECT m.id,m.session_id,m.content,m.created_at,m.speaker,m.speaker_label,m.sequence,m.source_timestamp,"
                "s.title,s.source_platform,s.origin FROM messages_fts f JOIN messages m ON m.id=f.message_id "
                "JOIN sessions s ON s.id=m.session_id WHERE messages_fts MATCH ? AND m.session_id=? AND m.sequence<=? "
                "ORDER BY bm25(messages_fts) LIMIT ?",
                (segmented, session_id, through_sequence, limit),
            ).fetchall() if segmented else []
        except sqlite3.OperationalError:
            rows = []
        if not rows:
            fallback_terms = [term for term in re.findall(r"[\w\u3400-\u9fff]+", query) if len(term) > 1][:5]
            if fallback_terms:
                where = " OR ".join("m.content LIKE ?" for _ in fallback_terms)
                rows = db.execute(
                    "SELECT m.id,m.session_id,m.content,m.created_at,m.speaker,m.speaker_label,m.sequence,m.source_timestamp,"
                    "s.title,s.source_platform,s.origin FROM messages m JOIN sessions s ON s.id=m.session_id "
                    f"WHERE m.session_id=? AND m.sequence<=? AND ({where}) ORDER BY m.sequence DESC LIMIT ?",
                    (session_id, through_sequence, *[f"%{term}%" for term in fallback_terms], limit),
                ).fetchall()
    return [{**dict(row), "matched": True} for row in rows]


@app.get("/api/archives/search")
def search_archives(q: str, limit: int = 20) -> list[dict]:
    return search_archive_messages(q, max(1, min(limit, 50)))


@app.get("/api/archives/format")
def download_archive_format() -> FileResponse:
    return FileResponse(ROOT / "docs" / "通用会话档案格式.md", filename="通用会话档案格式.md", media_type="text/markdown")


@app.get("/api/archives/conversion-prompt")
def download_archive_prompt() -> FileResponse:
    return FileResponse(ROOT / "docs" / "会话转换提示词.md", filename="会话转换提示词.md", media_type="text/markdown")


@app.get("/api/archives/example")
def download_archive_example() -> FileResponse:
    return FileResponse(ROOT / "docs" / "示例会话档案.jsonl", filename="示例会话档案.jsonl", media_type="application/x-ndjson")
