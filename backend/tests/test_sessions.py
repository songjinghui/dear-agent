from companion.session_routes import SessionInput, SessionModeInput, create_session, get_session_chat_status, list_messages, list_sessions, set_session_mode
from companion.common import connect, now
from companion.model_call_logs import finish_model_call, record_model_call
from tests.support import ApiFeatureTestCase


class SessionFeatureTests(ApiFeatureTestCase):
    def test_chat_status_tracks_a_pending_request_and_clears_after_reply_is_saved(self) -> None:
        session = create_session(SessionInput(title="回复恢复"))
        with connect() as db:
            db.execute(
                "INSERT INTO messages(id,session_id,role,content,created_at,sequence) VALUES (?,?,?,?,?,?)",
                ("pending-user", session["id"], "user", "正在等待回复", now(), 1),
            )
        log_id = record_model_call(
            provider="openai-compatible", model="test-model", purpose="对话",
            request={"messages": [{"role": "user", "content": "正在等待回复"}]}, session_id=session["id"],
        )

        self.assertEqual(get_session_chat_status(session["id"])["status"], "processing")

        finish_model_call(log_id, status="complete", response_preview="已经回复")
        with connect() as db:
            db.execute(
                "INSERT INTO messages(id,session_id,role,content,created_at,sequence) VALUES (?,?,?,?,?,?)",
                ("pending-assistant", session["id"], "assistant", "已经回复", now(), 2),
            )
        self.assertEqual(get_session_chat_status(session["id"])["status"], "idle")

    def test_unanswered_user_message_is_reported_as_interrupted_after_failed_call(self) -> None:
        session = create_session(SessionInput(title="请求失败"))
        with connect() as db:
            db.execute(
                "INSERT INTO messages(id,session_id,role,content,created_at,sequence) VALUES (?,?,?,?,?,?)",
                ("failed-user", session["id"], "user", "没有收到回复", now(), 1),
            )
        log_id = record_model_call(
            provider="openai-compatible", model="test-model", purpose="对话",
            request={"messages": []}, session_id=session["id"],
        )
        finish_model_call(log_id, status="failed", error="模拟失败")

        status = get_session_chat_status(session["id"])
        self.assertEqual(status["status"], "interrupted")
        self.assertEqual(status["error"], "模拟失败")

    def test_create_list_and_open_session(self) -> None:
        session = create_session(SessionInput(title="旅行计划"))
        sessions = list_sessions()
        messages = list_messages(session["id"])

        self.assertEqual(sessions[0]["id"], session["id"])
        self.assertEqual(messages, [])

    def test_new_session_uses_configured_default_mode(self) -> None:
        with connect() as db:
            db.execute("UPDATE settings SET value='manager' WHERE key='default_mode'")

        session = create_session(SessionInput(title="训练安排"))

        self.assertEqual(session["mode"], "manager")

    def test_session_mode_can_be_changed_and_is_returned_in_session_lists(self) -> None:
        session = create_session()

        changed = set_session_mode(session["id"], SessionModeInput(mode="manager"))

        self.assertEqual(changed["mode"], "manager")
        self.assertEqual(list_sessions()[0]["mode"], "manager")

    def test_repeated_new_session_reuses_the_empty_conversation(self) -> None:
        first = create_session()
        second = create_session()

        self.assertEqual(second["id"], first["id"])
        self.assertEqual(len(list_sessions()), 1)

    def test_new_session_after_a_message_creates_a_fresh_conversation(self) -> None:
        first = create_session()
        with connect() as db:
            db.execute(
                "INSERT INTO messages(id,session_id,role,content,created_at) VALUES (?,?,?,?,?)",
                ("message-1", first["id"], "user", "你好", now()),
            )

        second = create_session()

        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(len(list_sessions()), 2)
