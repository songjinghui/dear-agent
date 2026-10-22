"""Conservative, provider-neutral context budgeting helpers."""

from math import ceil
from dataclasses import replace


def estimate_message_tokens(messages: list[dict]) -> int:
    total = 0
    for message in messages:
        content = str(message.get("content", ""))
        total += ceil(len(content.encode("utf-8")) / 2) + 4
    return total


def input_token_budget(
    *,
    context_window: int,
    max_output_tokens: int,
    safety_ratio: float = 0.1,
) -> int:
    if context_window <= 0 or max_output_tokens < 0 or not 0 <= safety_ratio < 1:
        raise ValueError("上下文窗口、输出预留或安全比例无效。")
    margin = max(256, ceil(context_window * safety_ratio))
    return max(0, context_window - max_output_tokens - margin)


def should_compact(estimated_tokens: int, input_budget: int, *, threshold: float = 0.75) -> bool:
    if not 0 < threshold < 1:
        raise ValueError("压缩触发比例必须大于 0 且小于 1。")
    return input_budget <= 0 or estimated_tokens > input_budget * threshold


def messages_after_sequence(messages: list[dict], through_sequence: int) -> list[dict]:
    return [dict(message) for message in messages if int(message.get("sequence", 0)) > through_sequence]


def split_history_for_compaction(
    messages: list[dict], *, keep_recent_messages: int = 8,
) -> tuple[list[dict], list[dict]]:
    if keep_recent_messages < 1:
        raise ValueError("压缩时至少要保留一条近期消息。")
    split_at = max(0, len(messages) - keep_recent_messages)
    return ([dict(message) for message in messages[:split_at]],
            [dict(message) for message in messages[split_at:]])


def trim_context_to_budget(context, input_budget: int):
    prior_history = list(context.prior_history)
    trimmed = replace(context, prior_history=prior_history)
    while prior_history and estimate_message_tokens(trimmed.api_messages) > input_budget:
        prior_history.pop(0)
        trimmed = replace(context, prior_history=list(prior_history))
    return trimmed
