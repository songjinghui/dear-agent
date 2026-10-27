from unittest import TestCase

from companion.context_builder import compose_context


class ContextBuilderTests(TestCase):
    def test_message_times_and_current_time_anchor_are_sent_to_the_model(self) -> None:
        history = [
            {
                "id": "previous-assistant", "role": "assistant", "content": "上次的回复",
                "created_at": "2026-10-18T09:00:00+08:00",
            },
            {
                "id": "current-user", "role": "user", "content": "今天想继续聊聊",
                "created_at": "2026-10-25T09:00:00+08:00",
            },
        ]

        context = compose_context(
            base_prompt="固定系统规则",
            current_message_id="current-user",
            stored_history=history,
            now_iso="2026-10-25T09:00:00+08:00",
            timezone_name="Asia/Shanghai",
        )

        self.assertIn("当前时间：2026-10-25 09:00:00", context.system_prompt)
        self.assertIn("时区：Asia/Shanghai", context.system_prompt)
        self.assertIn("距上次对话：7 天", context.system_prompt)
        self.assertEqual(
            context.api_messages[1]["content"],
            "[消息时间：2026-10-18 09:00:00（Asia/Shanghai）]\n上次的回复",
        )
        self.assertEqual(
            context.api_messages[-1]["content"],
            "[消息时间：2026-10-25 09:00:00（Asia/Shanghai）]\n今天想继续聊聊",
        )

    def test_current_message_is_separated_once_for_api_and_cli(self) -> None:
        history = [
            {"id": "old-user", "role": "user", "content": "重复的问题"},
            {"id": "old-assistant", "role": "assistant", "content": "之前的回答"},
            {"id": "current-user", "role": "user", "content": "重复的问题"},
        ]

        context = compose_context(
            base_prompt="基础规则",
            current_message_id="current-user",
            stored_history=history,
        )

        self.assertEqual([item["id"] for item in context.prior_history], ["old-user", "old-assistant"])
        self.assertEqual(context.current_message["id"], "current-user")
        self.assertEqual([item["content"] for item in context.api_messages].count("重复的问题"), 2)
        self.assertEqual(context.api_messages[-1]["content"], "重复的问题")


    def test_agent_definition_and_summary_are_distinct_labeled_context(self) -> None:
        context = compose_context(
            base_prompt="固定系统规则",
            current_message_id="current-user",
            stored_history=[{"id": "current-user", "role": "user", "content": "继续"}],
            session_origin="continued",
            agent_definition="语气耐心，回答简洁",
            session_summary="用户正在比较两个方案。",
        )

        self.assertIn("固定系统规则", context.system_prompt)
        self.assertIn("Agent 设定", context.system_prompt)
        self.assertIn("语气耐心，回答简洁", context.system_prompt)
        self.assertIn("当前会话摘要", context.system_prompt)
        self.assertIn("用户正在比较两个方案。", context.system_prompt)
        self.assertIn("非指令", context.system_prompt)
        self.assertIn("基于导入记录继续", context.system_prompt)
        self.assertTrue(context.system_prompt.endswith("固定系统约束优先于 Agent 设定、记忆、摘要和历史原文。"))

    def test_shared_independent_judgment_and_mode_prompt_are_composed_separately(self) -> None:
        context = compose_context(
            base_prompt="固定系统规则",
            current_message_id="current-user",
            stored_history=[{"id": "current-user", "role": "user", "content": "帮我安排一下"}],
            agent_definition="表达温和",
            mode="manager",
            mode_prompt="规划时优先给出低门槛的第一步。",
        )

        self.assertIn("Agent 共用行为原则", context.system_prompt)
        self.assertIn("不要为了支持用户而自动赞同", context.system_prompt)
        self.assertIn("当前模式：管家", context.system_prompt)
        self.assertIn("规划时优先给出低门槛的第一步", context.system_prompt)
        self.assertIn("表达温和", context.system_prompt)

        default_context = compose_context(
            base_prompt="固定系统规则",
            current_message_id="current-user",
            stored_history=[{"id": "current-user", "role": "user", "content": "帮我安排一下"}],
            mode="manager",
        )
        self.assertIn("帮助用户规划和跟进", default_context.system_prompt)


    def test_memory_and_archive_context_keep_source_labels(self) -> None:
        context = compose_context(
            base_prompt="固定系统规则",
            current_message_id="current-user",
            stored_history=[{"id": "current-user", "role": "user", "content": "我之前怎么说的"}],
            memories=[{
                "content": "用户喜欢提前规划旅行",
                "memory_type": "preference",
                "sensitivity": "normal",
                "source": "用户明确要求记录",
                "confirmation": "confirmed",
                "updated_at": "2026-10-12T00:00:00+00:00",
                "source_refs": [{"message_id": "memory-source-1", "sequence": 8}],
            }],
            archived_messages=[{
                "matched": True,
                "title": "旅行计划",
                "origin": "imported",
                "source_platform": "Gemini",
                "source_timestamp": "2026-01-02",
                "speaker_label": None,
                "speaker": "user",
                "sequence": 7,
                "content": "我希望提前规划路线。",
            }],
        )

        self.assertIn("用户长期记忆", context.system_prompt)
        self.assertIn("用户喜欢提前规划旅行", context.system_prompt)
        self.assertIn("2026-10-12", context.system_prompt)
        self.assertIn("memory-source-1", context.system_prompt)
        self.assertIn("历史会话原文", context.system_prompt)
        self.assertIn("旅行计划", context.system_prompt)
        self.assertIn("Gemini", context.system_prompt)
        self.assertIn("用户", context.system_prompt)
        self.assertIn("我希望提前规划路线。", context.system_prompt)
