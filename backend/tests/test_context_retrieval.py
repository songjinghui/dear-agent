from companion.archive_import_routes import index_message
from companion.archive_read_routes import search_session_context
from companion.common import connect
from companion.session_routes import SessionInput, create_session
from tests.support import ApiFeatureTestCase


class ContextRetrievalTests(ApiFeatureTestCase):
    def test_session_retrieval_returns_relevant_raw_message_with_source(self) -> None:
        session = create_session(SessionInput(title="预算讨论"))
        message = {
            "id": "old-budget", "session_id": session["id"], "role": "user",
            "content": "我的搬家预算是十万元。", "created_at": "2026-01-01T00:00:00+00:00",
            "speaker": "user", "sequence": 1,
        }
        with connect() as db:
            db.execute(
                "INSERT INTO messages(id,session_id,role,content,created_at,speaker,sequence) VALUES (:id,:session_id,:role,:content,:created_at,:speaker,:sequence)",
                message,
            )
        with connect() as db:
            index_message(db, message["id"], message["content"])

        result = search_session_context("搬家预算", session["id"], through_sequence=1)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "old-budget")
        self.assertEqual(result[0]["title"], "预算讨论")
        self.assertEqual(result[0]["speaker"], "user")
        self.assertEqual(result[0]["sequence"], 1)

    def test_session_retrieval_does_not_repeat_recent_uncompressed_messages(self) -> None:
        session = create_session(SessionInput(title="近期内容"))
        message = {
            "id": "recent", "session_id": session["id"], "role": "user",
            "content": "我的预算是五万元。", "created_at": "2026-01-01T00:00:00+00:00",
            "speaker": "user", "sequence": 5,
        }
        with connect() as db:
            db.execute(
                "INSERT INTO messages(id,session_id,role,content,created_at,speaker,sequence) VALUES (:id,:session_id,:role,:content,:created_at,:speaker,:sequence)",
                message,
            )
        with connect() as db:
            index_message(db, message["id"], message["content"])

        result = search_session_context("预算", session["id"], through_sequence=4)

        self.assertEqual(result, [])
