import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from companion import chat_routes, common
from companion.context_builder import ComposedContext
from companion.model_call_logs import (
    delete_model_call_log, finish_model_call, get_model_call_log,
    list_model_call_logs, prune_expired_model_call_logs, record_model_call,
)
from companion.session_routes import SessionInput, create_session
from tests.support import ApiFeatureTestCase


class ModelCallLogFeatureTests(ApiFeatureTestCase):
    def test_pending_memory_candidates_are_only_injected_when_memory_context_is_enabled(self) -> None:
        captured_prompts = []
        settings = {
            "provider": "openai-compatible", "base_url": "https://api.example.com/v1",
            "model": "test-model", "temperature": 0.2, "max_tokens": 80,
            "use_memories_in_cloud": False, "use_vector_memory_search": False,
            "use_archive_history": False, "agent_definition": "", "chat_mode_prompt": "",
        }

        async def prepare_context(**kwargs):
            captured_prompts.append(kwargs["base_prompt"])
            return ComposedContext(
                system_prompt="fixed", prior_history=[],
                current_message={"id": "current", "role": "user", "content": "test"},
            )

        async def reply(*_args):
            return "normal reply"

        with (
            patch.object(chat_routes, "read_settings", side_effect=lambda: dict(settings)),
            patch.object(chat_routes.keyring, "get_password", return_value="secret-key"),
            patch.object(chat_routes, "pending_normal_memory_suggestions", return_value=[{
                "id": "candidate-1", "topic": "ongoing topic",
                "content": "pending candidate", "memory_type": "working",
            }]),
            patch.object(chat_routes, "prepare_direct_api_context", side_effect=prepare_context),
            patch.object(chat_routes, "call_with_context_retry", side_effect=reply),
        ):
            disabled_session = create_session(SessionInput(title="memory context disabled"))
            asyncio.run(chat_routes.chat(chat_routes.ChatInput(
                session_id=disabled_session["id"], content="first message",
            )))
            settings["use_memories_in_cloud"] = True
            enabled_session = create_session(SessionInput(title="memory context enabled"))
            asyncio.run(chat_routes.chat(chat_routes.ChatInput(
                session_id=enabled_session["id"], content="second message",
            )))

        self.assertNotIn("待整理候选", captured_prompts[0])
        self.assertIn("待整理候选", captured_prompts[1])

    def test_logs_expire_after_thirty_days_without_deleting_newer_records(self) -> None:
        reference_time = datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)
        expired_id = record_model_call(
            provider="openai-compatible", model="old-model", purpose="chat", request={},
        )
        retained_id = record_model_call(
            provider="openai-compatible", model="new-model", purpose="chat", request={},
        )
        with common.connect() as db:
            db.execute(
                "UPDATE model_call_logs SET created_at=? WHERE id=?",
                ((reference_time - timedelta(days=30, microseconds=1)).isoformat(timespec="microseconds"), expired_id),
            )
            db.execute(
                "UPDATE model_call_logs SET created_at=? WHERE id=?",
                ((reference_time - timedelta(days=30)).isoformat(timespec="microseconds"), retained_id),
            )

        self.assertEqual(prune_expired_model_call_logs(reference_time), 1)
        self.assertEqual(list_model_call_logs()["total"], 1)
        self.assertEqual(list_model_call_logs()["items"][0]["id"], retained_id)

    def test_responses_api_conversation_is_saved_and_reused_for_the_same_local_session(self) -> None:
        session_id = create_session(SessionInput(title="Responses 会话续接测试"))["id"]
        responses = [
            {"id": "resp_1", "conversation": {"id": "conv_1"}, "output": [{"type": "message", "content": [{"type": "output_text", "text": "第一条回复"}]}]},
            {"id": "resp_2", "conversation": {"id": "conv_1"}, "output": [{"type": "message", "content": [{"type": "output_text", "text": "第二条回复"}]}]},
        ]
        requests = []

        class Response:
            def __init__(self, data):
                self.data = data

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return self.data

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, _url, json, **_kwargs):
                requests.append(json)
                return Response(responses.pop(0))

        settings = {
            "base_url": "https://api.example.com/v1", "model": "test-model",
            "temperature": 0.2, "max_tokens": 80, "api_protocol": "responses",
        }
        with patch.object(chat_routes.httpx, "AsyncClient", return_value=Client()):
            for messages in (
                [{"role": "system", "content": "系统提示"}, {"role": "user", "content": "第一问"}],
                [{"role": "system", "content": "更新后的提示"}, {"role": "user", "content": "第二问"}],
            ):
                with chat_routes.model_call_context("对话", session_id):
                    coroutine = chat_routes.call_model(settings, "secret-key", messages)
                    with self.assertRaises(StopIteration) as completed:
                        coroutine.send(None)
                    self.assertIn("回复", completed.exception.value)

        self.assertNotIn("conversation", requests[0])
        self.assertEqual(requests[1]["conversation"], "conv_1")
        self.assertEqual(requests[1]["input"], [{"role": "user", "content": "第二问"}])
        self.assertEqual(requests[1]["instructions"], "更新后的提示")
        with chat_routes.connect() as db:
            saved = db.execute(
                "SELECT state_value FROM provider_session_state WHERE session_id=? AND provider_id=? AND state_key=?",
                (session_id, "openai-compatible", "responses_continuation_id"),
            ).fetchone()
        self.assertEqual(saved["state_value"], "conv_1")

    def test_responses_api_reuses_response_id_with_previous_response_id(self) -> None:
        session_id = create_session(SessionInput(title="Responses response ID 测试"))["id"]
        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"id": "resp_only", "output": [{"type": "message", "content": [{"type": "output_text", "text": "完成"}]}]}

        requests = []

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, _url, json, **_kwargs):
                requests.append(json)
                return Response()

        settings = {
            "base_url": "https://api.example.com/v1", "model": "test-model",
            "temperature": 0.2, "max_tokens": 80, "api_protocol": "responses",
        }
        with patch.object(chat_routes.httpx, "AsyncClient", return_value=Client()):
            for content in ("第一问", "第二问"):
                with chat_routes.model_call_context("对话", session_id):
                    coroutine = chat_routes.call_model(
                        settings, "secret-key", [{"role": "user", "content": content}],
                    )
                    with self.assertRaises(StopIteration):
                        coroutine.send(None)

        self.assertNotIn("conversation", requests[0])
        self.assertNotIn("previous_response_id", requests[0])
        self.assertEqual(requests[1]["previous_response_id"], "resp_only")
        self.assertEqual(requests[1]["input"], [{"role": "user", "content": "第二问"}])
        with chat_routes.connect() as db:
            saved = db.execute(
                "SELECT state_value FROM provider_session_state WHERE session_id=? AND provider_id=? AND state_key=?",
                (session_id, "openai-compatible", "responses_continuation_id"),
            ).fetchone()
        self.assertEqual(saved["state_value"], "resp_only")

    def test_chat_completion_id_is_not_used_as_a_resumable_api_conversation(self) -> None:
        session_id = create_session(SessionInput(title="Chat Completions ID 测试"))["id"]
        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"id": "chatcmpl_one_turn", "choices": [{"message": {"content": "完成"}}]}

        requests = []

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, _url, json, **_kwargs):
                requests.append(json)
                return Response()

        settings = {
            "base_url": "https://api.example.com/v1", "model": "test-model",
            "temperature": 0.2, "max_tokens": 80,
        }
        with patch.object(chat_routes.httpx, "AsyncClient", return_value=Client()):
            for content in ("第一问", "第二问"):
                with chat_routes.model_call_context("对话", session_id):
                    coroutine = chat_routes.call_model(
                        settings, "secret-key", [{"role": "user", "content": content}],
                    )
                    with self.assertRaises(StopIteration):
                        coroutine.send(None)

        self.assertNotIn("conversation", requests[1])
        self.assertNotIn("previous_response_id", requests[1])

    def test_chat_completions_turn_clears_stale_responses_continuation(self) -> None:
        session_id = create_session(SessionInput(title="协议切换清理测试"))["id"]
        with common.connect() as db:
            db.executemany(
                "INSERT INTO provider_session_state(session_id,provider_id,state_key,state_value) VALUES (?,?,?,?)",
                [
                    (session_id, "openai-compatible", "responses_continuation_id", "old-response"),
                    (session_id, "openai-compatible", "responses_continuation_kind", "previous_response_id"),
                    (session_id, "openai-compatible", "responses_fingerprint", "https://api.example.com/v1|test-model"),
                ],
            )

        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"id": "chatcmpl_new", "choices": [{"message": {"content": "完成"}}]}

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, *_args, **_kwargs):
                return Response()

        with patch.object(chat_routes.httpx, "AsyncClient", return_value=Client()):
            with chat_routes.model_call_context("对话", session_id):
                coroutine = chat_routes.call_model({
                    "base_url": "https://api.example.com/v1", "model": "test-model",
                    "temperature": 0.2, "max_tokens": 80,
                }, "secret-key", [{"role": "user", "content": "切回 Chat Completions"}])
                with self.assertRaises(StopIteration):
                    coroutine.send(None)

        with common.connect() as db:
            saved = db.execute(
                "SELECT state_key FROM provider_session_state WHERE session_id=? AND provider_id=? AND state_key LIKE 'responses_%'",
                (session_id, "openai-compatible"),
            ).fetchall()
        self.assertEqual(saved, [])

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

    def test_deepseek_chat_completions_disables_thinking_and_keeps_final_text(self) -> None:
        requests = []

        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"choices": [{"message": {"content": "可显示的最终回答"}}]}

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, _url, json, **_kwargs):
                requests.append(json)
                return Response()

        with patch.object(chat_routes.httpx, "AsyncClient", return_value=Client()):
            coroutine = chat_routes.call_model({
                "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
                "temperature": 0.2, "max_tokens": 80,
            }, "secret-key", [{"role": "user", "content": "请回复一句话"}])
            with self.assertRaises(StopIteration) as completed:
                coroutine.send(None)

        self.assertEqual(completed.exception.value, "可显示的最终回答")
        self.assertEqual(requests[0]["thinking"], {"type": "disabled"})

    def test_other_openai_compatible_endpoints_do_not_receive_deepseek_thinking_option(self) -> None:
        requests = []

        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"choices": [{"message": {"content": "正常回复"}}]}

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, _url, json, **_kwargs):
                requests.append(json)
                return Response()

        with patch.object(chat_routes.httpx, "AsyncClient", return_value=Client()):
            coroutine = chat_routes.call_model({
                "base_url": "https://api.example.com/v1", "model": "test-model",
                "temperature": 0.2, "max_tokens": 80,
            }, "secret-key", [{"role": "user", "content": "请回复一句话"}])
            with self.assertRaises(StopIteration):
                coroutine.send(None)

        self.assertNotIn("thinking", requests[0])

    def test_empty_chat_completion_content_is_failed_not_recorded_as_complete(self) -> None:
        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"choices": [{"message": {"content": "   "}}]}

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, *_args, **_kwargs):
                return Response()

        with patch.object(chat_routes.httpx, "AsyncClient", return_value=Client()):
            coroutine = chat_routes.call_model({
                "base_url": "https://api.example.com/v1", "model": "test-model",
                "temperature": 0.2, "max_tokens": 80,
            }, "secret-key", [{"role": "user", "content": "请回复一句话"}])
            with self.assertRaisesRegex(ValueError, "可显示的文本"):
                coroutine.send(None)

        log = list_model_call_logs()["items"][0]
        self.assertEqual(log["status"], "failed")
        self.assertNotIn("secret-key", str(get_model_call_log(log["id"])))

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

    def test_cli_turn_continues_after_the_sse_consumer_disconnects(self) -> None:
        session_id = "cli-disconnect-session"
        timestamp = common.now()
        with common.connect() as db:
            db.execute(
                "INSERT INTO sessions(id,title,created_at,updated_at,mode,origin) VALUES (?,?,?,?,?,?)",
                (session_id, "CLI 断连测试", timestamp, timestamp, "chat", "native"),
            )

        class DelayedProvider:
            display_name = "Delayed CLI"

            def __init__(self) -> None:
                self.started = asyncio.Event()
                self.release = asyncio.Event()
                self.cancelled = asyncio.Event()
                self.completed = asyncio.Event()

            async def run_turn(self, _context, _on_delta):
                self.started.set()
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
                    raise
                self.completed.set()
                return "断连后仍要保存的回复"

        provider = DelayedProvider()

        async def simulate_disconnect() -> None:
            stream = chat_routes.stream_cli_chat(
                "delayed-cli", session_id, "native",
                {"id": "user-1", "session_id": session_id, "content": "测试"},
                "测试", "系统提示", [],
            )
            consumer = asyncio.create_task(anext(stream))
            await provider.started.wait()
            consumer.cancel()
            try:
                await consumer
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
            await asyncio.sleep(0)
            self.assertFalse(provider.cancelled.is_set())
            provider.release.set()
            await asyncio.wait_for(provider.completed.wait(), timeout=1)
            await asyncio.sleep(0)

        with patch.object(chat_routes.cli_providers, "get", return_value=provider):
            asyncio.run(simulate_disconnect())

        with common.connect() as db:
            reply = db.execute(
                "SELECT content FROM messages WHERE session_id=? AND role='assistant'", (session_id,),
            ).fetchone()
        self.assertEqual(reply["content"], "断连后仍要保存的回复")
