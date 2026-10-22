from unittest import TestCase

from companion.context_budget import (
    estimate_message_tokens,
    input_token_budget,
    messages_after_sequence,
    split_history_for_compaction,
    should_compact,
    trim_context_to_budget,
)
from companion.context_builder import ComposedContext


class ContextBudgetTests(TestCase):
    def test_budget_reserves_output_and_safety_margin(self) -> None:
        budget = input_token_budget(context_window=10000, max_output_tokens=1200, safety_ratio=0.1)
        self.assertEqual(budget, 7800)
        self.assertTrue(should_compact(6000, budget, threshold=0.75))
        self.assertFalse(should_compact(5000, budget, threshold=0.75))

    def test_message_estimate_counts_content_and_message_overhead(self) -> None:
        estimate = estimate_message_tokens([{"role": "user", "content": "你好"}])
        self.assertGreaterEqual(estimate, 4)

    def test_summary_only_replaces_messages_through_its_sequence(self) -> None:
        history = [{"sequence": number, "role": "user", "content": f"消息 {number}"} for number in range(1, 9)]
        pending = messages_after_sequence(history, through_sequence=4)
        self.assertEqual([item["sequence"] for item in pending], [5, 6, 7, 8])

    def test_compaction_split_keeps_the_recent_tail_verbatim(self) -> None:
        history = [{"sequence": number, "content": f"消息 {number}"} for number in range(1, 11)]
        older, recent = split_history_for_compaction(history, keep_recent_messages=4)
        self.assertEqual([item["sequence"] for item in older], [1, 2, 3, 4, 5, 6])
        self.assertEqual([item["sequence"] for item in recent], [7, 8, 9, 10])

    def test_overflow_trim_preserves_system_prompt_and_current_input(self) -> None:
        context = ComposedContext(
            system_prompt="固定系统规则",
            prior_history=[
                {"id": "old", "role": "user", "content": "过去很长的内容" * 40},
                {"id": "recent", "role": "assistant", "content": "简短回答"},
            ],
            current_message={"id": "current", "role": "user", "content": "当前问题"},
        )

        trimmed = trim_context_to_budget(context, 30)

        self.assertIn("固定系统规则", trimmed.system_prompt)
        self.assertEqual(trimmed.current_message["id"], "current")
        self.assertNotIn("old", [item["id"] for item in trimmed.prior_history])
