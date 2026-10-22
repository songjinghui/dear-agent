from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Awaitable, Callable, Protocol


DeltaCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class ProviderStatus:
    available: bool
    authenticated: bool | None = None
    message: str = ""


@dataclass
class ProviderTurnContext:
    session_id: str
    content: str
    prompt: str
    prior_history: list[dict]
    data_dir: str
    get_state: Callable[[str], str | None]
    set_state: Callable[[str, str], None]
    record_request: Callable[[dict, str | None], None] | None = None


class CLIProvider(Protocol):
    """Contract implemented by an installed local CLI provider plugin."""

    provider_id: str
    display_name: str
    description: str

    def status(self) -> ProviderStatus: ...

    async def run_turn(self, context: ProviderTurnContext, on_delta: DeltaCallback) -> str: ...


class CLIProviderRegistry:
    """Loads built-in and third-party providers registered through Python entry points."""

    def __init__(self) -> None:
        self._providers: dict[str, CLIProvider] = {}

    def register(self, provider: CLIProvider) -> None:
        provider_id = provider.provider_id.strip()
        if not provider_id or provider_id == "openai-compatible":
            raise ValueError("CLI provider id must be non-empty and cannot shadow openai-compatible.")
        if provider_id in self._providers:
            raise ValueError(f"Duplicate CLI provider id: {provider_id}")
        self._providers[provider_id] = provider

    def discover(self) -> None:
        try:
            candidates = entry_points(group="personal_companion.cli_providers")
        except TypeError:  # Python versions with the older entry_points API
            candidates = entry_points().get("personal_companion.cli_providers", [])
        for candidate in candidates:
            try:
                loaded = candidate.load()
                provider = loaded() if isinstance(loaded, type) else loaded
                self.register(provider)
            except Exception:
                # A broken optional plugin must not prevent the local app from starting.
                continue

    def get(self, provider_id: str) -> CLIProvider | None:
        return self._providers.get(provider_id)

    def list(self) -> list[dict]:
        result = []
        for provider in self._providers.values():
            try:
                status = provider.status()
            except Exception as exc:
                status = ProviderStatus(False, False, f"无法读取插件状态：{exc}")
            result.append({
                "id": provider.provider_id,
                "name": provider.display_name,
                "description": provider.description,
                "available": status.available,
                "authenticated": status.authenticated,
                "message": status.message,
            })
        return result
