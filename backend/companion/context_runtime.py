"""Prepare direct-API context and compact older turns when needed."""

from collections.abc import Awaitable, Callable

from .context_builder import ComposedContext, compose_context
from .context_budget import (
    estimate_message_tokens,
    input_token_budget,
    messages_after_sequence,
    should_compact,
    split_history_for_compaction,
    trim_context_to_budget,
)
from .context_compactor import is_context_length_error, summary_request
from .context_store import save_session_summary
from .model_call_logs import model_call_context


class ContextBudgetExceeded(ValueError):
    pass


async def call_with_context_retry(settings: dict, api_key: str, context: ComposedContext, call_model):
    try:
        return await call_model(settings, api_key, context.api_messages)
    except Exception as error:
        response = getattr(error, "response", None)
        if response is None:
            raise
        try:
            body = response.json()
        except Exception:
            body = {}
        if not is_context_length_error(response.status_code, body):
            raise
        current_only = ComposedContext(context.system_prompt, [], context.current_message)
        minimal_budget = estimate_message_tokens(current_only.api_messages)
        reduced = trim_context_to_budget(context, minimal_budget)
        return await call_model(settings, api_key, reduced.api_messages)


async def prepare_direct_api_context(
    *,
    settings: dict,
    api_key: str,
    session_id: str,
    current_message_id: str,
    all_messages: list[dict],
    summary_record: dict,
    session_origin: str,
    base_prompt: str,
    agent_definition: str,
    memories: list[dict],
    archived_messages: list[dict],
    call_model: Callable[[dict, str, list[dict]], Awaitable[str]],
    mode: str = "chat",
    mode_prompt: str | None = None,
) -> ComposedContext:
    summary = summary_record.get("summary", "")
    through_sequence = int(summary_record.get("through_sequence", 0))
    pending = messages_after_sequence(all_messages, through_sequence)
    context = _compose(
        base_prompt, current_message_id, pending, session_origin, agent_definition, mode, mode_prompt,
        summary, memories, archived_messages,
    )
    budget = input_token_budget(
        context_window=int(settings.get("context_window_tokens", 8192)),
        max_output_tokens=int(settings.get("max_tokens", 1200)),
    )
    if should_compact(estimate_message_tokens(context.api_messages), budget):
        older, recent = split_history_for_compaction(pending, keep_recent_messages=8)
        if older:
            try:
                compact_settings = {
                    **settings,
                    "temperature": 0.2,
                    "max_tokens": min(
                        int(settings.get("max_tokens", 1200)),
                        max(64, int(settings.get("max_tokens", 1200)) // 2),
                        256,
                    ),
                }
                with model_call_context("会话压缩摘要", session_id):
                    updated_summary = await call_model(compact_settings, api_key, summary_request(summary, older))
                if updated_summary.strip():
                    if summary.strip() and summary.strip() not in updated_summary:
                        # A compression model can focus on the newest source
                        # messages and omit an older fact. Retain the prior
                        # summary verbatim rather than silently forgetting it.
                        updated_summary = f"{summary.strip()}\n{updated_summary.strip()}"
                    summary = updated_summary.strip()
                    through_sequence = max(int(item.get("sequence", 0)) for item in older)
                    try:
                        save_session_summary(session_id, summary, through_sequence)
                    except Exception:
                        pass
            except Exception:
                # A failed compaction never blocks the turn; old raw messages remain local.
                pass
        else:
            recent = pending
        def compose_and_trim(summary_for_context: str) -> ComposedContext:
            compacted = _compose(
                base_prompt, current_message_id, recent, session_origin, agent_definition, mode, mode_prompt,
                summary_for_context, memories, archived_messages,
            )
            return trim_context_to_budget(compacted, budget)

        context = compose_and_trim(summary)
        if estimate_message_tokens(context.api_messages) > budget and summary:
            # The saved summary is useful, but it must not make the next user
            # turn impossible to send. Keep the longest leading portion that
            # fits alongside the fixed prompt and current message; the full
            # summary remains persisted for later turns with a larger budget.
            lower, upper = 0, len(summary)
            best_summary: str | None = None
            while lower <= upper:
                midpoint = (lower + upper) // 2
                candidate = summary[:midpoint].rstrip()
                candidate_context = compose_and_trim(candidate)
                if estimate_message_tokens(candidate_context.api_messages) <= budget:
                    best_summary = candidate
                    lower = midpoint + 1
                else:
                    upper = midpoint - 1
            if best_summary is not None:
                context = compose_and_trim(best_summary)
    if estimate_message_tokens(context.api_messages) > budget:
        raise ContextBudgetExceeded("当前消息和固定上下文已超过模型可用输入空间，请缩短或分段发送。")
    return context


def _compose(
    base_prompt: str,
    current_message_id: str,
    messages: list[dict],
    session_origin: str,
    agent_definition: str,
    mode: str,
    mode_prompt: str | None,
    summary: str,
    memories: list[dict],
    archived_messages: list[dict],
) -> ComposedContext:
    return compose_context(
        base_prompt=base_prompt,
        current_message_id=current_message_id,
        stored_history=messages,
        session_origin=session_origin,
        agent_definition=agent_definition,
        mode=mode,
        mode_prompt=mode_prompt,
        session_summary=summary,
        memories=memories,
        archived_messages=archived_messages,
    )
