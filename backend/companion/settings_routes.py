"""HTTP endpoints and handlers for the settings domain."""

from typing import Literal

from .common import *  # noqa: F403
from .mode_prompts import DEFAULT_MODE_PROMPTS
from .memory_embeddings import is_local_embedding_url

class SettingsInput(BaseModel):
    provider: str = Field(default="openai-compatible", min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=1200, ge=1, le=32000)
    context_window_tokens: int = Field(default=8192, ge=2048, le=1000000)
    use_memories_in_cloud: bool = False
    use_vector_memory_search: bool = False
    memory_embedding_url: str = Field(default="http://127.0.0.1:11434/api/embed", max_length=500)
    memory_embedding_model: str = Field(default="", max_length=200)
    use_archive_history: bool = False
    default_mode: Literal["chat", "manager"] = "chat"
    chat_mode_prompt: str = Field(default=DEFAULT_MODE_PROMPTS["chat"], max_length=5000)
    manager_mode_prompt: str = Field(default=DEFAULT_MODE_PROMPTS["manager"], max_length=5000)
    agent_definition: str = Field(default="", max_length=5000)
    api_key: str | None = None


@app.get("/api/settings")
def get_settings() -> dict:
    return read_settings()


@app.put("/api/settings")
def put_settings(payload: SettingsInput) -> dict:
    if payload.provider != "openai-compatible" and cli_providers.get(payload.provider) is None:
        raise HTTPException(400, "所选 CLI 插件尚未安装或注册。")
    if not is_local_embedding_url(payload.memory_embedding_url):
        raise HTTPException(400, "记忆嵌入地址必须使用本机 Ollama /api/embed 服务。")
    values = payload.model_dump(exclude={"api_key"})
    with connect() as db:
        for key, value in values.items():
            db.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value) if isinstance(value, bool) else str(value)),
            )
    if payload.api_key:
        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, payload.api_key)
        except Exception as exc:
            raise HTTPException(500, "无法写入 Windows 凭据管理器；密钥没有保存。") from exc
    return read_settings()


@app.delete("/api/settings/api-key")
def delete_api_key() -> dict:
    try:
        if keyring.get_password(KEYRING_SERVICE, KEYRING_USER):
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception as exc:
        raise HTTPException(500, "无法从系统凭据存储删除密钥。") from exc
    return {"has_api_key": False}
