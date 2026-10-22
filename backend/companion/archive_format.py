"""Validation helpers for the Personal Conversation Archive JSONL format."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Iterator


FORMAT_NAME = "personal-conversation-archive"
FORMAT_VERSION = "1.1"
SUPPORTED_FORMAT_VERSIONS = {"1.0", FORMAT_VERSION}
MAX_PART_BYTES = 100 * 1024 * 1024
MAX_LINE_BYTES = 5 * 1024 * 1024
MAX_MESSAGES_PER_PART = 100_000
MAX_IMAGES_PER_PART = 10_000
MAX_IMAGE_BYTES = 20 * 1024 * 1024
SPEAKERS = {"user", "assistant", "system", "tool", "unknown"}
IMAGE_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
}


class ArchiveFormatError(ValueError):
    def __init__(self, message: str, line: int | None = None):
        self.line = line
        super().__init__(f"第 {line} 行：{message}" if line is not None else message)


@dataclass
class ParsedArchive:
    header: dict[str, Any]
    conversations: dict[str, dict[str, Any]]
    message_ids: set[str]
    message_sequences: set[tuple[str, int]]
    summary_ids: set[str]
    message_count: int
    summary_count: int
    message_counts_by_conversation: dict[str, int]
    attachments: list[dict[str, Any]]
    attachment_counts_by_conversation: dict[str, int]
    attachment_bytes_by_conversation: dict[str, int]


def iter_records(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    try:
        with path.open("rb") as file:
            line_number = 0
            while raw_line := file.readline(MAX_LINE_BYTES + 1):
                line_number += 1
                if len(raw_line) > MAX_LINE_BYTES:
                    raise ArchiveFormatError("单条记录超过 5 MiB。", line_number)
                if not raw_line.strip():
                    continue
                try:
                    # Accept a UTF-8 BOM at the beginning of the first non-empty
                    # record, even when a file starts with blank lines.
                    record = json.loads(raw_line.decode("utf-8-sig"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ArchiveFormatError("必须是 UTF-8 编码的有效 JSONL。", line_number) from exc
                if not isinstance(record, dict):
                    raise ArchiveFormatError("每行必须是 JSON 对象。", line_number)
                yield line_number, record
    except OSError as exc:
        raise ArchiveFormatError("无法读取导入文件。") from exc


def _required_text(record: dict[str, Any], key: str, line: int, max_length: int = 500) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ArchiveFormatError(f"字段 {key} 必须是非空字符串。", line)
    if len(value) > max_length:
        raise ArchiveFormatError(f"字段 {key} 超过 {max_length} 个字符。", line)
    return value


def image_mime_type(path: Path) -> str | None:
    try:
        with path.open("rb") as file:
            signature = file.read(16)
    except OSError:
        return None
    if signature.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if signature.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if signature.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(signature) >= 12 and signature[:4] == b"RIFF" and signature[8:12] == b"WEBP":
        return "image/webp"
    return None


def _attachment_path(value: object, line: int) -> PurePosixPath:
    if not isinstance(value, str) or len(value) > 1000 or "\\" in value:
        raise ArchiveFormatError("图片附件 path 必须是 media/ 下的相对路径。", line)
    relative = PurePosixPath(value)
    if relative.is_absolute() or len(relative.parts) < 2 or relative.parts[0] != "media" or any(part in {"", ".", ".."} for part in relative.parts):
        raise ArchiveFormatError("图片附件 path 必须是 media/ 下的安全相对路径。", line)
    if relative.suffix.lower() not in IMAGE_MIME_BY_SUFFIX:
        raise ArchiveFormatError("图片仅支持 JPEG、PNG、GIF 和 WebP。", line)
    return relative


def validate_archive(path: Path, media_root: Path | None = None) -> ParsedArchive:
    if path.stat().st_size > MAX_PART_BYTES:
        raise ArchiveFormatError("单个 JSONL 分片不能超过 100 MiB。")

    header: dict[str, Any] | None = None
    conversations: dict[str, dict[str, Any]] = {}
    message_ids: set[str] = set()
    message_sequences: set[tuple[str, int]] = set()
    summary_ids: set[str] = set()
    message_count = 0
    summary_count = 0
    attachments: list[dict[str, Any]] = []
    attachment_ids: set[str] = set()
    attachment_counts_by_conversation: dict[str, int] = {}
    attachment_bytes_by_conversation: dict[str, int] = {}
    message_counts_by_conversation: dict[str, int] = {}
    for line, record in iter_records(path):
        record_type = record.get("type")
        if header is None:
            if record_type != "archive":
                raise ArchiveFormatError("首条记录必须是 archive 格式头。", line)
            if record.get("format") != FORMAT_NAME:
                raise ArchiveFormatError(f"不支持的格式名称；应为 {FORMAT_NAME}。", line)
            if record.get("version") not in SUPPORTED_FORMAT_VERSIONS:
                raise ArchiveFormatError(f"不支持的格式版本；当前支持 {', '.join(sorted(SUPPORTED_FORMAT_VERSIONS))}。", line)
            archive_id = _required_text(record, "archive_id", line, 128)
            part_id = _required_text(record, "part_id", line, 128)
            source_platform = record.get("source_platform")
            if source_platform is not None and (not isinstance(source_platform, str) or len(source_platform) > 100):
                raise ArchiveFormatError("source_platform 必须是最多 100 个字符的字符串或 null。", line)
            part_index = record.get("part_index")
            part_count = record.get("part_count")
            if not isinstance(part_index, int) or isinstance(part_index, bool) or not isinstance(part_count, int) or isinstance(part_count, bool) or part_index < 1 or part_count < 1 or part_index > part_count:
                raise ArchiveFormatError("part_index/part_count 必须是有效的从 1 开始的分片序号。", line)
            header = {**record, "archive_id": archive_id, "part_id": part_id, "part_index": part_index, "part_count": part_count}
            continue

        if record_type == "archive":
            raise ArchiveFormatError("每个分片只能有一条 archive 格式头。", line)
        if record_type == "conversation":
            conversation_id = _required_text(record, "id", line, 256)
            title = record.get("title", "未命名会话")
            if not isinstance(title, str) or len(title) > 500:
                raise ArchiveFormatError("title 必须是最多 500 个字符的字符串。", line)
            created_at = record.get("created_at")
            if created_at is not None and (not isinstance(created_at, str) or not _is_iso_datetime(created_at)):
                raise ArchiveFormatError("created_at 必须是 ISO 8601 时间或 null。", line)
            candidate = {"id": conversation_id, "title": title or "未命名会话", "created_at": created_at}
            if conversation_id in conversations and conversations[conversation_id] != candidate:
                raise ArchiveFormatError("同一分片中的会话 ID 重复且内容不一致。", line)
            conversations[conversation_id] = candidate
        elif record_type == "message":
            message_id = _required_text(record, "id", line, 256)
            conversation_id = _required_text(record, "conversation_id", line, 256)
            sequence = record.get("sequence")
            if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
                raise ArchiveFormatError("sequence 必须是从 1 开始的整数。", line)
            speaker = record.get("speaker")
            if not isinstance(speaker, str) or speaker not in SPEAKERS:
                raise ArchiveFormatError("speaker 必须是 user、assistant、system、tool 或 unknown。", line)
            speaker_label = record.get("speaker_label")
            if speaker_label is not None and (not isinstance(speaker_label, str) or len(speaker_label) > 200):
                raise ArchiveFormatError("speaker_label 必须是最多 200 个字符的字符串或 null。", line)
            timestamp = record.get("timestamp")
            if timestamp is not None and (not isinstance(timestamp, str) or not _is_iso_datetime(timestamp)):
                raise ArchiveFormatError("timestamp 必须是 ISO 8601 时间或 null。", line)
            content = record.get("content")
            if not isinstance(content, str):
                raise ArchiveFormatError("content 必须是字符串；当前版本导入文本消息。", line)
            if message_id in message_ids:
                raise ArchiveFormatError("message ID 在同一分片中重复。", line)
            key = (conversation_id, sequence)
            if key in message_sequences:
                raise ArchiveFormatError("同一会话中的 sequence 重复。", line)
            message_ids.add(message_id)
            message_sequences.add(key)
            message_counts_by_conversation[conversation_id] = message_counts_by_conversation.get(conversation_id, 0) + 1
            message_count += 1
            if message_count > MAX_MESSAGES_PER_PART:
                raise ArchiveFormatError("单个分片最多支持 100,000 条消息。", line)
            message_attachments = record.get("attachments", [])
            if not isinstance(message_attachments, list) or len(message_attachments) > 20:
                raise ArchiveFormatError("attachments 必须是最多包含 20 张图片的数组。", line)
            for attachment in message_attachments:
                if not isinstance(attachment, dict) or attachment.get("type", "image") != "image":
                    raise ArchiveFormatError("message attachments 只支持图片对象。", line)
                attachment_id = _required_text(attachment, "id", line, 256)
                if attachment_id in attachment_ids:
                    raise ArchiveFormatError("图片附件 ID 在同一分片中重复。", line)
                attachment_ids.add(attachment_id)
                relative = _attachment_path(attachment.get("path"), line)
                filename = attachment.get("filename")
                if not isinstance(filename, str) or not filename.strip() or len(filename) > 255:
                    raise ArchiveFormatError("图片附件 filename 必须是 1 到 255 个字符。", line)
                if media_root is None:
                    raise ArchiveFormatError("带图片的会话档案必须使用 ZIP 包，并包含 media/ 图片文件。", line)
                root = media_root.resolve()
                image_path = (root / Path(*relative.parts)).resolve()
                if root not in image_path.parents or not image_path.is_file():
                    raise ArchiveFormatError(f"图片附件文件不存在：{relative.as_posix()}。", line)
                byte_size = image_path.stat().st_size
                if byte_size <= 0 or byte_size > MAX_IMAGE_BYTES:
                    raise ArchiveFormatError("单张图片大小必须在 1 字节到 20 MiB 之间。", line)
                mime_type = image_mime_type(image_path)
                expected_mime = IMAGE_MIME_BY_SUFFIX[relative.suffix.lower()]
                if mime_type != expected_mime:
                    raise ArchiveFormatError(f"图片文件内容与扩展名不匹配：{relative.as_posix()}。", line)
                attachments.append({
                    "id": attachment_id, "conversation_id": conversation_id, "message_id": message_id,
                    "path": relative.as_posix(), "filename": filename, "mime_type": mime_type,
                    "byte_size": byte_size,
                })
                attachment_counts_by_conversation[conversation_id] = attachment_counts_by_conversation.get(conversation_id, 0) + 1
                attachment_bytes_by_conversation[conversation_id] = attachment_bytes_by_conversation.get(conversation_id, 0) + byte_size
                if len(attachments) > MAX_IMAGES_PER_PART:
                    raise ArchiveFormatError("单个分片最多支持 10,000 张图片。", line)
        elif record_type == "summary":
            summary_id = _required_text(record, "id", line, 256)
            _required_text(record, "content", line, 100_000)
            if summary_id in summary_ids:
                raise ArchiveFormatError("summary ID 在同一分片中重复。", line)
            based_on = record.get("based_on_message_ids", [])
            if not isinstance(based_on, list) or any(not isinstance(item, str) for item in based_on):
                raise ArchiveFormatError("based_on_message_ids 必须是字符串数组。", line)
            summary_conversation = record.get("conversation_id")
            if summary_conversation is not None and not isinstance(summary_conversation, str):
                raise ArchiveFormatError("summary 的 conversation_id 必须是字符串或 null。", line)
            summary_ids.add(summary_id)
            summary_count += 1
        else:
            raise ArchiveFormatError("type 必须是 archive、conversation、message 或 summary。", line)

    if header is None:
        raise ArchiveFormatError("文件为空或缺少 archive 格式头。")
    if message_count == 0:
        raise ArchiveFormatError("分片中没有 message 记录。")
    return ParsedArchive(
        header, conversations, message_ids, message_sequences, summary_ids, message_count,
        summary_count, message_counts_by_conversation, attachments,
        attachment_counts_by_conversation, attachment_bytes_by_conversation,
    )


def _is_iso_datetime(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return "T" in value and parsed.tzinfo is not None
    except ValueError:
        return False

