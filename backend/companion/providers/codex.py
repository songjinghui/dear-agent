from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .base import DeltaCallback, ProviderStatus, ProviderTurnContext


CODEX_MODEL = "gpt-6-astra"
MAX_APP_SERVER_LINE_BYTES = 16 * 1024 * 1024


class CodexRequestError(RuntimeError):
    def __init__(self, method: str, error: dict):
        self.method = method
        self.error = error
        super().__init__(error.get("message", "Codex app-server 请求失败。"))


class CodexProvider:
    provider_id = "codex-app-server"
    display_name = "Codex Agent"
    description = "通过本机 Codex CLI app-server 运行，并沿用 Codex CLI 登录。"

    def __init__(self) -> None:
        self._turn_slot = asyncio.Semaphore(1)

    def status(self) -> ProviderStatus:
        if not shutil.which("codex"):
            return ProviderStatus(False, False, "未找到 Codex CLI。")
        try:
            result = subprocess.run(
                ["codex", "login", "status"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            authenticated = result.returncode == 0 and "logged in" in (result.stdout + result.stderr).lower()
            return ProviderStatus(True, authenticated, "Codex CLI 已登录。" if authenticated else "Codex CLI 尚未登录。")
        except (OSError, subprocess.TimeoutExpired):
            return ProviderStatus(True, False, "无法检查 Codex CLI 登录状态。")

    async def run_turn(self, context: ProviderTurnContext, on_delta: DeltaCallback) -> str:
        async with self._turn_slot:
            return await self._run_turn(context, on_delta)

    async def _run_turn(self, context: ProviderTurnContext, on_delta: DeltaCallback) -> str:
        status = await asyncio.to_thread(self.status)
        if not status.available:
            raise RuntimeError("没有找到 Codex CLI。请先安装 Codex CLI 并重新启动应用。")
        if not status.authenticated:
            raise RuntimeError("Codex CLI 尚未登录。请先在终端运行 codex login，然后重试。")

        sandbox = Path(context.data_dir) / "codex-agent-sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        process = await self._start_app_server(sandbox)
        try:
            line_count = 0
            max_line_bytes = 0
            large_line_samples: list[dict] = []
            request_for_log: dict = {}

            def persist_request(request: dict) -> None:
                if not context.record_request:
                    return
                request_for_log.clear()
                request_for_log.update(request)
                request_for_log["app_server_transport"] = {
                    "line_count": line_count,
                    "max_line_bytes": max_line_bytes,
                    "large_line_samples": list(large_line_samples),
                }
                context.record_request(dict(request_for_log), CODEX_MODEL)

            def observe_line_size(size: int, exact: bool = True) -> None:
                nonlocal line_count, max_line_bytes
                line_count += 1
                max_line_bytes = max(max_line_bytes, size)
                if size <= 64 * 1024 and exact:
                    return
                large_line_samples.append({"bytes": size, "exact": exact})
                del large_line_samples[:-20]

            await self._request(process, 1, "initialize", {
                "clientInfo": {"name": "personal_life_agent", "title": "个人生活 Agent", "version": "0.1.0"}
            }, observe_line_size)
            if process.stdin is None:
                raise RuntimeError("无法连接 Codex app-server。")
            process.stdin.write(b'{"method":"initialized"}\n')
            await process.stdin.drain()

            thread_id, resumed = await self._resume_or_start_thread(process, context, sandbox, observe_line_size)

            history_context = ""
            if not resumed and context.prior_history:
                history_context = "\n\n此前对话记录（仅用于理解上下文）：\n" + "\n".join(
                    f"{'用户' if item['role'] == 'user' else '助手'}：{item['content']}" for item in context.prior_history
                )
            turn_input = f"{context.prompt}{history_context}\n\n用户本轮消息：\n{context.content}"
            if context.record_request:
                persist_request({
                    "method": "turn/start",
                    "input": turn_input,
                    "resumed_thread": resumed,
                    "provider_context_note": "Codex 恢复线程时还会使用 Codex 自行管理的线程上下文；应用无法读取其内部完整上下文。" if resumed else "本次请求从应用提供的上下文开始。",
                })
            process.stdin.write((json.dumps({
                "id": 3, "method": "turn/start",
                "params": {"threadId": thread_id, "input": [{"type": "text", "text": turn_input}]},
            }) + "\n").encode("utf-8"))
            await process.stdin.drain()

            pieces: list[str] = []
            while True:
                message = await self._read_message(process, observe_line_size)
                if message.get("id") == 3 and "error" in message:
                    raise RuntimeError(message["error"].get("message", "Codex 无法开始本轮对话。"))
                if message.get("method") == "item/agentMessage/delta":
                    delta = message.get("params", {}).get("delta")
                    if isinstance(delta, str):
                        pieces.append(delta)
                        await on_delta(delta)
                elif message.get("method") == "turn/completed":
                    turn = message.get("params", {}).get("turn", {})
                    if turn.get("status") != "completed":
                        failure = turn.get("error")
                        if isinstance(failure, dict):
                            failure = failure.get("message") or failure.get("code")
                        detail = f"：{failure}" if failure else "。请检查 Codex 登录状态和模型权限。"
                        raise RuntimeError(f"Codex 本轮未能完成（{turn.get('status', '未知状态')}）{detail}")
                    break
            answer = "".join(pieces).strip()
            if not answer:
                raise RuntimeError("Codex 本轮没有返回文字。")
            return answer
        finally:
            try:
                if context.record_request:
                    if not request_for_log:
                        request_for_log.update({
                            "method": "app-server/transport-diagnostics",
                            "note": "仅记录本机协议消息的字节数，不记录消息内容。",
                        })
                    persist_request(dict(request_for_log))
            finally:
                await self._terminate_app_server(process)

    async def _resume_or_start_thread(
        self, process, context: ProviderTurnContext, sandbox: Path,
        on_line_size: Callable[[int, bool], None] | None = None,
    ) -> tuple[str, bool]:
        thread_id = context.get_state("thread_id")
        resumed = bool(thread_id and context.get_state("model") == CODEX_MODEL)
        if resumed:
            try:
                result = await self._request(process, 2, "thread/resume", {"threadId": thread_id}, on_line_size)
                thread_id = result.get("thread", {}).get("id", thread_id)
            except CodexRequestError as exc:
                if not self._is_missing_thread_error(exc):
                    raise
                thread_id, resumed = None, False
        if not resumed:
            result = await self._request(
                process, 2, "thread/start", {"cwd": str(sandbox), "model": CODEX_MODEL}, on_line_size,
            )
            thread_id = result.get("thread", {}).get("id")
            if not thread_id:
                raise RuntimeError("Codex 没有返回对话标识。")
            context.set_state("thread_id", thread_id)
            context.set_state("model", CODEX_MODEL)
        return thread_id, resumed

    @staticmethod
    def _is_missing_thread_error(error: CodexRequestError) -> bool:
        if error.method != "thread/resume":
            return False
        message = str(error).lower()
        missing = ("not found", "does not exist", "could not find", "unable to find", "unknown", "找不到", "不存在")
        target = ("thread", "conversation", "session", "rollout", "会话", "对话")
        return any(term in message for term in missing) and any(term in message for term in target)

    @staticmethod
    def _resolve_cli_command() -> list[str]:
        executable = shutil.which("codex")
        if not executable:
            raise RuntimeError("没有找到 Codex CLI。请先安装 Codex CLI 并重新启动应用。")
        wrapper = Path(executable)
        if wrapper.suffix.lower() not in {".cmd", ".bat"}:
            return [str(wrapper)]

        try:
            wrapper_text = wrapper.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise RuntimeError("无法读取 Codex CLI 启动脚本，无法安全管理 app-server 进程。") from exc
        expanded = wrapper_text.replace("%~dp0", str(wrapper.parent) + os.sep)
        match = re.search(
            r'(?:(?:"(?P<node>[^"]*node(?:\.exe)?)")|node(?:\.exe)?)\s+"(?P<script>[^"]*codex\.js)"',
            expanded,
            re.IGNORECASE,
        )
        if not match:
            raise RuntimeError("无法识别 Codex CLI 启动脚本，无法安全管理 app-server 进程。")

        node = Path(match.group("node")) if match.group("node") else None
        if node is None or not node.is_file():
            node_path = shutil.which("node.exe") or shutil.which("node")
            if not node_path:
                raise RuntimeError("启动 Codex CLI 需要 Node.js，但没有找到 node。")
            node = Path(node_path)
        node = node.resolve()
        script = Path(match.group("script")).resolve()
        if not script.is_file():
            raise RuntimeError("没有找到 Codex CLI 的启动脚本。")
        return [str(node), str(script)]

    @staticmethod
    async def _terminate_app_server(process: asyncio.subprocess.Process) -> None:
        if process.stdin and not process.stdin.is_closing():
            process.stdin.close()
            try:
                await asyncio.wait_for(process.stdin.wait_closed(), timeout=3)
            except (asyncio.TimeoutError, BrokenPipeError):
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
            return
        except asyncio.TimeoutError:
            pass

        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=8)
                return
            except asyncio.TimeoutError:
                pass
        if process.returncode is None:
            process.kill()
            await process.wait()

    @staticmethod
    async def _read_message(
        process: asyncio.subprocess.Process,
        on_line_size: Callable[[int, bool], None] | None = None,
    ) -> dict:
        if process.stdout is None:
            raise RuntimeError("Codex app-server 没有标准输出。")
        try:
            line = await asyncio.wait_for(process.stdout.readuntil(b"\n"), timeout=150)
        except asyncio.LimitOverrunError as exc:
            separator_found = str(exc).startswith("Separator is found")
            observed_bytes = exc.consumed + (1 if separator_found else 0)
            if on_line_size:
                on_line_size(observed_bytes, separator_found)
            amount = f"{observed_bytes} 字节" if separator_found else f"至少 {observed_bytes} 字节"
            raise RuntimeError(
                f"Codex app-server 的单条消息超过 {MAX_APP_SERVER_LINE_BYTES} 字节上限；已观察到 {amount}。"
            ) from exc
        if on_line_size:
            on_line_size(len(line), True)
        if not line:
            raise RuntimeError("Codex app-server 已退出，没有返回响应。")
        return json.loads(line.decode("utf-8"))

    async def _start_app_server(self, sandbox: Path) -> asyncio.subprocess.Process:
        command = self._resolve_cli_command()
        return await asyncio.create_subprocess_exec(
            *command, "app-server", "--listen", "stdio://", "--disable", "code_mode_host", "--enable", "multi_agent",
            "-c", "mcp_servers={}", "-c", "plugins={}",
            "-c", 'sandbox_mode="read-only"', "-c", 'approval_policy="never"',
            cwd=str(sandbox), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=MAX_APP_SERVER_LINE_BYTES,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    async def _request(
        self, process: asyncio.subprocess.Process, request_id: int, method: str, params: dict,
        on_line_size: Callable[[int, bool], None] | None = None,
    ) -> dict:
        if process.stdin is None:
            raise RuntimeError("无法连接 Codex app-server。")
        process.stdin.write((json.dumps({"id": request_id, "method": method, "params": params}) + "\n").encode("utf-8"))
        await process.stdin.drain()
        while True:
            message = await self._read_message(process, on_line_size)
            if message.get("id") == request_id:
                if "error" in message:
                    raise CodexRequestError(method, message["error"])
                return message.get("result", {})
