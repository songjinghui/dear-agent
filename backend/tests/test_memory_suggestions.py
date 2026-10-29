from companion.memory_suggestions import (
    approve_memory_suggestion,
    approve_memory_suggestion_group,
    dismiss_memory_suggestion_group,
    dismiss_memory_suggestion,
    list_memory_suggestions,
    process_agent_response,
    save_memory_suggestions,
)
from companion import common
from fastapi import HTTPException
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
                "topic": "项目方案",
            }],
        }, ensure_ascii=False)

        result = process_agent_response(raw, "我周五前要交项目方案", "session-0", "message-0")

        self.assertEqual(result["reply"], "我可以帮你拆分准备步骤。")
        self.assertEqual(len(result["memory_suggestions"]), 1)
        self.assertEqual(list_memory_suggestions("session-0")[0]["status"], "pending")
        self.assertEqual(list_memory_suggestions("session-0")[0]["topic"], "项目方案")

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

    def test_approving_a_topic_group_writes_one_memory_with_all_evidence(self) -> None:
        with common.connect() as db:
            db.execute(
                "INSERT INTO sessions(id,title,created_at,updated_at) VALUES (?,?,?,?)",
                ("session-topic", "北疆旅行", "2026-10-23", "2026-10-23"),
            )
            for sequence, message_id, content in [
                (1, "message-topic-1", "我预计十一月去北疆。"),
                (2, "message-topic-2", "我已经买了羽绒服。"),
            ]:
                db.execute(
                    "INSERT INTO messages(id,session_id,role,content,created_at,sequence) VALUES (?,?,?,?,?,?)",
                    (message_id, "session-topic", "user", content, "2026-10-23", sequence),
                )
        rows = [
            save_memory_suggestions("session-topic", "message-topic-1", [{
                "content": "用户预计十一月去北疆。", "memory_type": "working", "importance": 3,
                "sensitivity": "normal", "evidence": "我预计十一月去北疆。", "rationale": "旅行时间", "expires_at": None,
            }])[0],
            save_memory_suggestions("session-topic", "message-topic-2", [{
                "content": "用户已经买了羽绒服。", "memory_type": "working", "importance": 3,
                "sensitivity": "normal", "evidence": "我已经买了羽绒服。", "rationale": "装备进度", "expires_at": None,
            }])[0],
        ]

        approved = approve_memory_suggestion_group(
            [row["id"] for row in rows],
            {
                "content": "北疆旅行的期待与准备\n- 预计十一月出发\n- 已购羽绒服",
                "memory_type": "working", "importance": 4, "sensitivity": "normal", "expires_at": None,
            },
        )

        self.assertEqual(approved["content"], "北疆旅行的期待与准备\n- 预计十一月出发\n- 已购羽绒服")
        self.assertEqual(len(approved["source_refs"]), 2)
        self.assertEqual(list_memory_suggestions("session-topic"), [])
        with common.connect() as db:
            memories = db.execute("SELECT id FROM memories").fetchall()
            suggestions = db.execute(
                "SELECT status,memory_id FROM memory_suggestions WHERE session_id='session-topic' ORDER BY created_at",
            ).fetchall()
        self.assertEqual(len(memories), 1)
        self.assertEqual([(row["status"], row["memory_id"]) for row in suggestions], [("approved", memories[0]["id"])] * 2)

    def test_dismissing_a_topic_group_hides_all_of_its_pending_details(self) -> None:
        with common.connect() as db:
            db.execute(
                "INSERT INTO sessions(id,title,created_at,updated_at) VALUES (?,?,?,?)",
                ("session-dismiss-topic", "旅行", "2026-10-23", "2026-10-23"),
            )
            for sequence, message_id, content in [
                (1, "message-dismiss-1", "我要去旅行。"),
                (2, "message-dismiss-2", "我在准备装备。"),
            ]:
                db.execute(
                    "INSERT INTO messages(id,session_id,role,content,created_at,sequence) VALUES (?,?,?,?,?,?)",
                    (message_id, "session-dismiss-topic", "user", content, "2026-10-23", sequence),
                )
        rows = [
            save_memory_suggestions("session-dismiss-topic", "message-dismiss-1", [{
                "content": "用户要去旅行。", "memory_type": "working", "importance": 3,
                "sensitivity": "normal", "evidence": "我要去旅行。", "rationale": "行程", "expires_at": None,
            }])[0],
            save_memory_suggestions("session-dismiss-topic", "message-dismiss-2", [{
                "content": "用户在准备装备。", "memory_type": "working", "importance": 3,
                "sensitivity": "normal", "evidence": "我在准备装备。", "rationale": "装备", "expires_at": None,
            }])[0],
        ]

        dismiss_memory_suggestion_group([row["id"] for row in rows])

        self.assertEqual(list_memory_suggestions("session-dismiss-topic"), [])
        with common.connect() as db:
            statuses = db.execute(
                "SELECT status FROM memory_suggestions WHERE session_id='session-dismiss-topic'",
            ).fetchall()
        self.assertEqual([row["status"] for row in statuses], ["dismissed", "dismissed"])

    def test_topic_group_rejects_different_subjects_from_the_same_session(self) -> None:
        with common.connect() as db:
            db.execute(
                "INSERT INTO sessions(id,title,created_at,updated_at) VALUES (?,?,?,?)",
                ("session-separate-topics", "日常记录", "2026-10-23", "2026-10-23"),
            )
            for sequence, message_id, content in [
                (1, "message-trip", "我在准备北疆旅行。"),
                (2, "message-sleep", "我本周想早睡。"),
            ]:
                db.execute(
                    "INSERT INTO messages(id,session_id,role,content,created_at,sequence) VALUES (?,?,?,?,?,?)",
                    (message_id, "session-separate-topics", "user", content, "2026-10-23", sequence),
                )
        trip = save_memory_suggestions("session-separate-topics", "message-trip", [{
            "topic": "北疆旅行", "content": "用户在准备北疆旅行。", "memory_type": "working", "importance": 3,
            "sensitivity": "normal", "evidence": "我在准备北疆旅行。", "rationale": "旅行准备", "expires_at": None,
        }])[0]
        sleep = save_memory_suggestions("session-separate-topics", "message-sleep", [{
            "topic": "睡眠调整", "content": "用户本周想早睡。", "memory_type": "working", "importance": 3,
            "sensitivity": "normal", "evidence": "我本周想早睡。", "rationale": "作息计划", "expires_at": None,
        }])[0]

        with self.assertRaises(HTTPException) as raised:
            approve_memory_suggestion_group(
                [trip["id"], sleep["id"]],
                {"content": "不应合并", "memory_type": "working", "importance": 3, "sensitivity": "normal"},
            )

        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(len(list_memory_suggestions("session-separate-topics")), 2)

    def test_supplementary_candidate_updates_its_pending_topic(self) -> None:
        import json

        with common.connect() as db:
            db.execute(
                "INSERT INTO sessions(id,title,created_at,updated_at) VALUES (?,?,?,?)",
                ("session-merge", "test", "2026-10-23", "2026-10-23"),
            )
            for sequence, message_id, content in [
                (1, "message-merge-1", "I will visit northern Xinjiang in November."),
                (2, "message-merge-2", "The northern Xinjiang trip will be in December instead."),
            ]:
                db.execute(
                    "INSERT INTO messages(id,session_id,role,content,created_at,sequence) VALUES (?,?,?,?,?,?)",
                    (message_id, "session-merge", "user", content, "2026-10-23", sequence),
                )

        original = save_memory_suggestions("session-merge", "message-merge-1", [{
            "content": "User plans a November trip to northern Xinjiang.",
            "memory_type": "working", "importance": 3, "sensitivity": "normal",
            "evidence": "I will visit northern Xinjiang in November.",
            "rationale": "Travel plan", "expires_at": None,
        }])[0]
        raw = json.dumps({
            "reply": "That sounds exciting.",
            "memory_candidates": [{
                "content": "User plans a December trip to northern Xinjiang.",
                "memory_type": "working", "importance": 4, "sensitivity": "normal",
                "evidence": "The northern Xinjiang trip will be in December instead.",
                "rationale": "Corrected travel timing", "merge_with": original["id"],
            }],
        })

        result = process_agent_response(
            raw,
            "The northern Xinjiang trip will be in December instead.",
            "session-merge",
            "message-merge-2",
        )

        self.assertEqual([item["id"] for item in result["memory_suggestions"]], [original["id"]])
        pending = list_memory_suggestions("session-merge")
        self.assertEqual([item["id"] for item in pending], [original["id"]])
        self.assertIn("December", pending[0]["content"])
        self.assertIn("November", pending[0]["evidence"])
        self.assertIn("December instead", pending[0]["evidence"])
