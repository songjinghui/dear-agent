from unittest.mock import patch

from companion import common
from companion.settings_routes import SettingsInput, put_settings
from tests.support import ApiFeatureTestCase


class SettingsFeatureTests(ApiFeatureTestCase):
    def test_default_agent_personality_is_a_separate_setting(self) -> None:
        with patch.object(common.keyring, "get_password", return_value=None), patch.object(
            common.cli_providers, "list", return_value=[],
        ):
            result = common.read_settings()

        self.assertIn("温暖、坦诚", result["agent_definition"])

    def test_save_model_and_history_privacy_preferences(self) -> None:
        payload = SettingsInput(
            base_url="http://localhost:1234/v1",
            model="local-model",
            use_memories_in_cloud=False,
            use_archive_history=True,
        )
        with patch.object(common.keyring, "get_password", return_value=None), patch.object(
            common.cli_providers, "list", return_value=[],
        ):
            result = put_settings(payload)

        self.assertEqual(result["model"], "local-model")
        self.assertFalse(result["use_memories_in_cloud"])
        self.assertTrue(result["use_archive_history"])

    def test_local_vector_search_can_be_configured(self) -> None:
        payload = SettingsInput(
            base_url="http://localhost:1234/v1", model="local-model",
            use_memories_in_cloud=True,
            use_vector_memory_search=True, memory_embedding_model="embeddinggemma",
        )
        with patch.object(common.keyring, "get_password", return_value=None), patch.object(
            common.cli_providers, "list", return_value=[],
        ):
            result = put_settings(payload)

        self.assertTrue(result["use_vector_memory_search"])
        self.assertEqual(result["memory_embedding_model"], "embeddinggemma")

    def test_embedding_url_must_be_local(self) -> None:
        from fastapi import HTTPException

        payload = SettingsInput(
            base_url="http://localhost:1234/v1", model="local-model",
            memory_embedding_url="https://example.com/api/embed",
        )
        with self.assertRaises(HTTPException):
            put_settings(payload)

    def test_default_mode_can_be_saved_and_is_validated(self) -> None:
        payload = SettingsInput(
            base_url="http://localhost:1234/v1",
            model="local-model",
            default_mode="manager",
        )
        with patch.object(common.keyring, "get_password", return_value=None), patch.object(
            common.cli_providers, "list", return_value=[],
        ):
            result = put_settings(payload)

        self.assertEqual(result["default_mode"], "manager")

    def test_mode_prompts_can_be_edited_independently(self) -> None:
        payload = SettingsInput(
            base_url="http://localhost:1234/v1",
            model="local-model",
            chat_mode_prompt="聊天时先共情，再讨论可能的解释。",
            manager_mode_prompt="规划时优先给出低门槛的第一步。",
        )
        with patch.object(common.keyring, "get_password", return_value=None), patch.object(
            common.cli_providers, "list", return_value=[],
        ):
            result = put_settings(payload)

        self.assertEqual(result["chat_mode_prompt"], payload.chat_mode_prompt)
        self.assertEqual(result["manager_mode_prompt"], payload.manager_mode_prompt)

    def test_agent_definition_is_saved_and_loaded_separately(self) -> None:
        payload = SettingsInput(
            base_url="http://localhost:1234/v1",
            model="local-model",
            agent_definition="你是一个语气温和、先倾听再给建议的 Agent。",
        )
        with patch.object(common.keyring, "get_password", return_value=None), patch.object(
            common.cli_providers, "list", return_value=[],
        ):
            result = put_settings(payload)

        self.assertEqual(result["agent_definition"], payload.agent_definition)
        with common.connect() as db:
            self.assertIsNone(db.execute("SELECT id FROM memories WHERE content=?", (payload.agent_definition,)).fetchone())

    def test_context_window_can_be_configured_for_direct_api(self) -> None:
        payload = SettingsInput(
            base_url="http://localhost:1234/v1",
            model="local-model",
            context_window_tokens=12000,
        )
        with patch.object(common.keyring, "get_password", return_value=None), patch.object(
            common.cli_providers, "list", return_value=[],
        ):
            result = put_settings(payload)

        self.assertEqual(result["context_window_tokens"], 12000)
