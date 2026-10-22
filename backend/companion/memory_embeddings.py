"""Local Ollama embeddings used to broaden memory recall beyond exact keywords."""

from __future__ import annotations

import hashlib
import json
import math
from urllib.parse import urlparse

import httpx

from .common import connect


MIN_VECTOR_SIMILARITY = 0.35


def is_local_embedding_url(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and parsed.path.rstrip("/") == "/api/embed"
        and not parsed.username
        and not parsed.password
    )


def embed_texts(texts: list[str], model: str, endpoint: str) -> list[list[float]]:
    """Create embeddings through a local Ollama endpoint; never call a cloud host."""
    if not texts:
        return []
    if not is_local_embedding_url(endpoint):
        raise ValueError("记忆嵌入服务必须是本机 Ollama /api/embed 地址。")
    response = httpx.post(endpoint, json={"model": model, "input": texts}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    embeddings = payload.get("embeddings")
    if not isinstance(embeddings, list) or len(embeddings) != len(texts):
        raise ValueError("本机嵌入服务返回的向量数量不匹配。")
    vectors: list[list[float]] = []
    for vector in embeddings:
        if not isinstance(vector, list) or not vector:
            raise ValueError("本机嵌入服务返回了空向量。")
        vectors.append([float(value) for value in vector])
    return vectors


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return -1.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return -1.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def search_vector_memories(query: str, limit: int) -> list[dict]:
    """Return local vector matches, caching memory vectors by model and content hash."""
    with connect() as db:
        settings = {row["key"]: row["value"] for row in db.execute(
            "SELECT key,value FROM settings WHERE key IN "
            "('use_vector_memory_search','memory_embedding_model','memory_embedding_url')"
        )}
        if settings.get("use_vector_memory_search") != "true":
            return []
        model = settings.get("memory_embedding_model", "").strip()
        endpoint = settings.get("memory_embedding_url", "http://127.0.0.1:11434/api/embed")
        if not model or not is_local_embedding_url(endpoint):
            return []
        memories = [dict(row) for row in db.execute("SELECT * FROM memories WHERE sensitivity!='private'")]
        if not memories:
            return []
        cached = {
            row["memory_id"]: row
            for row in db.execute("SELECT memory_id,model,content_hash,vector_json FROM memory_embeddings")
        }

    query_vector = embed_texts([query], model, endpoint)[0]
    missing: list[dict] = []
    vectors: dict[str, list[float]] = {}
    for memory in memories:
        content_hash = hashlib.sha256(memory["content"].encode("utf-8")).hexdigest()
        entry = cached.get(memory["id"])
        if entry and entry["model"] == model and entry["content_hash"] == content_hash:
            vectors[memory["id"]] = json.loads(entry["vector_json"])
        else:
            memory["_content_hash"] = content_hash
            missing.append(memory)

    if missing:
        generated = embed_texts([memory["content"] for memory in missing], model, endpoint)
        with connect() as db:
            for memory, vector in zip(missing, generated):
                vectors[memory["id"]] = vector
                db.execute(
                    "INSERT INTO memory_embeddings(memory_id,model,content_hash,vector_json) VALUES (?,?,?,?) "
                    "ON CONFLICT(memory_id) DO UPDATE SET model=excluded.model,content_hash=excluded.content_hash,vector_json=excluded.vector_json",
                    (memory["id"], model, memory["_content_hash"], json.dumps(vector)),
                )

    ranked = []
    for memory in memories:
        similarity = _cosine(query_vector, vectors[memory["id"]])
        if similarity >= MIN_VECTOR_SIMILARITY:
            memory["_vector_similarity"] = similarity
            ranked.append((similarity, memory))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [memory for _, memory in ranked[:max(0, limit)]]
