import unittest
import asyncio
import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from pathlib import Path

from companion.providers.base import ProviderTurnContext
from companion.providers.codex import CODEX_MODEL, MAX_APP_SERVER_LINE_BYTES, CodexProvider, CodexRequestError


class CodexRecoveryTests(unittest.TestCase):
    def test_cmd_wrapper_is_resolved_to_direct_node_launch(self):
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

    def test_app_server_stream_limit_supports_large_json_lines(self):
        provider = CodexProvider()
        with patch("companion.providers.codex.asyncio.create_subprocess_exec", new_callable=AsyncMock) as start:
            pending = provider._start_app_server(Path("."))
            with self.assertRaises(StopIteration):
                pending.send(None)

        self.assertEqual(start.await_args.kwargs["limit"], MAX_APP_SERVER_LINE_BYTES)
        self.assertNotIn("cmd.exe", start.await_args.args)


class CodexStreamSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_reader_reports_large_line_size_without_recording_content(self):
        line = (json.dumps({"id": 1, "result": "x" * 70000}) + "\n").encode()
        reader = asyncio.StreamReader(limit=len(line) + 1)
        reader.feed_data(line)
        reader.feed_eof()
        sizes = []

        message = await CodexProvider._read_message(
            SimpleNamespace(stdout=reader), on_line_size=lambda size, exact: sizes.append((size, exact)),
        )

        self.assertEqual(message["id"], 1)
        self.assertEqual(sizes, [(len(line), True)])

    async def test_codex_turns_are_serialized_for_shared_provider_instance(self):
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

        async def no_delta(_text):
            return None

        provider._run_turn = fake_turn
        context = ProviderTurnContext(
            session_id="session", content="hello", prompt="", prior_history=[], data_dir="",
            get_state=lambda _key: None, set_state=lambda _key, _value: None,
        )
        await asyncio.wait_for(
            asyncio.gather(provider.run_turn(context, no_delta), provider.run_turn(context, no_delta)),
            timeout=1,
        )

        self.assertEqual(maximum_active, 1)

    async def test_app_server_cleanup_closes_stdin_and_waits_for_graceful_exit(self):
        if os.name != "nt":
            self.skipTest("Windows app-server lifecycle")
        provider = CodexProvider()
        stdin = SimpleNamespace(is_closing=Mock(return_value=False), close=Mock(), wait_closed=AsyncMock())
        process = SimpleNamespace(pid=43210, stdin=stdin, returncode=None, wait=AsyncMock())
        with patch("companion.providers.codex.subprocess.run") as taskkill:
            await provider._terminate_app_server(process)

        stdin.close.assert_called_once()
        stdin.wait_closed.assert_awaited_once()
        process.wait.assert_awaited_once()
        taskkill.assert_not_called()

    async def test_app_server_cleanup_kills_tracked_tree_after_grace_period(self):
        if os.name != "nt":
            self.skipTest("Windows app-server lifecycle")
        provider = CodexProvider()
        stdin = SimpleNamespace(is_closing=Mock(return_value=False), close=Mock(), wait_closed=AsyncMock())
        process = SimpleNamespace(
            pid=43210, stdin=stdin, returncode=None,
            wait=AsyncMock(side_effect=[asyncio.TimeoutError(), None]),
        )
        with patch("companion.providers.codex.subprocess.run") as taskkill:
            await provider._terminate_app_server(process)

        self.assertEqual(taskkill.call_args.args[0], ["taskkill", "/PID", "43210", "/T", "/F"])
        self.assertEqual(process.wait.await_count, 2)

    def test_missing_saved_thread_starts_fresh_and_replaces_saved_id(self):
        state = {"thread_id": "deleted-thread", "model": CODEX_MODEL}
        context = ProviderTurnContext(
            session_id="session-1", content="继续刚才的话题", prompt="系统提示",
            prior_history=[{"role": "user", "content": "之前的问题"}],
            data_dir="", get_state=state.get, set_state=state.__setitem__,
        )
        provider = CodexProvider()
        calls = []

        async def request(_process, _request_id, method, params, _on_line_size=None):
            calls.append((method, params))
            if method == "thread/resume":
                raise CodexRequestError(method, {"message": "Thread not found"})
            return {"thread": {"id": "replacement-thread"}}

        provider._request = request
        pending = provider._resume_or_start_thread(None, context, Path("."))
        with self.assertRaises(StopIteration) as completed:
            pending.send(None)
        thread_id, resumed = completed.exception.value

        self.assertEqual([method for method, _ in calls], ["thread/resume", "thread/start"])
        self.assertEqual(thread_id, "replacement-thread")
        self.assertFalse(resumed)
        self.assertEqual(state["thread_id"], "replacement-thread")
        self.assertEqual(state["model"], CODEX_MODEL)

    def test_other_resume_errors_are_not_hidden_by_starting_a_new_thread(self):
        context = ProviderTurnContext(
            session_id="session-1", content="test", prompt="", prior_history=[], data_dir="",
            get_state=lambda key: {"thread_id": "saved-thread", "model": CODEX_MODEL}.get(key),
            set_state=lambda _key, _value: None,
        )
        provider = CodexProvider()
        async def request(*_args):
            raise CodexRequestError("thread/resume", {"message": "authentication required"})

        provider._request = request

        pending = provider._resume_or_start_thread(None, context, Path("."))
        with self.assertRaises(CodexRequestError):
            pending.send(None)

    def test_only_missing_thread_errors_are_recoverable(self):
        missing = CodexRequestError("thread/resume", {"message": "找不到会话"})
        unrelated = CodexRequestError("thread/resume", {"message": "authentication required"})

        self.assertTrue(CodexProvider._is_missing_thread_error(missing))
        self.assertFalse(CodexProvider._is_missing_thread_error(unrelated))
