from unittest.mock import patch

from companion import chat_routes
from companion.session_routes import SessionInput, create_session, list_messages
from tests.support import ApiFeatureTestCase


class ChatMemoryFeatureTests(ApiFeatureTestCase):
    def test_explicit_memory_instruction_saves_and_confirms(self) -> None:
        session = create_session(SessionInput(title="记忆指令检查"))
        with patch.object(chat_routes, "create_memory") as save_memory, patch.object(chat_routes, "index_message"):
            pending = chat_routes.chat(chat_routes.ChatInput(
                session_id=session["id"], content="记住这个：我喜欢先听完再讨论建议",
            ))
            with self.assertRaises(StopIteration) as completed:
                pending.send(None)

        result = completed.exception.value
        self.assertEqual(result["assistant_message"]["role"], "assistant")
        self.assertEqual(save_memory.call_args.args[0].content, "我喜欢先听完再讨论建议")
        messages = list_messages(session["id"])
        self.assertEqual([item["role"] for item in messages], ["user", "assistant"])
