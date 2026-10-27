import asyncio
from unittest.mock import AsyncMock, patch

from companion import chat_routes
from companion.context_builder import ComposedContext
from companion.session_routes import SessionInput, create_session
from tests.support import ApiFeatureTestCase


class ChatTimeContextTests(ApiFeatureTestCase):
    def test_direct_api_context_receives_persisted_message_times(self) -> None:
        session = create_session(SessionInput(title="时间上下文"))
        with chat_routes.connect() as db:
            db.execute(
                "INSERT INTO messages(id,session_id,role,content,created_at,sequence) VALUES (?,?,?,?,?,?)",
                ("old-message", session["id"], "assistant", "上次回复", "2026-10-18T01:00:00+00:00", 1),
            )

        captured = {}

        async def prepare(**kwargs):
            captured["messages"] = kwargs["all_messages"]
            return ComposedContext("系统提示", [], kwargs["all_messages"][-1])

        with (
            patch.object(chat_routes.keyring, "get_password", return_value="test-key"),
            patch.object(chat_routes, "prepare_direct_api_context", side_effect=prepare),
            patch.object(chat_routes, "call_with_context_retry", new=AsyncMock(return_value="模型回复")),
            patch.object(chat_routes, "process_agent_response", return_value={"reply": "模型回复", "memory_suggestions": []}),
        ):
            asyncio.run(chat_routes.chat(chat_routes.ChatInput(session_id=session["id"], content="继续聊")))

        self.assertEqual(captured["messages"][0]["created_at"], "2026-10-18T01:00:00+00:00")
        self.assertTrue(captured["messages"][-1]["created_at"])
