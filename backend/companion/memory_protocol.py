"""Structured side-channel contract for proposing memories during chat."""

from __future__ import annotations

import json
import re


MEMORY_RESPONSE_INSTRUCTIONS = """
请以一个 JSON 对象返回本轮结果，格式为：
{"reply":"给用户看的正常回复","memory_candidates":[{"content":"简洁、可独立理解的记忆","memory_type":"working|episodic|semantic","importance":1,"sensitivity":"normal|sensitive|private","evidence":"从本轮用户原话逐字摘录的一段连续文本","rationale":"为什么这条内容值得记忆","expires_at":null}]}
要求：reply 是唯一给用户看的回答；memory_candidates 只放用户本轮明确表达且未来有复用价值的内容，最多 4 条。候选按彼此无关的持续事项划分，而不是按零散细节划分：同一趟旅行、同一个计划或同一段持续经历的时间、期待、进度和准备，应整合成一条主题记忆；content 可以用换行和短横线保留多项细节。只有互不相关的事项才拆成多条候选。没有合适候选时使用空数组。evidence 必须逐字来自本轮用户消息；不能根据助手的话、猜测、一次性情绪或一般常识创建用户记忆。把具体待办归为 working、具体经历归为 episodic、稳定偏好或事实归为 semantic。敏感内容如实标注 sensitivity；不确定时宁可不提候选。只输出 JSON，不要 Markdown。
""".strip() + "\n每个 memory_candidate 还必须包含 topic：不超过 120 字的简短事项主题，用来区分同一会话中的不同持续事项。"


def pending_memory_merge_instructions(suggestions: list[dict]) -> str:
    """Tell the model how to update a duplicate normal pending fact."""
    if not suggestions:
        return ""
    visible = [
        {"id": item["id"], "topic": item.get("topic", ""), "content": item["content"], "memory_type": item["memory_type"]}
        for item in suggestions
    ]
    return (
        "\n\n以下是本会话里仍待用户整理的普通记忆候选。若本轮是在重复、修正或补充其中同一持续事项（例如同一趟旅行、同一个计划），"
        "请将时间、期待、进度等细节整合为一条主题记忆，并在该候选加入 merge_with，值必须是对应候选的 id；"
        "不要另建重复候选。整合后的 content 应保留各项细节，可用换行和短横线组织。"
        "只有与既有候选无关的事项才不要设置 merge_with。"
        "不要合并敏感或严格私密信息。\n"
        f"待整理候选：{json.dumps(visible, ensure_ascii=False)}"
    )


def _decode_object(raw: str) -> dict | None:
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _partial_reply(raw: str) -> str | None:
    """Recover a completed reply string when a trailing candidate array was cut off."""
    match = re.search(r'["\']reply["\']\s*:\s*', raw)
    if not match:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(raw[match.end():].lstrip())
    except (json.JSONDecodeError, TypeError):
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def parse_agent_response(raw: str, current_user_message: str) -> dict:
    """Parse the side channel; fail open for chat and fail closed for memory."""
    fallback = {"reply": raw, "memory_candidates": []}
    payload = _decode_object(raw)
    if not payload:
        reply = _partial_reply(raw)
        return {"reply": reply, "memory_candidates": []} if reply else fallback
    if not isinstance(payload.get("reply"), str) or not payload["reply"].strip():
        return fallback

    candidates = []
    raw_candidates = payload.get("memory_candidates", [])
    if not isinstance(raw_candidates, list):
        raw_candidates = []
    for candidate in raw_candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        evidence = candidate.get("evidence")
        memory_type = candidate.get("memory_type")
        sensitivity = candidate.get("sensitivity")
        if not isinstance(content, str) or not content.strip() or len(content) > 5000:
            continue
        if not isinstance(evidence, str) or not evidence.strip() or evidence not in current_user_message:
            continue
        if memory_type not in {"working", "episodic", "semantic"}:
            continue
        if sensitivity not in {"normal", "sensitive", "private"}:
            continue
        importance = candidate.get("importance", 3)
        if isinstance(importance, bool) or not isinstance(importance, int) or not 1 <= importance <= 5:
            importance = 3
        expires_at = candidate.get("expires_at")
        if not isinstance(expires_at, str) or not expires_at.strip():
            expires_at = None
        topic = candidate.get("topic", "")
        if not isinstance(topic, str):
            topic = ""
        topic = topic.strip()
        if len(topic) > 120:
            topic = ""
        candidates.append({
            "topic": topic,
            "content": content.strip(),
            "memory_type": memory_type,
            "importance": importance,
            "sensitivity": sensitivity,
            "evidence": evidence,
            "rationale": str(candidate.get("rationale", ""))[:500],
            "expires_at": expires_at,
            "merge_with": candidate.get("merge_with")[:128] if isinstance(candidate.get("merge_with"), str) else None,
        })
        if len(candidates) == 4:
            break
    return {"reply": payload["reply"].strip(), "memory_candidates": candidates}
