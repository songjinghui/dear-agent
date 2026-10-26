from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .base import DeltaCallback, ProviderStatus, ProviderTurnContext


CODEX_MODEL = "gpt-6-astra"
MAX_CODEX_JSON_LINE_BYTES = 16 * 1024 * 1024
OAUTH_CLEARED_ENV_KEYS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "OPENAI_ORG_ID",
    "OPENAI_ORGANIZATION",
)


class CodexExecError(RuntimeError):
    """A completed Codex exec invocation reported a terminal failure."""


class CodexProvider:
    # Keep the existing identifier so saved model settings and native session state continue to work.
    provider_id = "codex-app-server"
    display_name = "Codex Agent"
    description = "通过本机 Codex CLI 的 exec --json 运行，并沿用 Codex CLI 登录。"

    def __init__(self) -> None:
        self._turn_slot = asyncio.Semaphore(1)

    def status(self) -> ProviderStatus:
        try:
            command = self._resolve_cli_command()
        except RuntimeError as exc:
            return ProviderStatus(False, False, str(exc))
        try:
            result = subprocess.run(
                [*command, "login", "status"], capture_output=True, text=True,
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
        thread_id = context.get_state("thread_id")
        resumed = bool(thread_id and context.get_state("model") == CODEX_MODEL)
        prompt = self._build_prompt(context, resumed)
        if context.record_request:
            context.record_request({
                "method": "codex exec --json",
                "input": prompt,
                "resumed_thread": resumed,
                "provider_context_note": (
                    "Codex 恢复线程时会使用其保存的线程上下文。"
                    if resumed else "本次请求从应用提供的上下文开始。"
                ),
            }, CODEX_MODEL)

        process = await self._start_exec(sandbox, thread_id if resumed else None)
        try:
            await self._write_prompt(process, prompt)
            return await self._read_exec_result(process, context, on_delta)
        finally:
            await self._terminate_process_if_running(process)

    @staticmethod
    def _build_prompt(context: ProviderTurnContext, resumed: bool) -> str:
        history_context = ""
        if not resumed and context.prior_history:
            history_context = "\n\n此前对话记录（仅用于理解上下文）：\n" + "\n".join(
                f"{'用户' if item['role'] == 'user' else '助手'}：{item['content']}" for item in context.prior_history
            )
        return f"{context.prompt}{history_context}\n\n用户本轮消息：\n{context.content}"

    async def _start_exec(self, sandbox: Path, thread_id: str | None) -> asyncio.subprocess.Process:
        command = self._resolve_cli_command()
        return await asyncio.create_subprocess_exec(
            *command, *self._exec_args(thread_id),
            cwd=str(sandbox), env=self._oauth_cli_environment(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            limit=MAX_CODEX_JSON_LINE_BYTES,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    @staticmethod
    def _exec_args(thread_id: str | None) -> list[str]:
        common = [
            "--json",
            "--ignore-user-config",
            "--skip-git-repo-check",
            "--config", "mcp_servers={}",
            "--config", "apps._default.enabled=false",
            "--model", CODEX_MODEL,
            "--config", 'model_provider="openai"',
            "--config", 'approval_policy="never"',
        ]
        if thread_id:
            return [
                "exec", "resume", thread_id,
                *common,
                "--config", 'sandbox_mode="read-only"',
                "--", "-",
            ]
        return ["exec", *common, "--sandbox", "read-only", "--", "-"]

    @staticmethod
    def _oauth_cli_environment() -> dict[str, str]:
        environment = dict(os.environ)
        for key in OAUTH_CLEARED_ENV_KEYS:
            environment.pop(key, None)
        return environment

    @staticmethod
    async def _write_prompt(process: asyncio.subprocess.Process, prompt: str) -> None:
        if process.stdin is None:
            raise RuntimeError("无法向 Codex CLI 发送请求。")
        process.stdin.write(prompt.encode())
        await process.stdin.drain()
        process.stdin.close()
        try:
            await process.stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def _read_exec_result(
        self,
        process: asyncio.subprocess.Process,
        context: ProviderTurnContext,
        on_delta: DeltaCallback,
    ) -> str:
        final_agent_message: str | None = None
        turn_completed = False
        while event := await self._read_exec_event(process):
            event_type = event.get("type")
            if event_type == "thread.started":
                thread_id = event.get("thread_id")
                if isinstance(thread_id, str) and thread_id:
                    context.set_state("thread_id", thread_id)
                    context.set_state("model", CODEX_MODEL)
            elif event_type == "item.completed":
                item = event.get("item")
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    text = item.get("text")
                    if isinstance(text, str) and text:
                        # `exec --json` can emit progress messages before the
                        # final answer. The final agent message is the only
                        # user-visible turn result; joining them would corrupt
                        # the structured response envelope.
                        final_agent_message = text
            elif event_type == "turn.failed":
                raise CodexExecError(self._event_error(event))
            elif event_type == "error":
                raise CodexExecError(self._event_error(event))
            elif event_type == "turn.completed":
                turn_completed = True

        returncode = await process.wait()
        if returncode != 0:
            raise CodexExecError(f"Codex CLI 以退出码 {returncode} 结束。")
        if not turn_completed:
            raise CodexExecError("Codex CLI 未确认本轮完成。")
        answer = (final_agent_message or "").strip()
        if not answer:
            raise RuntimeError("Codex 本轮没有返回文字。")
        await on_delta(answer)
        return answer

    @staticmethod
    async def _read_exec_event(process: asyncio.subprocess.Process) -> dict | None:
        if process.stdout is None:
            raise RuntimeError("Codex CLI 没有标准输出。")
        try:
            line = await asyncio.wait_for(process.stdout.readuntil(b"\n"), timeout=150)
        except asyncio.IncompleteReadError as exc:
            if not exc.partial:
                return None
            raise CodexExecError("Codex CLI 返回了不完整的 JSONL 事件。") from exc
        except asyncio.LimitOverrunError as exc:
            raise CodexExecError(
                f"Codex CLI 的单条 JSONL 事件超过 {MAX_CODEX_JSON_LINE_BYTES} 字节上限。"
            ) from exc
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CodexExecError("Codex CLI 返回了无法解析的 JSONL 事件。") from exc
        if not isinstance(event, dict):
            raise CodexExecError("Codex CLI 返回了格式错误的 JSONL 事件。")
        return event

    @staticmethod
    def _event_error(event: dict) -> str:
        error = event.get("error")
        if isinstance(error, dict):
            detail = error.get("message") or error.get("code")
            if isinstance(detail, str) and detail:
                return f"Codex 本轮未能完成：{detail}"
        message = event.get("message")
        if isinstance(message, str) and message:
            return f"Codex 本轮未能完成：{message}"
        return "Codex 本轮未能完成。请检查 Codex 登录状态和模型权限。"

    @staticmethod
    async def _terminate_process_if_running(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
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
        if process.returncode is None:
            process.kill()
            await process.wait()

    @staticmethod
    def _resolve_cli_command() -> list[str]:
        executable = shutil.which("codex.cmd") if os.name == "nt" else shutil.which("codex")
        if not executable:
            raise RuntimeError("没有找到 Codex CLI。请先安装 Codex CLI 并重新启动应用。")
        wrapper = Path(executable)
        if wrapper.suffix.lower() not in {".cmd", ".bat"}:
            return [str(wrapper)]

        try:
            wrapper_text = wrapper.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise RuntimeError("无法读取 Codex CLI 启动脚本，无法安全管理 Codex 进程。") from exc
        expanded = wrapper_text.replace("%~dp0", str(wrapper.parent) + os.sep)
        match = re.search(
            r'(?:(?:"(?P<node>[^"]*node(?:\.exe)?)")|node(?:\.exe)?)\s+"(?P<script>[^"]*codex\.js)"',
            expanded,
            re.IGNORECASE,
        )
        if not match:
            raise RuntimeError("无法识别 Codex CLI 启动脚本，无法安全管理 Codex 进程。")

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
