import json
from unittest import TestCase

from companion.memory_protocol import parse_agent_response


class MemoryProtocolTests(TestCase):
    def test_parses_user_reply_and_only_grounded_memory_candidates(self) -> None:
        raw = json.dumps({
            "reply": "我可以帮你安排准备计划。",
            "memory_candidates": [
                {
                    "content": "周五前要交项目方案",
                    "memory_type": "working",
                    "importance": 4,
                    "sensitivity": "normal",
                    "evidence": "周五前要交项目方案",
                    "rationale": "明确的近期任务",
                },
                {
                    "content": "用户讨厌老板",
                    "memory_type": "semantic",
                    "importance": 4,
                    "sensitivity": "normal",
                    "evidence": "老板根本不尊重我",
                    "rationale": "推断出的长期态度",
                },
            ],
        }, ensure_ascii=False)

        result = parse_agent_response(raw, "我周五前要交项目方案。")

        self.assertEqual(result["reply"], "我可以帮你安排准备计划。")
        self.assertEqual(len(result["memory_candidates"]), 1)
        self.assertEqual(result["memory_candidates"][0]["content"], "周五前要交项目方案")

    def test_plain_text_or_invalid_json_remains_a_normal_reply(self) -> None:
        for raw in ("普通文字回复", '{"reply": 4, "memory_candidates": []}'):
            with self.subTest(raw=raw):
                result = parse_agent_response(raw, "用户消息")
                self.assertEqual(result, {"reply": raw, "memory_candidates": []})

    def test_rejects_unknown_types_and_invalid_sensitivity(self) -> None:
        raw = json.dumps({
            "reply": "收到。",
            "memory_candidates": [
                {"content": "A", "memory_type": "unknown", "sensitivity": "normal", "evidence": "A"},
                {"content": "B", "memory_type": "episodic", "sensitivity": "unknown", "evidence": "B"},
            ],
        })
        self.assertEqual(parse_agent_response(raw, "A B")["memory_candidates"], [])
