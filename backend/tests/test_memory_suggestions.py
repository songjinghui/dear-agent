from companion.memory_suggestions import (
    approve_memory_suggestion,
    dismiss_memory_suggestion,
    list_memory_suggestions,
    process_agent_response,
    save_memory_suggestions,
)
from companion import common
from tests.support import ApiFeatureTestCase


class MemorySuggestionTests(ApiFeatureTestCase):
    def test_model_response_is_split_into_user_reply_and_persisted_suggestions(self) -> None:
        import json

        with common.connect() as db:
            db.execute(
                "INSERT INTO sessions(id,title,created_at,updated_at) VALUES (?,?,?,?)",
                ("session-0", "test", "2026-10-23", "2026-10-23"),
            )
            db.execute(
                "INSERT INTO messages(id,session_id,role,content,created_at,sequence) VALUES (?,?,?,?,?,?)",
                ("message-0", "session-0", "user", "我周五前要交项目方案", "2026-10-23", 1),
            )
        raw = json.dumps({
            "reply": "我可以帮你拆分准备步骤。",
            "memory_candidates": [{
                "content": "周五前要交项目方案", "memory_type": "working", "importance": 4,
                "sensitivity": "normal", "evidence": "我周五前要交项目方案", "rationale": "近期任务",
            }],
        }, ensure_ascii=False)

        result = process_agent_response(raw, "我周五前要交项目方案", "session-0", "message-0")

        self.assertEqual(result["reply"], "我可以帮你拆分准备步骤。")
        self.assertEqual(len(result["memory_suggestions"]), 1)
        self.assertEqual(list_memory_suggestions("session-0")[0]["status"], "pending")

    def test_candidates_are_pending_until_user_approves_them(self) -> None:
        with common.connect() as db:
            db.execute(
                "INSERT INTO sessions(id,title,created_at,updated_at) VALUES (?,?,?,?)",
                ("session-1", "test", "2026-10-23", "2026-10-23"),
            )
            db.execute(
                "INSERT INTO messages(id,session_id,role,content,created_at,sequence) VALUES (?,?,?,?,?,?)",
                ("message-1", "session-1", "user", "周五前要交项目方案", "2026-10-23", 1),
            )

        rows = save_memory_suggestions("session-1", "message-1", [{
            "content": "周五前要交项目方案", "memory_type": "working", "importance": 4,
            "sensitivity": "normal", "evidence": "周五前要交项目方案", "rationale": "明确的近期任务",
            "expires_at": None,
        }])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "pending")
        self.assertEqual(list_memory_suggestions("session-1")[0]["content"], "周五前要交项目方案")
        approved = approve_memory_suggestion(rows[0]["id"])
        self.assertEqual(approved["content"], "周五前要交项目方案")
        self.assertEqual(approved["memory_type"], "working")
        self.assertEqual(approved["source_refs"][0]["message_id"], "message-1")
        self.assertEqual(list_memory_suggestions("session-1"), [])

    def test_dismissed_candidate_is_not_saved_as_a_memory(self) -> None:
        with common.connect() as db:
            db.execute(
                "INSERT INTO sessions(id,title,created_at,updated_at) VALUES (?,?,?,?)",
                ("session-2", "test", "2026-10-23", "2026-10-23"),
            )
            db.execute(
                "INSERT INTO messages(id,session_id,role,content,created_at,sequence) VALUES (?,?,?,?,?,?)",
                ("message-2", "session-2", "user", "我喜欢简单的建议", "2026-10-23", 1),
            )
        row = save_memory_suggestions("session-2", "message-2", [{
            "content": "用户喜欢简单的建议", "memory_type": "semantic", "importance": 3,
            "sensitivity": "normal", "evidence": "我喜欢简单的建议", "rationale": "用户偏好",
            "expires_at": None,
        }])[0]

        dismiss_memory_suggestion(row["id"])

        self.assertEqual(list_memory_suggestions("session-2"), [])
        with common.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 0)

