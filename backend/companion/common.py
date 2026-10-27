"""Shared application state, configuration, and SQLite setup."""

from __future__ import annotations

import json
import hashlib
import os
import re
import asyncio
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

import httpx
import jieba
import keyring
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .providers import CLIProviderRegistry, ProviderTurnContext
from .providers.codex import CodexProvider
from .mode_prompts import DEFAULT_MODE_PROMPTS
from .archive_format import (
    ArchiveFormatError, FORMAT_NAME, FORMAT_VERSION, MAX_PART_BYTES, MAX_IMAGE_BYTES,
    MAX_IMAGES_PER_PART, IMAGE_MIME_BY_SUFFIX, image_mime_type, validate_archive, iter_records,
)


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("COMPANION_DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "companion.sqlite3"
FRONTEND_DIST = ROOT / "frontend" / "dist"
KEYRING_SERVICE = "personal-companion-agent"
KEYRING_USER = "model-api-key"
cli_providers = CLIProviderRegistry()
cli_providers.register(CodexProvider())

app = FastAPI(title="个人生活 Agent", version="0.1.0")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def hydrate_message_attachments(rows: list[sqlite3.Row | dict]) -> list[dict]:
    messages = [dict(row) for row in rows]
    message_ids = [message["id"] for message in messages]
    if not message_ids:
        return messages
    placeholders = ",".join("?" for _ in message_ids)
    with connect() as db:
        attachment_rows = db.execute(
            f"SELECT id,message_id,filename,mime_type,byte_size FROM archive_attachments WHERE message_id IN ({placeholders}) ORDER BY rowid",
            message_ids,
        ).fetchall()
    by_message: dict[str, list[dict]] = {}
    for row in attachment_rows:
        by_message.setdefault(row["message_id"], []).append({
            "id": row["id"], "filename": row["filename"], "mime_type": row["mime_type"],
            "byte_size": row["byte_size"], "url": f"/api/attachments/{row['id']}",
        })
    for message in messages:
        message["attachments"] = by_message.get(message["id"], [])
    return messages


def initialize() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'chat',
                origin TEXT NOT NULL DEFAULT 'native',
                source_platform TEXT,
                import_id TEXT,
                source_conversation_id TEXT
            );
            CREATE TABLE IF NOT EXISTS codex_threads (
                session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
                thread_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS provider_session_state (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                provider_id TEXT NOT NULL,
                state_key TEXT NOT NULL,
                state_value TEXT NOT NULL,
                PRIMARY KEY(session_id, provider_id, state_key)
            );
            CREATE TABLE IF NOT EXISTS model_call_logs (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                provider TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                purpose TEXT NOT NULL,
                request_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'processing',
                response_preview TEXT,
                error TEXT,
                duration_ms INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS model_call_logs_created ON model_call_logs(created_at DESC);
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                speaker TEXT NOT NULL DEFAULT '',
                speaker_label TEXT,
                sequence INTEGER NOT NULL DEFAULT 0,
                source_message_id TEXT,
                source_timestamp TEXT
            );
            CREATE INDEX IF NOT EXISTS messages_session_time
                ON messages(session_id, created_at);
            CREATE TABLE IF NOT EXISTS session_context_summaries (
                session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
                summary TEXT NOT NULL,
                through_sequence INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                importance INTEGER NOT NULL DEFAULT 3,
                source TEXT NOT NULL DEFAULT '用户明确要求记录',
                confirmation TEXT NOT NULL DEFAULT 'confirmed',
                sensitivity TEXT NOT NULL DEFAULT 'normal',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT,
                source_refs TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS memory_embeddings (
                memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
                model TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                vector_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memory_suggestions (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                content TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                importance INTEGER NOT NULL DEFAULT 3,
                sensitivity TEXT NOT NULL DEFAULT 'normal',
                evidence TEXT NOT NULL,
                rationale TEXT NOT NULL DEFAULT '',
                expires_at TEXT,
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','dismissed')),
                memory_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(message_id, content)
            );
            CREATE TABLE IF NOT EXISTS archive_imports (
                id TEXT PRIMARY KEY,
                source_platform TEXT NOT NULL DEFAULT '',
                expected_parts INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS archive_import_parts (
                archive_id TEXT NOT NULL REFERENCES archive_imports(id) ON DELETE CASCADE,
                part_id TEXT NOT NULL,
                part_index INTEGER NOT NULL,
                checksum TEXT NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                summary_count INTEGER NOT NULL DEFAULT 0,
                attachment_count INTEGER NOT NULL DEFAULT 0,
                imported_at TEXT NOT NULL,
                PRIMARY KEY (archive_id, part_id),
                UNIQUE (archive_id, part_index)
            );
            CREATE TABLE IF NOT EXISTS archive_attachments (
                id TEXT PRIMARY KEY,
                archive_id TEXT NOT NULL REFERENCES archive_imports(id) ON DELETE CASCADE,
                message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                source_attachment_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                storage_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (archive_id, source_attachment_id)
            );
            CREATE INDEX IF NOT EXISTS archive_attachments_message ON archive_attachments(message_id);
            CREATE TABLE IF NOT EXISTS archive_summaries (
                id TEXT PRIMARY KEY,
                archive_id TEXT NOT NULL REFERENCES archive_imports(id) ON DELETE CASCADE,
                source_summary_id TEXT NOT NULL,
                conversation_id TEXT,
                content TEXT NOT NULL,
                based_on_message_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                UNIQUE (archive_id, source_summary_id)
            );
            CREATE TABLE IF NOT EXISTS archive_analysis_jobs (
                id TEXT PRIMARY KEY,
                archive_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                progress REAL NOT NULL DEFAULT 0,
                processed_chars INTEGER NOT NULL DEFAULT 0,
                total_chars INTEGER NOT NULL DEFAULT 0,
                checkpoint_sequence INTEGER NOT NULL DEFAULT 0,
                checkpoint_text_part INTEGER NOT NULL DEFAULT 0,
                supplemented_through_sequence INTEGER NOT NULL DEFAULT 0,
                automatic_retries INTEGER NOT NULL DEFAULT 0,
                retry_after TEXT,
                summary TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS archive_analysis_session ON archive_analysis_jobs(session_id,created_at DESC);
            CREATE TABLE IF NOT EXISTS archive_memory_candidates (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES archive_analysis_jobs(id) ON DELETE CASCADE,
                archive_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                content TEXT NOT NULL,
                topic TEXT NOT NULL DEFAULT '',
                memory_type TEXT NOT NULL,
                evidence_type TEXT NOT NULL,
                importance INTEGER NOT NULL DEFAULT 3,
                sensitivity TEXT NOT NULL DEFAULT 'normal',
                rationale TEXT NOT NULL DEFAULT '',
                source_refs TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'pending',
                memory_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS archive_candidates_job_status ON archive_memory_candidates(job_id,status,created_at);
            CREATE TABLE IF NOT EXISTS archive_memory_candidate_organizations (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL UNIQUE REFERENCES archive_analysis_jobs(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'queued',
                groups_json TEXT NOT NULL DEFAULT '[]',
                automatic_retries INTEGER NOT NULL DEFAULT 0,
                retry_after TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        memory_columns = {row["name"] for row in db.execute("PRAGMA table_info(memories)")}
        if "source_refs" not in memory_columns:
            db.execute("ALTER TABLE memories ADD COLUMN source_refs TEXT NOT NULL DEFAULT '[]'")
        session_columns = {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}
        for name, definition in {
            "origin": "TEXT NOT NULL DEFAULT 'native'",
            "source_platform": "TEXT",
            "import_id": "TEXT",
            "source_conversation_id": "TEXT",
        }.items():
            if name not in session_columns:
                db.execute(f"ALTER TABLE sessions ADD COLUMN {name} {definition}")
        message_columns = {row["name"] for row in db.execute("PRAGMA table_info(messages)")}
        for name, definition in {
            "speaker": "TEXT NOT NULL DEFAULT ''",
            "speaker_label": "TEXT",
            "sequence": "INTEGER NOT NULL DEFAULT 0",
            "source_message_id": "TEXT",
            "source_timestamp": "TEXT",
        }.items():
            if name not in message_columns:
                db.execute(f"ALTER TABLE messages ADD COLUMN {name} {definition}")
        db.execute("UPDATE messages SET speaker=role WHERE speaker='' OR speaker IS NULL")
        for session_row in db.execute("SELECT id FROM sessions WHERE origin='native'").fetchall():
            next_sequence = db.execute("SELECT COALESCE(MAX(sequence),0) FROM messages WHERE session_id=?", (session_row["id"],)).fetchone()[0]
            old_rows = db.execute("SELECT id FROM messages WHERE session_id=? AND sequence=0 ORDER BY created_at,rowid", (session_row["id"],)).fetchall()
            for old_row in old_rows:
                next_sequence += 1
                db.execute("UPDATE messages SET sequence=? WHERE id=?", (next_sequence, old_row["id"]))
        db.execute("CREATE INDEX IF NOT EXISTS sessions_origin_updated ON sessions(origin, updated_at DESC)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS messages_session_sequence ON messages(session_id, sequence) WHERE sequence > 0")
        codex_thread_columns = {row["name"] for row in db.execute("PRAGMA table_info(codex_threads)")}
        if "model" not in codex_thread_columns:
            db.execute("ALTER TABLE codex_threads ADD COLUMN model TEXT NOT NULL DEFAULT ''")
        db.execute(
            "INSERT OR IGNORE INTO provider_session_state(session_id, provider_id, state_key, state_value) "
            "SELECT c.session_id, 'codex-app-server', 'thread_id', c.thread_id FROM codex_threads c "
            "WHERE EXISTS (SELECT 1 FROM sessions s WHERE s.id=c.session_id)"
        )
        db.execute(
            "INSERT OR IGNORE INTO provider_session_state(session_id, provider_id, state_key, state_value) "
            "SELECT c.session_id, 'codex-app-server', 'model', c.model FROM codex_threads c "
            "WHERE c.model != '' AND EXISTS (SELECT 1 FROM sessions s WHERE s.id=c.session_id)"
        )
        try:
            db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts "
                "USING fts5(memory_id UNINDEXED, content, tokenize='unicode61')"
            )
        except sqlite3.OperationalError:
            # Some system SQLite builds omit FTS5. LIKE search remains available.
            pass
        try:
            db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts "
                "USING fts5(message_id UNINDEXED, content, tokenize='unicode61')"
            )
            missing_messages = db.execute(
                "SELECT id,content FROM messages WHERE id NOT IN (SELECT message_id FROM messages_fts)"
            ).fetchall()
            db.executemany(
                "INSERT INTO messages_fts(message_id,content) VALUES (?,?)",
                [(row["id"], " ".join(jieba.cut(row["content"]))) for row in missing_messages],
            )
        except sqlite3.OperationalError:
            pass
        archive_part_columns = {row["name"] for row in db.execute("PRAGMA table_info(archive_import_parts)")}
        session_columns = {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}
        if "mode" not in session_columns:
            db.execute("ALTER TABLE sessions ADD COLUMN mode TEXT NOT NULL DEFAULT 'chat'")
        if "attachment_count" not in archive_part_columns:
            db.execute("ALTER TABLE archive_import_parts ADD COLUMN attachment_count INTEGER NOT NULL DEFAULT 0")
        analysis_job_columns = {row["name"] for row in db.execute("PRAGMA table_info(archive_analysis_jobs)")}
        for name in ("checkpoint_sequence", "checkpoint_text_part", "supplemented_through_sequence", "automatic_retries"):
            if name not in analysis_job_columns:
                db.execute(f"ALTER TABLE archive_analysis_jobs ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")
        if "retry_after" not in analysis_job_columns:
            db.execute("ALTER TABLE archive_analysis_jobs ADD COLUMN retry_after TEXT")
        organization_columns = {row["name"] for row in db.execute("PRAGMA table_info(archive_memory_candidate_organizations)")}
        if "automatic_retries" not in organization_columns:
            db.execute("ALTER TABLE archive_memory_candidate_organizations ADD COLUMN automatic_retries INTEGER NOT NULL DEFAULT 0")
        if "retry_after" not in organization_columns:
            db.execute("ALTER TABLE archive_memory_candidate_organizations ADD COLUMN retry_after TEXT")
        candidate_columns = {row["name"] for row in db.execute("PRAGMA table_info(archive_memory_candidates)")}
        if "topic" not in candidate_columns:
            db.execute("ALTER TABLE archive_memory_candidates ADD COLUMN topic TEXT NOT NULL DEFAULT ''")
        for key, value in {
            "provider": "openai-compatible",
            "api_protocol": "chat_completions",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "temperature": "0.7",
            "max_tokens": "1200",
            "use_memories_in_cloud": "false",
            "use_vector_memory_search": "false",
            "memory_embedding_url": "http://127.0.0.1:11434/api/embed",
            "memory_embedding_model": "",
            "use_archive_history": "false",
            "agent_definition": "你是一个温暖、坦诚、尊重边界的个人生活陪伴助手。先理解用户当下需要倾听、建议还是分析。",
            "default_mode": "chat",
            "chat_mode_prompt": DEFAULT_MODE_PROMPTS["chat"],
            "manager_mode_prompt": DEFAULT_MODE_PROMPTS["manager"],
            "context_window_tokens": "8192",
        }.items():
            db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))
        # Sensitive memory is authorized per request; remove the retired global toggle.
        db.execute("DELETE FROM settings WHERE key='use_sensitive_memories_in_cloud'")


@app.on_event("startup")
def on_startup() -> None:
    initialize()
    cli_providers.discover()


def read_settings() -> dict:
    with connect() as db:
        values = {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM settings")}
    providers = cli_providers.list()
    codex_status = next((provider for provider in providers if provider["id"] == "codex-app-server"), {})
    return {
        **values,
        "temperature": float(values.get("temperature", "0.7")),
        "max_tokens": int(values.get("max_tokens", "1200")),
        "context_window_tokens": int(values.get("context_window_tokens", "8192")),
        "use_memories_in_cloud": values.get("use_memories_in_cloud", "false") == "true",
        "use_vector_memory_search": values.get("use_vector_memory_search", "false") == "true",
        "use_archive_history": values.get("use_archive_history", "false") == "true",
        "default_mode": values.get("default_mode", "chat") if values.get("default_mode") in {"chat", "manager"} else "chat",
        "api_protocol": values.get("api_protocol", "chat_completions") if values.get("api_protocol") in {"chat_completions", "responses"} else "chat_completions",
        "has_api_key": bool(keyring.get_password(KEYRING_SERVICE, KEYRING_USER)),
        "codex_available": codex_status.get("available", False),
        "has_codex_login": codex_status.get("authenticated", False),
        "providers": providers,
    }
