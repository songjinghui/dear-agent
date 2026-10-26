import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from companion.providers.base import ProviderStatus, ProviderTurnContext
from companion.providers.codex import CODEX_MODEL, CodexProvider


class FakeStdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self._closed = False

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self._closed = True

    def is_closing(self) -> bool:
        return self._closed

    async def wait_closed(self) -> None:
        return None


class FakeExecProcess:
    def __init__(self, events: list[dict], returncode: int = 0) -> None:
        self.stdin = FakeStdin()
        self.stdout = asyncio.StreamReader()
        for event in events:
            self.stdout.feed_data((json.dumps(event) + "\n").encode())
        self.stdout.feed_eof()
        self.returncode = returncode

    async def wait(self) -> int:
        return self.returncode


def turn_context(state: dict[str, str], **overrides) -> ProviderTurnContext:
    values = {
        "session_id": "session-1",
        "content": "本轮问题",
        "prompt": "系统提示",
        "prior_history": [{"role": "user", "content": "此前的问题"}],
        "data_dir": "",
        "get_state": state.get,
        "set_state": state.__setitem__,
    }
    values.update(overrides)
    return ProviderTurnContext(**values)


class CodexExecJsonTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_turn_uses_exec_json_with_stdin_prompt_and_persists_thread(self) -> None:
        provider = CodexProvider()
        state: dict[str, str] = {}
        deltas: list[str] = []
        recorded: list[tuple[dict, str | None]] = []
        process = FakeExecProcess([
            {"type": "thread.started", "thread_id": "new-thread"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "你好"}},
            {"type": "turn.completed"},
        ])

        async def on_delta(text: str) -> None:
            deltas.append(text)

        context = turn_context(state, record_request=lambda request, model: recorded.append((request, model)))
        with (
            patch.object(provider, "status", return_value=ProviderStatus(True, True)),
            patch.object(provider, "_resolve_cli_command", return_value=["node.exe", "codex.js"]),
            patch("companion.providers.codex.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as start,
        ):
            answer = await provider._run_turn(context, on_delta)

        self.assertEqual(answer, "你好")
        self.assertEqual(deltas, ["你好"])
        self.assertEqual(state, {"thread_id": "new-thread", "model": CODEX_MODEL})
        args = start.await_args.args
        self.assertEqual(args[:4], ("node.exe", "codex.js", "exec", "--json"))
        self.assertIn("--sandbox", args)
        self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
        self.assertIn("--skip-git-repo-check", args)
        self.assertEqual(args[-2:], ("--", "-"))
        self.assertNotIn("app-server", args)
        self.assertNotIn("本轮问题", args)
        self.assertEqual(b"".join(process.stdin.writes).decode(), "系统提示\n\n此前对话记录（仅用于理解上下文）：\n用户：此前的问题\n\n用户本轮消息：\n本轮问题")
        self.assertEqual(recorded[0][0]["method"], "codex exec --json")
        self.assertEqual(recorded[0][1], CODEX_MODEL)
        spawned_env = start.await_args.kwargs["env"]
        self.assertNotIn("OPENAI_API_KEY", spawned_env)
        self.assertNotIn("OPENAI_BASE_URL", spawned_env)

    async def test_saved_thread_uses_exec_resume_and_avoids_duplicate_history(self) -> None:
        provider = CodexProvider()
        state = {"thread_id": "saved-thread", "model": CODEX_MODEL}
        process = FakeExecProcess([
            {"type": "item.completed", "item": {"type": "agent_message", "text": "续聊回复"}},
            {"type": "turn.completed"},
        ])

        async def no_delta(_text: str) -> None:
            return None

        with (
            patch.object(provider, "status", return_value=ProviderStatus(True, True)),
            patch.object(provider, "_resolve_cli_command", return_value=["node.exe", "codex.js"]),
            patch("companion.providers.codex.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as start,
        ):
            answer = await provider._run_turn(turn_context(state), no_delta)

        self.assertEqual(answer, "续聊回复")
        args = start.await_args.args
        self.assertEqual(args[:6], ("node.exe", "codex.js", "exec", "resume", "saved-thread", "--json"))
        self.assertIn("--skip-git-repo-check", args)
        stdin_text = b"".join(process.stdin.writes).decode()
        self.assertIn("用户本轮消息：\n本轮问题", stdin_text)
        self.assertNotIn("此前对话记录", stdin_text)

    async def test_only_final_agent_message_becomes_the_chat_reply(self) -> None:
        provider = CodexProvider()
        deltas: list[str] = []
        final_reply = '{"reply":"北疆出行时请注意防风。","memory_candidates":[]}'
        process = FakeExecProcess([
            {"type": "item.completed", "item": {"type": "agent_message", "text": "我先查一下温度记录。"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": final_reply}},
            {"type": "turn.completed"},
        ])

        async def on_delta(text: str) -> None:
            deltas.append(text)

        answer = await provider._read_exec_result(process, turn_context({}), on_delta)

        self.assertEqual(answer, final_reply)
        self.assertEqual(deltas, [final_reply])

    async def test_codex_turns_are_serialized_for_shared_provider_instance(self) -> None:
        provider = CodexProvider()
        active = 0
        maximum_active = 0

        async def fake_turn(_context, _on_delta):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return "done"

        async def no_delta(_text: str) -> None:
            return None

        provider._run_turn = fake_turn
        context = turn_context({})
        await asyncio.wait_for(
            asyncio.gather(provider.run_turn(context, no_delta), provider.run_turn(context, no_delta)),
            timeout=1,
        )

        self.assertEqual(maximum_active, 1)


class CodexCliCommandTests(unittest.TestCase):
    def test_status_uses_the_resolved_cli_command(self) -> None:
        provider = CodexProvider()
        completed = SimpleNamespace(returncode=0, stdout="Logged in using ChatGPT", stderr="")
        with (
            patch.object(CodexProvider, "_resolve_cli_command", return_value=["node.exe", "codex.js"]) as resolve,
            patch("companion.providers.codex.subprocess.run", return_value=completed) as run,
        ):
            status = provider.status()

        resolve.assert_called_once_with()
        self.assertEqual(run.call_args.args[0], ["node.exe", "codex.js", "login", "status"])
        self.assertTrue(status.available)
        self.assertTrue(status.authenticated)

    def test_cmd_wrapper_is_resolved_to_direct_node_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = root / "codex.CMD"
            node = root / "node.exe"
            script = root / "codex.js"
            wrapper.write_text(f'"{node}" "{script}" %*', encoding="utf-8")
            node.touch()
            script.touch()
            with patch("companion.providers.codex.shutil.which", return_value=str(wrapper)):
                command = CodexProvider._resolve_cli_command()

        self.assertEqual(command, [str(node.resolve()), str(script.resolve())])
