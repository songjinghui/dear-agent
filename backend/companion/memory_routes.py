"""HTTP endpoints and handlers for the memory domain."""

from .common import *  # noqa: F403
from .memory_embeddings import search_vector_memories

def search_relevant_memories(query: str, limit: int = 4, *, vector_search: bool = False) -> list[dict]:
    # Index segmented Chinese words so FTS5 can perform useful lexical matching.
    terms = [token.strip() for token in jieba.cut(query) if re.fullmatch(r"[\w\u3400-\u9fff]+", token.strip())]
    segmented = " OR ".join(f'"{term}"' for term in terms[:12])
    with connect() as db:
        try:
            rows = db.execute(
                "SELECT m.* FROM memories_fts f JOIN memories m ON m.id=f.memory_id "
                "WHERE memories_fts MATCH ? ORDER BY bm25(memories_fts) LIMIT ?",
                (segmented, limit),
            ).fetchall() if segmented else []
        except sqlite3.OperationalError:
            rows = []
        if not rows:
            terms = [t for t in re.findall(r"[\w\u3400-\u9fff]+", query) if len(t) > 1][:5]
            if terms:
                where = " OR ".join("content LIKE ?" for _ in terms)
                rows = db.execute(
                    f"SELECT * FROM memories WHERE {where} ORDER BY importance DESC, updated_at DESC LIMIT ?",
                    (*[f"%{term}%" for term in terms], limit),
                ).fetchall()
    lexical = [dict(row) for row in rows]
    if not vector_search:
        return lexical
    try:
        vector = search_vector_memories(query, limit)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        vector = []
    merged = {item["id"]: item for item in lexical}
    merged.update({item["id"]: item for item in vector})
    return list(merged.values())


class MemoryInput(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    memory_type: Literal["episodic", "semantic", "working"] = "semantic"
    importance: int = Field(default=3, ge=1, le=5)
    sensitivity: Literal["normal", "sensitive", "private"] = "normal"
    expires_at: str | None = None


def upsert_memory(
    memory_id: str,
    payload: MemoryInput,
    created_at: str | None = None,
    source: str = "用户明确要求记录",
    source_refs: list[dict] | None = None,
) -> dict:
    timestamp = now()
    item = {
        "id": memory_id,
        "content": payload.content.strip(),
        "memory_type": payload.memory_type,
        "importance": payload.importance,
        "source": source,
        "confirmation": "confirmed",
        "sensitivity": payload.sensitivity,
        "created_at": created_at or timestamp,
        "updated_at": timestamp,
        "expires_at": payload.expires_at,
        "source_refs": json.dumps(source_refs or [], ensure_ascii=False),
    }
    with connect() as db:
        db.execute(
            "INSERT INTO memories(id,content,memory_type,importance,source,confirmation,sensitivity,created_at,updated_at,expires_at,source_refs) "
            "VALUES (:id,:content,:memory_type,:importance,:source,:confirmation,:sensitivity,:created_at,:updated_at,:expires_at,:source_refs) "
            "ON CONFLICT(id) DO UPDATE SET content=excluded.content,memory_type=excluded.memory_type,importance=excluded.importance,sensitivity=excluded.sensitivity,updated_at=excluded.updated_at,expires_at=excluded.expires_at",
            item,
        )
        db.execute("DELETE FROM memory_embeddings WHERE memory_id=?", (memory_id,))
        try:
            db.execute("DELETE FROM memories_fts WHERE memory_id=?", (memory_id,))
            db.execute("INSERT INTO memories_fts(memory_id, content) VALUES (?, ?)", (memory_id, " ".join(jieba.cut(item["content"]))))
        except sqlite3.OperationalError:
            pass
    return item


@app.get("/api/memories")
def list_memories(q: str = "") -> list[dict]:
    if q.strip():
        return search_relevant_memories(q, limit=100)
    with connect() as db:
        rows = db.execute("SELECT * FROM memories ORDER BY updated_at DESC").fetchall()
    return [dict(row) for row in rows]


@app.post("/api/memories")
def create_memory(payload: MemoryInput) -> dict:
    return upsert_memory(str(uuid.uuid4()), payload)


@app.put("/api/memories/{memory_id}")
def edit_memory(memory_id: str, payload: MemoryInput) -> dict:
    with connect() as db:
        row = db.execute("SELECT created_at FROM memories WHERE id=?", (memory_id,)).fetchone()
    if not row:
        raise HTTPException(404, "记忆不存在")
    return upsert_memory(memory_id, payload, row["created_at"])


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: str) -> dict:
    with connect() as db:
        db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        try:
            db.execute("DELETE FROM memories_fts WHERE memory_id=?", (memory_id,))
        except sqlite3.OperationalError:
            pass
    return {"deleted": True}
