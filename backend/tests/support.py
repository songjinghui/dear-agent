from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from companion import common


class ApiFeatureTestCase(TestCase):
    """Run feature handlers against an isolated SQLite database."""

    def setUp(self) -> None:
        self.temp_data = tempfile.TemporaryDirectory(prefix="feature-test-", dir=Path(__file__).parent)
        self.original_data_dir = common.DATA_DIR
        self.original_db_path = common.DB_PATH
        common.DATA_DIR = Path(self.temp_data.name)
        common.DB_PATH = common.DATA_DIR / "test.sqlite3"
        self.tokenizer_patch = patch.object(common.jieba, "cut", side_effect=lambda text: [text])
        self.tokenizer_patch.start()
        common.initialize()

    def tearDown(self) -> None:
        self.tokenizer_patch.stop()
        common.DATA_DIR = self.original_data_dir
        common.DB_PATH = self.original_db_path
        self.temp_data.cleanup()


def archive_jsonl(archive_id: str = "feature-archive") -> str:
    import json

    records = [
        {"type": "archive", "format": "personal-conversation-archive", "version": "1.0",
         "archive_id": archive_id, "part_id": "part-1", "part_index": 1, "part_count": 1,
         "source_platform": "test"},
        {"type": "conversation", "id": "conversation-1", "title": "一次旅行", "created_at": None},
        {"type": "message", "id": "message-1", "conversation_id": "conversation-1", "sequence": 1,
         "timestamp": None, "speaker": "user", "speaker_label": None, "content": "我想去杭州旅行"},
        {"type": "message", "id": "message-2", "conversation_id": "conversation-1", "sequence": 2,
         "timestamp": None, "speaker": "assistant", "speaker_label": "助手", "content": "可以一起规划"},
        {"type": "summary", "id": "summary-1", "conversation_id": "conversation-1",
         "content": "用户讨论杭州旅行。", "based_on_message_ids": ["message-1"]},
    ]
    return "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n"
