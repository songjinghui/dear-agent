import unittest

from companion.memory_protocol import parse_agent_response, pending_memory_merge_instructions


class MemoryProtocolTests(unittest.TestCase):
    def test_truncated_memory_envelope_keeps_the_complete_reply_and_discards_candidates(self) -> None:
        parsed = parse_agent_response(
            '{"reply":"可以先看看纸张和页数，再决定去哪里印。","memory_candidates":[{"content":"',
            "我想做一本照片册。",
        )

        self.assertEqual(parsed["reply"], "可以先看看纸张和页数，再决定去哪里印。")
        self.assertEqual(parsed["memory_candidates"], [])

    def test_merge_instruction_combines_one_ongoing_subject_into_a_topic_memory(self) -> None:
        prompt = pending_memory_merge_instructions([
            {"id": "candidate-1", "content": "A November trip to northern Xinjiang", "memory_type": "working"},
        ])

        self.assertIn("同一持续事项", prompt)
        self.assertIn("整合为一条主题记忆", prompt)
        self.assertNotIn("共同主题本身不足以合并", prompt)
