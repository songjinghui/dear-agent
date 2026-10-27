"""HTTP endpoints and handlers for the chat domain."""

from .common import *  # noqa: F403
from .archive_import_routes import index_message
from .archive_read_routes import search_history_context, search_session_context
from .context_builder import compose_context
from .context_runtime import ContextBudgetExceeded, call_with_context_retry, prepare_direct_api_context
from .context_store import get_session_summary
from .memory_context import memory_fingerprint, select_memories_for_context
from .memory_protocol import MEMORY_RESPONSE_INSTRUCTIONS
from .memory_routes import MemoryInput, create_memory, search_relevant_memories
from .memory_suggestions import process_agent_response
from .model_call_logs import (
    current_call_context, finish_model_call, model_call_context, record_model_call,
    update_model_call_request,
)

class ChatInput(BaseModel):
    session_id: str
    content: str = Field(min_length=1, max_length=20000)
    sensitive_approvals: dict[str, str] = Field(default_factory=dict, max_length=4)


class MemoryPreviewInput(BaseModel):
    session_id: str
    content: str = Field(min_length=1, max_length=20000)


def memory_directive(text: str) -> str | None:
    match = re.match(r"^\s*(?:请)?记住(?:这个|一下)?[：:]\s*(.+?)\s*$", text, re.S)
    if match:
        return match.group(1).strip()
    if re.match(r"^\s*(?:请)?记住这个\s*$", text):
        return None
    return None


async def call_model(settings: dict, api_key: str, messages: list[dict]) -> str:
    import time

    base = settings["base_url"].rstrip("/")
    context = current_call_context()
    session_id = context.get("session_id")
    resumable_turn = settings.get("api_protocol") == "responses" and context.get("purpose") == "对话"
    if settings.get("api_protocol") == "responses":
        endpoint = base if base.endswith("/responses") else f"{base}/responses"
        instructions = "\n\n".join(item["content"] for item in messages if item.get("role") == "system")
        api_input = [item for item in messages if item.get("role") != "system"]
        payload = {
            "model": settings["model"],
            "instructions": instructions,
            "input": api_input,
            "temperature": settings["temperature"],
            "max_output_tokens": settings["max_tokens"],
        }
        continuation = _get_responses_continuation(session_id, settings) if resumable_turn and session_id else None
        if continuation:
            payload[continuation[0]] = continuation[1]
            latest_user_message = next((item for item in reversed(api_input) if item.get("role") == "user"), None)
            if latest_user_message:
                payload["input"] = [latest_user_message]
    else:
        if context.get("purpose") == "对话" and session_id:
            _clear_responses_continuation(session_id)
        endpoint = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
        payload = {
            "model": settings["model"],
            "messages": messages,
            "temperature": settings["temperature"],
            "max_tokens": settings["max_tokens"],
        }
    log_id = record_model_call(
        provider="openai-compatible", model=settings["model"],
        purpose=context.get("purpose", "对话"), request=payload,
        session_id=session_id,
    )
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(endpoint, json=payload, headers={"Authorization": f"Bearer {api_key}"})
            response.raise_for_status()
        data = response.json()
        if settings.get("api_protocol") == "responses":
            answer = _responses_output_text(data)
            if resumable_turn and session_id:
                _save_responses_continuation(session_id, settings, data)
        else:
            answer = data["choices"][0]["message"]["content"]
        finish_model_call(log_id, status="complete", response_preview=str(answer)[:2000], duration_ms=round((time.perf_counter() - started) * 1000))
        return answer
    except asyncio.CancelledError:
        finish_model_call(log_id, status="interrupted", error="浏览器连接已结束，本次回复停止生成。", duration_ms=round((time.perf_counter() - started) * 1000))
        raise
    except Exception as exc:
        error_text = str(exc).replace(api_key, "[已隐藏]") if api_key else str(exc)
        finish_model_call(log_id, status="failed", error=error_text[:1000], duration_ms=round((time.perf_counter() - started) * 1000))
        raise


def _responses_fingerprint(settings: dict) -> str:
    return f"{settings['base_url'].rstrip('/')}|{settings['model']}"


def _get_responses_continuation(session_id: str, settings: dict) -> tuple[str, str] | None:
    with connect() as db:
        rows = {
            row["state_key"]: row["state_value"]
            for row in db.execute(
                "SELECT state_key,state_value FROM provider_session_state "
                "WHERE session_id=? AND provider_id='openai-compatible' AND state_key IN (?,?,?)",
                (session_id, "responses_continuation_id", "responses_continuation_kind", "responses_fingerprint"),
            )
        }
    if rows.get("responses_fingerprint") != _responses_fingerprint(settings):
        return None
    continuation_id = rows.get("responses_continuation_id")
    kind = rows.get("responses_continuation_kind", "previous_response_id")
    if not continuation_id:
        return None
    return ("conversation" if kind == "conversation" else "previous_response_id", continuation_id)


def _clear_responses_continuation(session_id: str) -> None:
    with connect() as db:
        db.execute(
            "DELETE FROM provider_session_state WHERE session_id=? AND provider_id='openai-compatible' "
            "AND state_key LIKE 'responses_%'",
            (session_id,),
        )


def _save_responses_continuation(session_id: str, settings: dict, data: dict) -> None:
    conversation = data.get("conversation")
    if isinstance(conversation, dict):
        continuation_id = conversation.get("id")
    else:
        continuation_id = data.get("conversation_id") or (conversation if isinstance(conversation, str) else None)
    kind = "conversation" if isinstance(continuation_id, str) and continuation_id else "previous_response_id"
    if not continuation_id:
        continuation_id = data.get("id")
    if not isinstance(continuation_id, str) or not continuation_id:
        return
    fingerprint = _responses_fingerprint(settings)
    with connect() as db:
        for key, value in (
            ("responses_continuation_id", continuation_id),
            ("responses_continuation_kind", kind),
            ("responses_fingerprint", fingerprint),
        ):
            db.execute(
                "INSERT INTO provider_session_state(session_id,provider_id,state_key,state_value) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id,provider_id,state_key) DO UPDATE SET state_value=excluded.state_value",
                (session_id, "openai-compatible", key, value),
            )


def _responses_output_text(data: dict) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str):
        return output_text
    pieces = []
    for item in data.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    pieces.append(text)
    if not pieces:
        raise ValueError("Responses API 响应中没有可显示的文本。")
    return "".join(pieces)


async def stream_cli_chat(
    provider_id: str,
    session_id: str,
    session_origin: str,
    user_message: dict,
    content: str,
    prompt: str,
    prior_history: list[dict],
):
    provider = cli_providers.get(provider_id)
    if provider is None:
        yield f"data: {json.dumps({'type': 'error', 'message': '所选 CLI 插件尚未安装或注册。'}, ensure_ascii=False)}\n\n"
        return
    queue: asyncio.Queue[dict] = asyncio.Queue()
    import time

    started = time.perf_counter()
    log_id = record_model_call(
        provider=provider.display_name, model="由本机 CLI 管理", purpose="对话",
        session_id=session_id,
        request={
            "prompt": prompt,
            "prior_history": prior_history,
            "current_message": content,
            "note": "这是应用提供给 CLI 插件的上下文；插件可能自行管理额外线程上下文。",
        },
    )

    def save_exact_request(request: dict, model: str | None) -> None:
        update_model_call_request(log_id, request, model)

    async def send_delta(_delta: str) -> None:
        # The provider response is a structured envelope. Buffering prevents its
        # internal JSON from leaking into the user-visible chat stream.
        return None

    async def run_turn() -> None:
        try:
            def get_state(key: str) -> str | None:
                with connect() as db:
                    row = db.execute(
                        "SELECT state_value FROM provider_session_state WHERE session_id=? AND provider_id=? AND state_key=?",
                        (session_id, provider_id, key),
                    ).fetchone()
                return row["state_value"] if row else None

            def set_state(key: str, value: str) -> None:
                with connect() as db:
                    db.execute(
                        "INSERT INTO provider_session_state(session_id, provider_id, state_key, state_value) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(session_id, provider_id, state_key) DO UPDATE SET state_value=excluded.state_value",
                        (session_id, provider_id, key, value),
                    )

            context = ProviderTurnContext(
                session_id=session_id, content=content, prompt=prompt, prior_history=prior_history,
                data_dir=str(DATA_DIR), get_state=get_state, set_state=set_state,
                record_request=save_exact_request,
            )
            answer = await asyncio.wait_for(
                provider.run_turn(context, send_delta),
                timeout=180,
            )
            processed = process_agent_response(answer, content, session_id, user_message["id"])
            answer = processed["reply"]
            finish_model_call(log_id, status="complete", response_preview=answer[:2000], duration_ms=round((time.perf_counter() - started) * 1000))
            assistant_message = {
                "id": str(uuid.uuid4()), "session_id": session_id, "role": "assistant",
                "content": answer, "created_at": now(), "speaker": "assistant", "origin": session_origin,
            }
            with connect() as db:
                assistant_message["sequence"] = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM messages WHERE session_id=?", (session_id,)).fetchone()[0]
                db.execute(
                    "INSERT INTO messages(id,session_id,role,content,created_at,speaker,sequence) VALUES (:id,:session_id,:role,:content,:created_at,:speaker,:sequence)",
                    assistant_message,
                )
                index_message(db, assistant_message["id"], assistant_message["content"])
            await queue.put({
                "type": "delta", "content": answer,
            })
            await queue.put({
                "type": "done", "user_message": user_message,
                "assistant_message": assistant_message,
                "memory_suggestions": processed["memory_suggestions"],
            })
        except asyncio.CancelledError:
            finish_model_call(log_id, status="interrupted", error="浏览器连接已结束，本次回复停止生成。", duration_ms=round((time.perf_counter() - started) * 1000))
            raise
        except Exception as exc:
            finish_model_call(log_id, status="failed", error=str(exc)[:1000], duration_ms=round((time.perf_counter() - started) * 1000))
            message = str(exc) or f"{provider.display_name} 处理消息失败。"
            if isinstance(exc, asyncio.TimeoutError):
                message = f"等待 {provider.display_name} 超过 3 分钟，已结束本次请求。请检查登录状态和网络后重试。"
            await queue.put({"type": "error", "message": message})

    task = asyncio.create_task(run_turn())
    try:
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event["type"] in {"done", "error"}:
                break
    finally:
        # A browser reload or navigation closes this SSE generator, but it
        # must not cancel the already-started local model turn. The turn has
        # a bounded timeout and persists its final assistant message, so a
        # later session sync can recover it.
        if not task.done():
            _detached_cli_turns.add(task)
            task.add_done_callback(_detached_cli_turns.discard)


@app.post("/api/chat/memory-preview")
def preview_sensitive_memories(payload: MemoryPreviewInput) -> dict:
    """Return exact sensitive memory candidates for an in-the-moment local confirmation."""
    with connect() as db:
        session = db.execute("SELECT id, origin FROM sessions WHERE id=?", (payload.session_id,)).fetchone()
        if not session:
            raise HTTPException(404, "对话不存在")
        if session["origin"] not in {"native", "continued"}:
            raise HTTPException(403, "请先选择「继续此会话」再发送消息。")

    settings = read_settings()
    if not settings["use_memories_in_cloud"]:
        return {"memories": []}
    summary_record = get_session_summary(payload.session_id)
    with connect() as db:
        history = db.execute(
            "SELECT content FROM messages WHERE session_id=? AND sequence>? ORDER BY sequence DESC LIMIT 4",
            (payload.session_id, summary_record["through_sequence"]),
        ).fetchall()
    supplemental_context = " ".join(
        [summary_record.get("summary", "")]
        + [row["content"] for row in reversed(history)]
    )
    memory_query = f"{payload.content} {supplemental_context}".strip()
    candidates = search_relevant_memories(
        memory_query, limit=12, vector_search=settings["use_vector_memory_search"],
    )
    selected = select_memories_for_context(
        payload.content, candidates, supplemental_context=supplemental_context,
        limit=4, allow_sensitive=True,
    )
    return {
        "memories": [
            {"id": item["id"], "content": item["content"], "fingerprint": memory_fingerprint(item["content"])}
            for item in selected if item.get("sensitivity") == "sensitive"
        ]
    }


@app.post("/api/chat")
async def chat(payload: ChatInput) -> dict:
    content = payload.content.strip()
    remembered = memory_directive(content)
    needs_model = not remembered and content not in {"记住这个", "请记住这个"}
    settings = None
    api_key = None
    if needs_model:
        settings = read_settings()
        if settings["provider"] == "openai-compatible":
            try:
                api_key = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
            except Exception as exc:
                raise HTTPException(500, "无法读取系统凭据存储中的模型密钥。") from exc
            if not api_key:
                raise HTTPException(400, "请先在设置中保存模型 API Key。")
            parsed_url = urlparse(settings["base_url"])
            local_hosts = {"localhost", "127.0.0.1", "::1"}
            if parsed_url.scheme != "https" and not (parsed_url.scheme == "http" and parsed_url.hostname in local_hosts):
                raise HTTPException(400, "模型服务地址必须使用 HTTPS；本机服务可使用 localhost。")
        else:
            provider = cli_providers.get(settings["provider"])
            if provider is None:
                raise HTTPException(400, "所选 CLI 插件尚未安装或注册。")
            status = await asyncio.to_thread(provider.status)
            if not status.available or status.authenticated is False:
                raise HTTPException(400, status.message or f"{provider.display_name} 尚未准备就绪。")

    with connect() as db:
        session = db.execute("SELECT id, title, origin, mode FROM sessions WHERE id=?", (payload.session_id,)).fetchone()
        if not session:
            raise HTTPException(404, "对话不存在")
        if session["origin"] not in {"native", "continued"}:
            raise HTTPException(403, "请先选择「继续此会话」再发送消息。")
        user_message = {"id": str(uuid.uuid4()), "session_id": payload.session_id, "role": "user", "content": content, "created_at": now(), "speaker": "user", "origin": session["origin"]}
        user_message["sequence"] = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM messages WHERE session_id=?", (payload.session_id,)).fetchone()[0]
        db.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at,speaker,sequence) VALUES (:id,:session_id,:role,:content,:created_at,:speaker,:sequence)",
            user_message,
        )
        index_message(db, user_message["id"], user_message["content"])
        message_count = db.execute("SELECT COUNT(*) FROM messages WHERE session_id=?", (payload.session_id,)).fetchone()[0]
        title = content[:28] + ("…" if len(content) > 28 else "") if message_count == 1 else session["title"]
        db.execute("UPDATE sessions SET title=?, updated_at=? WHERE id=?", (title, now(), payload.session_id))

    memory_suggestions = []
    if remembered:
        create_memory(MemoryInput(content=remembered))
        answer = "记下来了。你可以在「记忆」里查看、修改或删除它。"
    elif content.strip() in {"记住这个", "请记住这个"}:
        answer = "可以。请把要保存的内容接在「记住这个：」后面发给我，我会按你的原话保存。"
    else:
        summary_record = get_session_summary(payload.session_id)
        with connect() as db:
            if settings["provider"] == "openai-compatible":
                history = db.execute(
                    "SELECT id, role, content, created_at, sequence FROM messages WHERE session_id=? AND sequence>? ORDER BY sequence",
                    (payload.session_id, summary_record["through_sequence"]),
                ).fetchall()
            else:
                history = db.execute(
                    "SELECT id, role, content, created_at, sequence FROM messages WHERE session_id=? AND sequence>? ORDER BY sequence DESC LIMIT 20",
                    (payload.session_id, summary_record["through_sequence"]),
                ).fetchall()
        stored_history = [dict(row) for row in reversed(history)] if settings["provider"] != "openai-compatible" else [dict(row) for row in history]
        supplemental_context = " ".join(
            [summary_record.get("summary", "")]
            + [item["content"] for item in stored_history[-5:-1]]
        )
        memory_query = f"{content} {supplemental_context}".strip()
        recalled_candidates = search_relevant_memories(
            memory_query, limit=12, vector_search=settings["use_vector_memory_search"],
        ) if settings["use_memories_in_cloud"] else []
        recalled = select_memories_for_context(
            content, recalled_candidates, supplemental_context=supplemental_context, limit=4,
            approved_sensitive=payload.sensitive_approvals,
        )
        archived = search_history_context(memory_query, payload.session_id, limit=5) if settings["use_archive_history"] else []
        if settings["use_archive_history"] and summary_record["through_sequence"] > 0:
            archived.extend(search_session_context(
                memory_query,
                payload.session_id,
                summary_record["through_sequence"],
                limit=5,
            ))
        base_prompt = (
            "固定边界：不要把推测当事实，不要假装记得没有提供的信息。尊重用户自主；提供陪伴和思路整理，不替代专业服务。\n\n"
            + MEMORY_RESPONSE_INSTRUCTIONS
        )
        if settings["provider"] == "openai-compatible":
            try:
                context = await prepare_direct_api_context(
                    settings=settings,
                    api_key=api_key,
                    session_id=payload.session_id,
                    current_message_id=user_message["id"],
                    all_messages=stored_history,
                    summary_record=summary_record,
                    session_origin=session["origin"],
                    base_prompt=base_prompt,
                    agent_definition=settings.get("agent_definition", ""),
                    mode=session["mode"],
                    mode_prompt=settings.get(f"{session['mode']}_mode_prompt"),
                    memories=recalled,
                    archived_messages=archived,
                    call_model=call_model,
                )
            except ContextBudgetExceeded as exc:
                raise HTTPException(413, str(exc)) from exc
        else:
            context = compose_context(
                base_prompt=base_prompt,
                current_message_id=user_message["id"],
                stored_history=stored_history,
                session_origin=session["origin"],
                agent_definition=settings.get("agent_definition", ""),
                mode=session["mode"],
                mode_prompt=settings.get(f"{session['mode']}_mode_prompt"),
                session_summary=summary_record.get("summary", ""),
                memories=recalled,
                archived_messages=archived,
            )
        if settings["provider"] != "openai-compatible":
            return StreamingResponse(
                stream_cli_chat(settings["provider"], payload.session_id, session["origin"], user_message, content, context.system_prompt, context.prior_history),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        try:
            with model_call_context("对话", payload.session_id):
                answer = await call_with_context_retry(settings, api_key, context, call_model)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(502, f"模型服务返回错误（HTTP {exc.response.status_code}）。请检查服务地址、模型名称和密钥。") from exc
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise HTTPException(502, "无法连接模型服务或响应格式不兼容；请检查模型设置。") from exc
        processed = process_agent_response(answer, content, payload.session_id, user_message["id"])
        answer = processed["reply"]
        memory_suggestions = processed["memory_suggestions"]

    assistant_message = {"id": str(uuid.uuid4()), "session_id": payload.session_id, "role": "assistant", "content": answer, "created_at": now(), "origin": session["origin"]}
    with connect() as db:
        assistant_message["speaker"] = "assistant"
        assistant_message["sequence"] = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM messages WHERE session_id=?", (payload.session_id,)).fetchone()[0]
        db.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at,speaker,sequence) VALUES (:id,:session_id,:role,:content,:created_at,:speaker,:sequence)",
            assistant_message,
        )
        index_message(db, assistant_message["id"], assistant_message["content"])
    return {"user_message": user_message, "assistant_message": assistant_message, "memory_suggestions": memory_suggestions}
