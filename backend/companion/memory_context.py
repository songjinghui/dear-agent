"""Filter and rank local memories before adding them to a model request."""

from datetime import datetime, timezone
import hashlib
import re

from .common import jieba


STOPWORDS = {
    "我", "你", "他", "她", "它", "的", "了", "是", "在", "有", "想", "去", "和", "吗", "呢",
    "什么", "怎么", "哪个", "如何", "能", "可以", "请", "帮", "觉得", "一下", "这个", "关于", "是否",
    "为什么", "推荐", "比较", "适合", "告诉", "之前", "现在", "一下", "一个", "那个", "怎么样", "刚才",
}


def _terms(text: str) -> set[str]:
    return {
        term.strip().lower()
        for term in jieba.cut(text)
        if re.fullmatch(r"[\w\u3400-\u9fff]+", term.strip())
        and term.strip().lower() not in STOPWORDS
        and (len(term.strip()) > 1 or not term.strip().isascii())
    }


def _timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def memory_fingerprint(content: str) -> str:
    """Bind a per-request approval to the exact memory text the user reviewed."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _is_eligible(
    memory: dict,
    now: datetime,
    allow_sensitive: bool = False,
    approved_sensitive: dict[str, str] | None = None,
) -> bool:
    sensitivity = memory.get("sensitivity", "normal")
    approved_fingerprint = (approved_sensitive or {}).get(str(memory.get("id", "")))
    approved_for_this_version = approved_fingerprint == memory_fingerprint(memory.get("content", ""))
    if sensitivity == "private" or (sensitivity == "sensitive" and not (allow_sensitive or approved_for_this_version)):
        return False
    if sensitivity not in {"normal", "sensitive", "private"}:
        return False
    if memory.get("confirmation", "confirmed") != "confirmed":
        return False
    expires_at = memory.get("expires_at")
    if not expires_at:
        return True
    expiry = _timestamp(expires_at)
    return expiry is not None and expiry > now


def select_memories_for_context(
    query: str,
    candidates: list[dict],
    *,
    supplemental_context: str = "",
    now_iso: str | None = None,
    limit: int = 4,
    minimum_relevance: float = 0.25,
    allow_sensitive: bool = False,
    approved_sensitive: dict[str, str] | None = None,
) -> list[dict]:
    query_terms = _terms(query)
    context_terms = _terms(supplemental_context)
    if (not query_terms and not context_terms) or limit <= 0:
        return []
    now = _timestamp(now_iso) if now_iso else datetime.now(timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    ranked = []
    for memory in candidates:
        if not _is_eligible(memory, now, allow_sensitive, approved_sensitive):
            continue
        memory_terms = _terms(memory.get("content", ""))
        query_relevance = len(query_terms & memory_terms) / len(query_terms) if query_terms else 0
        context_relevance = (len(context_terms & memory_terms) / len(context_terms)) * 0.8 if context_terms else 0
        vector_relevance = memory.get("_vector_similarity", 0) or 0
        relevance = max(query_relevance, context_relevance, vector_relevance)
        if relevance < minimum_relevance:
            continue
        ranked.append((relevance, memory))
    ranked.sort(
        key=lambda item: (
            item[0],
            int(item[1].get("importance", 0) or 0),
            item[1].get("updated_at", ""),
        ),
        reverse=True,
    )
    return [dict(memory) for _, memory in ranked[:limit]]
