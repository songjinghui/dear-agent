"""Persistence helpers for automatically generated per-session summaries."""

from .common import connect, now


def get_session_summary(session_id: str) -> dict:
    with connect() as db:
        row = db.execute(
            "SELECT summary,through_sequence,updated_at FROM session_context_summaries WHERE session_id=?",
            (session_id,),
        ).fetchone()
    return dict(row) if row else {"summary": "", "through_sequence": 0, "updated_at": None}


def save_session_summary(session_id: str, summary: str, through_sequence: int) -> None:
    if not summary.strip() or through_sequence < 0:
        raise ValueError("会话摘要或覆盖序号无效。")
    with connect() as db:
        db.execute(
            "INSERT INTO session_context_summaries(session_id,summary,through_sequence,updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
            "summary=excluded.summary,through_sequence=excluded.through_sequence,updated_at=excluded.updated_at",
            (session_id, summary.strip(), through_sequence, now()),
        )
