from unittest import TestCase
from unittest.mock import patch
import hashlib

from companion import memory_context
from tests.support import ApiFeatureTestCase


class MemoryRetrievalTests(ApiFeatureTestCase):
    def test_sensitive_memory_preview_returns_one_request_fingerprint_and_excludes_private(self) -> None:
        from companion import chat_routes, common

        with self.subTest("preview is local and exposes only sensitive candidates"):
            with common.connect() as db:
                db.execute(
                    "INSERT INTO sessions(id,title,created_at,updated_at,origin) VALUES (?,?,?,?, 'native')",
                    ("preview-session", "测试", "2026-10-22", "2026-10-22"),
                )
            sensitive_content = "亲密关系中的困难"
            candidates = [
                {"id": "sensitive", "content": sensitive_content, "sensitivity": "sensitive", "confirmation": "confirmed"},
                {"id": "private", "content": sensitive_content, "sensitivity": "private", "confirmation": "confirmed"},
            ]
            settings = {
                "use_memories_in_cloud": True,
                "use_vector_memory_search": False,
            }
            with patch.object(chat_routes, "read_settings", return_value=settings), \
                 patch.object(chat_routes, "search_relevant_memories", return_value=candidates), \
                 patch.object(memory_context.jieba, "cut", side_effect=lambda text: ["亲密关系", "困难"]):
                preview = chat_routes.preview_sensitive_memories(
                    chat_routes.MemoryPreviewInput(session_id="preview-session", content="亲密关系怎么聊")
                )

        self.assertEqual([item["id"] for item in preview["memories"]], ["sensitive"])
        self.assertEqual(
            preview["memories"][0]["fingerprint"],
            hashlib.sha256(sensitive_content.encode("utf-8")).hexdigest(),
        )

    def test_only_relevant_confirmed_current_non_sensitive_memories_are_selected(self) -> None:
        tokens = {
            "电影推荐": ["电影", "推荐"],
            "用户喜欢看电影": ["用户", "喜欢", "看", "电影"],
            "我想学烹饪": ["我", "想", "学", "烹饪"],
        }
        candidates = [
            {"id": "relevant", "content": "用户喜欢看电影", "sensitivity": "normal", "confirmation": "confirmed", "expires_at": None},
            {"id": "unrelated", "content": "我想学烹饪", "sensitivity": "normal", "confirmation": "confirmed", "expires_at": None},
            {"id": "sensitive", "content": "用户喜欢看电影", "sensitivity": "sensitive", "confirmation": "confirmed", "expires_at": None},
            {"id": "unconfirmed", "content": "用户喜欢看电影", "sensitivity": "normal", "confirmation": "pending", "expires_at": None},
            {"id": "expired", "content": "用户喜欢看电影", "sensitivity": "normal", "confirmation": "confirmed", "expires_at": "2026-10-01T00:00:00+00:00"},
        ]

        with patch.object(memory_context.jieba, "cut", side_effect=lambda text: tokens.get(text, [])):
            selected = memory_context.select_memories_for_context(
                "电影推荐", candidates, now_iso="2026-10-15T00:00:00+00:00", limit=4,
            )

        self.assertEqual([item["id"] for item in selected], ["relevant"])

    def test_more_relevant_candidates_rank_first_and_limit_is_respected(self) -> None:
        tokens = {
            "杭州旅行计划": ["杭州", "旅行", "计划"],
            "我喜欢杭州旅行": ["我", "喜欢", "杭州", "旅行"],
            "我去过杭州": ["我", "去过", "杭州"],
        }
        candidates = [
            {"id": "one-term", "content": "我去过杭州", "sensitivity": "normal", "confirmation": "confirmed"},
            {"id": "two-terms", "content": "我喜欢杭州旅行", "sensitivity": "normal", "confirmation": "confirmed"},
        ]

        with patch.object(memory_context.jieba, "cut", side_effect=lambda text: tokens.get(text, [])):
            selected = memory_context.select_memories_for_context("杭州旅行计划", candidates, limit=1)

        self.assertEqual([item["id"] for item in selected], ["two-terms"])

    def test_bm25_rank_breaks_ties_in_final_memory_selection(self) -> None:
        tokens = {
            "东京旅行计划": ["东京", "旅行", "计划"],
            "用户讨论东京旅行": ["用户", "讨论", "东京", "旅行"],
        }
        candidates = [
            {"id": "weaker-ft-match", "content": "用户讨论东京旅行", "importance": 3,
             "updated_at": "2026-10-01", "sensitivity": "normal", "confirmation": "confirmed", "_bm25_score": -0.2},
            {"id": "stronger-ft-match", "content": "用户讨论东京旅行", "importance": 3,
             "updated_at": "2026-10-01", "sensitivity": "normal", "confirmation": "confirmed", "_bm25_score": -0.9},
        ]

        with patch.object(memory_context.jieba, "cut", side_effect=lambda text: tokens.get(text, [])):
            selected = memory_context.select_memories_for_context("东京旅行计划", candidates)

        self.assertEqual([item["id"] for item in selected], ["stronger-ft-match", "weaker-ft-match"])

    def test_single_character_chinese_topic_can_match_a_preference(self) -> None:
        tokens = {"推荐书": ["推荐", "书"], "用户喜欢看书": ["用户", "喜欢", "看", "书"]}
        candidate = {"id": "reading", "content": "用户喜欢看书", "sensitivity": "normal", "confirmation": "confirmed"}

        with patch.object(memory_context.jieba, "cut", side_effect=lambda text: tokens.get(text, [])):
            selected = memory_context.select_memories_for_context("推荐书", [candidate])

        self.assertEqual([item["id"] for item in selected], ["reading"])

    def test_follow_up_can_use_recent_session_topic_for_memory_search(self) -> None:
        tokens = {
            "那个怎么样": ["那个", "怎么样"],
            "电影推荐": ["电影", "推荐"],
            "用户喜欢电影": ["用户", "喜欢", "电影"],
        }
        candidate = {"id": "film", "content": "用户喜欢电影", "sensitivity": "normal", "confirmation": "confirmed"}

        with patch.object(memory_context.jieba, "cut", side_effect=lambda text: tokens.get(text, [])):
            selected = memory_context.select_memories_for_context(
                "那个怎么样", [candidate], supplemental_context="电影推荐",
            )

        self.assertEqual([item["id"] for item in selected], ["film"])

    def test_sensitive_memories_require_matching_per_request_approval_and_private_never_enter_context(self) -> None:
        tokens = {
            "亲密关系怎么聊": ["亲密关系", "聊"],
            "亲密关系中的困难": ["亲密关系", "困难"],
        }
        candidates = [
            {"id": "sensitive", "content": "亲密关系中的困难", "sensitivity": "sensitive", "confirmation": "confirmed"},
            {"id": "private", "content": "亲密关系中的困难", "sensitivity": "private", "confirmation": "confirmed"},
        ]
        fingerprint = hashlib.sha256("亲密关系中的困难".encode("utf-8")).hexdigest()
        with patch.object(memory_context.jieba, "cut", side_effect=lambda text: tokens.get(text, [])):
            without_approval = memory_context.select_memories_for_context("亲密关系怎么聊", candidates)
            selected = memory_context.select_memories_for_context(
                "亲密关系怎么聊", candidates, approved_sensitive={"sensitive": fingerprint},
            )

        self.assertEqual(without_approval, [])
        self.assertEqual([item["id"] for item in selected], ["sensitive"])

    def test_vector_similarity_can_retrieve_paraphrase_without_keyword_overlap(self) -> None:
        from companion import common
        from companion.memory_routes import search_relevant_memories

        memory = {
            "id": "movie-preference", "content": "我喜欢看电影", "memory_type": "semantic",
            "importance": 4, "source": "test", "confirmation": "confirmed", "sensitivity": "normal",
            "created_at": "2026-10-01T00:00:00+00:00", "updated_at": "2026-10-01T00:00:00+00:00",
            "expires_at": None, "source_refs": "[]",
        }
        with common.connect() as db:
            db.executemany(
                "INSERT INTO memories(id,content,memory_type,importance,source,confirmation,sensitivity,created_at,updated_at,expires_at,source_refs) "
                "VALUES (:id,:content,:memory_type,:importance,:source,:confirmation,:sensitivity,:created_at,:updated_at,:expires_at,:source_refs)",
                [memory, {**memory, "id": "private-memory", "content": "严格私密的家庭经历", "sensitivity": "private"}],
            )
            for key, value in {
                "use_vector_memory_search": "true",
                "memory_embedding_model": "test-embed",
                "memory_embedding_url": "http://127.0.0.1:11434/api/embed",
            }.items():
                db.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

        from unittest.mock import patch

        embedded_texts = []

        def fake_embeddings(texts, model, endpoint):
            embedded_texts.extend(texts)
            return [[1.0, 0.0] if text == "周末想找部片子" or text == "我喜欢看电影" else [0.0, 1.0] for text in texts]

        with patch("companion.memory_embeddings.embed_texts", side_effect=fake_embeddings):
            selected = search_relevant_memories("周末想找部片子", limit=4, vector_search=True)

        self.assertEqual([item["id"] for item in selected], ["movie-preference"])
        self.assertNotIn("严格私密的家庭经历", embedded_texts)
