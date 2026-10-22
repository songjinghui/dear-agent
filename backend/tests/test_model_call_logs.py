import asyncio
from unittest.mock import patch

from companion import chat_routes, common
from companion.model_call_logs import delete_model_call_log, get_model_call_log, list_model_call_logs, record_model_call, finish_model_call
from tests.support import ApiFeatureTestCase


class ModelCallLogFeatureTests(ApiFeatureTestCase):
    def test_direct_api_call_is_recorded_with_the_exact_messages(self) -> None:
        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"choices": [{"message": {"content": "收到"}}]}

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, *_args, **_kwargs):
                return Response()

        messages = [{"role": "user", "content": "只在本地日志里核对"}]
        with patch.object(chat_routes.httpx, "AsyncClient", return_value=Client()):
            coroutine = chat_routes.call_model({
                "base_url": "http://localhost:1234/v1", "model": "test-model",
                "temperature": 0.2, "max_tokens": 80,
            }, "secret-key", messages)
            try:
                coroutine.send(None)
            except StopIteration as result:
                answer = result.value

        self.assertEqual(answer, "收到")
        log = list_model_call_logs()["items"][0]
        detail = get_model_call_log(log["id"])
        self.assertEqual(detail["request"]["messages"], messages)
        self.assertEqual(detail["request"]["model"], "test-model")
        self.assertNotIn("secret-key", str(detail))

    def test_request_is_saved_locally_and_can_be_inspected_and_deleted(self) -> None:
        log_id = record_model_call(
            provider="openai-compatible",
            model="local-test-model",
            purpose="chat",
            request={"messages": [{"role": "user", "content": "仅留在本机的内容"}]},
            session_id="session-1",
        )
        finish_model_call(log_id, status="complete", response_preview="测试回复")

        listed = list_model_call_logs()
        self.assertEqual(len(listed["items"]), 1)
        self.assertEqual(listed["items"][0]["model"], "local-test-model")
        detail = get_model_call_log(log_id)
        self.assertEqual(detail["request"]["messages"][0]["content"], "仅留在本机的内容")
        self.assertEqual(detail["response_preview"], "测试回复")

        delete_model_call_log(log_id)
        self.assertEqual(list_model_call_logs()["total"], 0)

    def test_failed_request_is_logged_without_credentials(self) -> None:
        log_id = record_model_call(
            provider="openai-compatible",
            model="local-test-model",
            purpose="chat",
            request={"messages": [{"role": "user", "content": "hello"}]},
        )
        finish_model_call(log_id, status="failed", error="连接失败")

        detail = get_model_call_log(log_id)
        self.assertEqual(detail["status"], "failed")
        self.assertEqual(detail["error"], "连接失败")
        self.assertNotIn("api_key", detail["request"])

    def test_cli_chat_route_updates_exact_request_and_saves_reply(self) -> None:
        session_id = "cli-route-test-session"
        timestamp = common.now()
        with common.connect() as db:
            db.execute(
                "INSERT INTO sessions(id,title,created_at,updated_at,mode,origin) VALUES (?,?,?,?,?,?)",
                (session_id, "CLI 测试", timestamp, timestamp, "chat", "native"),
            )

        class FakeProvider:
            display_name = "Fake CLI"

            async def run_turn(self, context, on_delta):
                context.record_request({
                    "method": "turn/start",
                    "app_server_transport": {"line_count": 3, "max_line_bytes": 1234},
                }, "fake-model")
                await on_delta("测试回复")
                return "测试回复"

        async def collect_events() -> list[str]:
            return [chunk async for chunk in chat_routes.stream_cli_chat(
                "fake-cli", session_id, "native",
                {"id": "user-1", "session_id": session_id, "content": "测试"},
                "测试", "系统提示", [],
            )]

        with patch.object(chat_routes.cli_providers, "get", return_value=FakeProvider()):
            chunks = asyncio.run(collect_events())

        self.assertTrue(any('"type": "done"' in chunk for chunk in chunks))
        log = list_model_call_logs()["items"][0]
        request = get_model_call_log(log["id"])["request"]
        self.assertEqual(request["method"], "turn/start")
        self.assertEqual(request["app_server_transport"]["max_line_bytes"], 1234)
        self.assertEqual(log["model"], "fake-model")
        with common.connect() as db:
            reply = db.execute(
                "SELECT content FROM messages WHERE session_id=? AND role='assistant'", (session_id,),
            ).fetchone()
        self.assertEqual(reply["content"], "测试回复")
