from unittest import TestCase

from companion.context_runtime import call_with_context_retry, prepare_direct_api_context
from companion.context_budget import estimate_message_tokens, input_token_budget
from companion.context_builder import ComposedContext
from companion.context_store import get_session_summary
from companion.session_routes import SessionInput, create_session
from tests.support import ApiFeatureTestCase


def complete_immediately(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("测试用异步替身不应挂起等待事件循环。")


class ContextRuntimeTests(ApiFeatureTestCase):
    def test_over_budget_history_is_summarized_and_recent_turns_stay_verbatim(self) -> None:
        session = create_session(SessionInput(title="自动压缩"))
        history = [
            {"id": f"m{sequence}", "sequence": sequence, "role": "user", "content": "搬家预算和地点讨论" * 40}
            for sequence in range(1, 11)
        ]
        calls = []

        async def summarize(compact_settings, _key, messages):
            calls.append((compact_settings, messages))
            return "用户在比较搬家地点和预算。"

        context = complete_immediately(prepare_direct_api_context(
            settings={"context_window_tokens": 8000, "max_tokens": 500},
            api_key="test-key",
            session_id=session["id"],
            current_message_id="m10",
            all_messages=history,
            summary_record={"summary": "", "through_sequence": 0},
            session_origin="native",
            base_prompt="固定规则",
            agent_definition="回答简洁",
            memories=[],
            archived_messages=[],
            call_model=summarize,
        ))

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0]["max_tokens"], 250)
        self.assertIn("用户在比较搬家地点和预算。", context.system_prompt)
        self.assertEqual(context.current_message["id"], "m10")
        self.assertEqual([item["id"] for item in context.prior_history], [f"m{n}" for n in range(3, 10)])
        persisted = get_session_summary(session["id"])
        self.assertEqual(persisted["through_sequence"], 2)

    def test_summary_failure_keeps_previous_summary_and_uses_recent_raw_history(self) -> None:
        history = [
            {"id": f"m{sequence}", "sequence": sequence, "role": "user", "content": "过去的内容" * 100}
            for sequence in range(1, 11)
        ]

        async def fail_summary(_settings, _key, _messages):
            raise RuntimeError("summary unavailable")

        context = complete_immediately(prepare_direct_api_context(
            settings={"context_window_tokens": 8000, "max_tokens": 500},
            api_key="test-key",
            session_id="not-persisted",
            current_message_id="m10",
            all_messages=history,
            summary_record={"summary": "上一版摘要", "through_sequence": 0},
            session_origin="native",
            base_prompt="固定规则",
            agent_definition="",
            memories=[],
            archived_messages=[],
            call_model=fail_summary,
        ))

        self.assertIn("上一版摘要", context.system_prompt)
        self.assertEqual(context.current_message["id"], "m10")
        self.assertNotIn("m1", [item["id"] for item in context.prior_history])

    def test_under_budget_history_does_not_call_summarizer(self) -> None:
        history = [{"id": "m1", "sequence": 1, "role": "user", "content": "短消息"}]
        calls = []

        async def summarize(*_args):
            calls.append(True)
            return "不应生成"

        context = complete_immediately(prepare_direct_api_context(
            settings={"context_window_tokens": 8000, "max_tokens": 500},
            api_key="test-key",
            session_id="unused",
            current_message_id="m1",
            all_messages=history,
            summary_record={"summary": "", "through_sequence": 0},
            session_origin="native",
            base_prompt="固定规则",
            agent_definition="",
            memories=[],
            archived_messages=[],
            call_model=summarize,
        ))

        self.assertEqual(calls, [])
        self.assertEqual(context.current_message["id"], "m1")

    def test_overlong_saved_summary_is_shortened_for_the_current_turn(self) -> None:
        history = [
            {"id": "m1", "sequence": 1, "role": "user", "content": "小澄下个月过生日。"},
            {"id": "m2", "sequence": 2, "role": "assistant", "content": "我记住了。"},
            {"id": "m3", "sequence": 3, "role": "user", "content": "我当时准备送她什么？"},
        ]

        async def unexpected_summary(*_args):
            raise AssertionError("已有摘要时不应再为这组短历史调用摘要模型")

        context = complete_immediately(prepare_direct_api_context(
            settings={"context_window_tokens": 4096, "max_tokens": 350},
            api_key="test-key",
            session_id="not-persisted",
            current_message_id="m3",
            all_messages=history,
            summary_record={
                "summary": "小澄下个月过生日，用户准备送她一本相册。" + "补充细节。" * 1000,
                "through_sequence": 2,
            },
            session_origin="native",
            base_prompt="固定规则",
            agent_definition="",
            memories=[],
            archived_messages=[],
            call_model=unexpected_summary,
        ))

        budget = input_token_budget(context_window=4096, max_output_tokens=350)
        self.assertEqual(context.current_message["id"], "m3")
        self.assertIn("小澄下个月过生日", context.system_prompt)
        self.assertLessEqual(estimate_message_tokens(context.api_messages), budget)

    def test_updated_summary_keeps_existing_early_facts(self) -> None:
        history = [
            {"id": f"m{sequence}", "sequence": sequence, "role": "user", "content": "新的日常内容。" * 40}
            for sequence in range(1, 12)
        ]

        async def summarize(_settings, _key, _messages):
            return "后来发生的新事情。"

        context = complete_immediately(prepare_direct_api_context(
            settings={"context_window_tokens": 8000, "max_tokens": 500},
            api_key="test-key",
            session_id="not-persisted",
            current_message_id="m11",
            all_messages=history,
            summary_record={"summary": "早期事实：小澄生日要送照片册。", "through_sequence": 0},
            session_origin="native",
            base_prompt="固定规则",
            agent_definition="",
            memories=[],
            archived_messages=[],
            call_model=summarize,
        ))

        self.assertIn("早期事实：小澄生日要送照片册。", context.system_prompt)

    def test_only_context_overflow_gets_one_conservative_retry(self) -> None:
        context = ComposedContext(
            system_prompt="固定规则",
            prior_history=[{"id": "old", "role": "assistant", "content": "旧内容" * 100}],
            current_message={"id": "current", "role": "user", "content": "当前问题"},
        )
        calls = []

        class FakeResponse:
            status_code = 400

            @staticmethod
            def json():
                return {"error": {"code": "context_length_exceeded"}}

        class FakeOverflow(Exception):
            response = FakeResponse()

        async def send(_settings, _key, messages):
            calls.append(messages)
            if len(calls) == 1:
                raise FakeOverflow()
            return "已完成"

        answer = complete_immediately(call_with_context_retry({"max_tokens": 500}, "key", context, send))

        self.assertEqual(answer, "已完成")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][-1], {"role": "user", "content": "当前问题"})
        self.assertEqual(calls[1][0]["content"], "固定规则")
        self.assertEqual(len(calls[1]), 2)

    def test_unrelated_provider_error_is_not_retried(self) -> None:
        context = ComposedContext(
            system_prompt="固定规则",
            prior_history=[],
            current_message={"id": "current", "role": "user", "content": "当前问题"},
        )
        calls = []

        class FakeResponse:
            status_code = 401

            @staticmethod
            def json():
                return {"message": "invalid key"}

        class FakeUnauthorized(Exception):
            response = FakeResponse()

        async def send(*_args):
            calls.append(True)
            raise FakeUnauthorized()

        with self.assertRaises(FakeUnauthorized):
            complete_immediately(call_with_context_retry({}, "key", context, send))
        self.assertEqual(len(calls), 1)
