"""HTTP endpoints and handlers for the archive import domain."""

import shutil
import time
import re
from pathlib import PurePosixPath

from . import common as app_state
from .common import *  # noqa: F403


PENDING_ARCHIVE_PREVIEWS: dict[str, Path] = {}
PENDING_ARCHIVE_MEDIA: dict[str, dict[str, tuple[Path, str]]] = {}
ARCHIVE_PREVIEW_TTL_SECONDS = 60 * 60

def archive_session_id(archive_id: str, source_id: str) -> str:
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, f"{FORMAT_NAME}:{archive_id}")
    return str(uuid.uuid5(namespace, f"conversation:{source_id}"))


def archive_message_id(archive_id: str, source_id: str) -> str:
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, f"{FORMAT_NAME}:{archive_id}")
    return str(uuid.uuid5(namespace, f"message:{source_id}"))


def archive_attachment_id(archive_id: str, source_id: str) -> str:
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, f"{FORMAT_NAME}:{archive_id}")
    return str(uuid.uuid5(namespace, f"attachment:{source_id}"))


def parse_archive_time(value: object, fallback: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback
    normalized = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).isoformat(timespec="microseconds")
    except ValueError:
        return fallback


def index_message(db: sqlite3.Connection, message_id: str, content: str) -> None:
    try:
        db.execute("INSERT OR REPLACE INTO messages_fts(message_id,content) VALUES (?,?)", (message_id, " ".join(jieba.cut(content))))
    except sqlite3.OperationalError:
        pass


def _store_archive_attachment(db: sqlite3.Connection, archive_id: str, message_id: str, attachment: dict, media_root: Path, created_files: list[Path]) -> None:
    relative = PurePosixPath(attachment["path"])
    source = (media_root / Path(*relative.parts)).resolve()
    root = media_root.resolve()
    if root not in source.parents or not source.is_file():
        raise HTTPException(422, f"图片附件文件不存在：{relative.as_posix()}。")
    mime_type = image_mime_type(source)
    suffix = relative.suffix.lower()
    if mime_type != IMAGE_MIME_BY_SUFFIX.get(suffix) or source.stat().st_size != attachment["byte_size"]:
        raise HTTPException(422, f"图片附件导入期间发生变化：{relative.as_posix()}。")
    digest = hashlib.sha256()
    with source.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    storage_name = f"{digest.hexdigest()}{suffix}"
    storage_dir = app_state.DATA_DIR / "attachments"
    storage_dir.mkdir(parents=True, exist_ok=True)
    target = storage_dir / storage_name
    if not target.exists():
        try:
            with source.open("rb") as input_file, target.open("xb") as output_file:
                shutil.copyfileobj(input_file, output_file, 1024 * 1024)
            created_files.append(target)
        except FileExistsError:
            pass
    elif hashlib.sha256(target.read_bytes()).hexdigest() != digest.hexdigest():
        raise HTTPException(500, "本地附件存储发生哈希冲突。")
    db.execute(
        "INSERT INTO archive_attachments(id,archive_id,message_id,source_attachment_id,filename,mime_type,byte_size,storage_name,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (archive_attachment_id(archive_id, attachment["id"]), archive_id, message_id, attachment["id"],
         attachment["filename"], mime_type, attachment["byte_size"], storage_name, now()),
    )


def import_archive_part(path: Path, selected_conversation_ids: set[str] | None = None, media_root: Path | None = None) -> dict:
    created_files: list[Path] = []
    try:
        return _import_archive_part(path, selected_conversation_ids, media_root, created_files)
    except Exception:
        for image_path in created_files:
            with connect() as db:
                referenced = db.execute("SELECT 1 FROM archive_attachments WHERE storage_name=? LIMIT 1", (image_path.name,)).fetchone()
            if not referenced:
                image_path.unlink(missing_ok=True)
        raise


def _import_archive_part(path: Path, selected_conversation_ids: set[str] | None, media_root: Path | None, created_files: list[Path]) -> dict:
    parsed = validate_archive(path, media_root)
    header = parsed.header
    archive_id = header["archive_id"]
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        while chunk := source_file.read(1024 * 1024):
            digest.update(chunk)
    if parsed.attachments and media_root is not None:
        for attachment in sorted(parsed.attachments, key=lambda item: item["id"]):
            image_path = media_root / Path(*PurePosixPath(attachment["path"]).parts)
            image_digest = hashlib.sha256()
            with image_path.open("rb") as image_file:
                while chunk := image_file.read(1024 * 1024):
                    image_digest.update(chunk)
            digest.update(attachment["id"].encode("utf-8"))
            digest.update(image_digest.digest())
    checksum = digest.hexdigest()
    imported_at = now()
    source_platform = str(header.get("source_platform") or "")[:100]
    with connect() as db:
        prior_part = db.execute(
            "SELECT checksum,message_count,summary_count,attachment_count FROM archive_import_parts WHERE archive_id=? AND part_id=?",
            (archive_id, header["part_id"]),
        ).fetchone()
        if prior_part:
            if prior_part["checksum"] != checksum:
                raise HTTPException(409, "这个 archive_id/part_id 已导入过，但文件内容不同；请为新档案使用新的 archive_id。")
            return {"archive_id": archive_id, "part_id": header["part_id"], "duplicate": True, "messages": prior_part["message_count"], "summaries": prior_part["summary_count"], "attachments": prior_part["attachment_count"]}
        prior_index = db.execute(
            "SELECT part_id FROM archive_import_parts WHERE archive_id=? AND part_index=?",
            (archive_id, header["part_index"]),
        ).fetchone()
        if prior_index:
            raise HTTPException(409, f"分片序号 {header['part_index']} 已被 {prior_index['part_id']} 使用。")

        archive = db.execute("SELECT * FROM archive_imports WHERE id=?", (archive_id,)).fetchone()
        if archive:
            if archive["expected_parts"] != header["part_count"]:
                raise HTTPException(409, "同一档案的分片总数不一致。")
            if archive["source_platform"] and source_platform and archive["source_platform"] != source_platform:
                raise HTTPException(409, "同一档案的来源平台标记不一致。")
            total = db.execute("SELECT COALESCE(SUM(message_count),0) FROM archive_import_parts WHERE archive_id=?", (archive_id,)).fetchone()[0]
            if total + parsed.message_count > 100_000:
                raise HTTPException(413, "一个档案最多支持 100,000 条消息；请拆成不同 archive_id 的档案。")
        else:
            if parsed.message_count > 100_000:
                raise HTTPException(413, "一个档案最多支持 100,000 条消息。")
            db.execute(
                "INSERT INTO archive_imports(id,source_platform,expected_parts,created_at,updated_at) VALUES (?,?,?,?,?)",
                (archive_id, source_platform, header["part_count"], imported_at, imported_at),
            )

        for source_id, conversation in parsed.conversations.items():
            if selected_conversation_ids is not None and source_id not in selected_conversation_ids:
                continue
            local_id = archive_session_id(archive_id, source_id)
            existing = db.execute("SELECT title,origin,import_id FROM sessions WHERE id=?", (local_id,)).fetchone()
            if existing:
                if existing["origin"] not in {"imported", "continued"} or existing["import_id"] != archive_id or existing["title"] != conversation["title"]:
                    raise HTTPException(409, f"会话 {source_id} 与已导入记录冲突。")
                continue
            created_at = parse_archive_time(conversation.get("created_at"), imported_at)
            db.execute(
                "INSERT INTO sessions(id,title,created_at,updated_at,origin,source_platform,import_id,source_conversation_id) VALUES (?,?,?,?, 'imported',?,?,?)",
                (local_id, conversation["title"], created_at, imported_at, source_platform, archive_id, source_id),
            )

        inserted_messages = 0
        inserted_summaries = 0
        pending_attachments: list[tuple[str, str, dict]] = []
        parsed_attachments = {item["id"]: item for item in parsed.attachments}
        for line, record in iter_records(path):
            if record["type"] == "message":
                source_conversation_id = record["conversation_id"]
                if selected_conversation_ids is not None and source_conversation_id not in selected_conversation_ids:
                    continue
                local_session_id = archive_session_id(archive_id, source_conversation_id)
                if not db.execute("SELECT 1 FROM sessions WHERE id=? AND import_id=?", (local_session_id, archive_id)).fetchone():
                    raise HTTPException(422, f"第 {line} 行引用了不存在的会话 {source_conversation_id}。")
                message_id = archive_message_id(archive_id, record["id"])
                speaker = record.get("speaker", "unknown")
                role = "user" if speaker == "user" else "assistant"
                content = record["content"]
                existing = db.execute("SELECT session_id,content,speaker,sequence FROM messages WHERE id=?", (message_id,)).fetchone()
                if existing:
                    raise HTTPException(409, f"消息 ID {record['id']} 已存在于其他分片；每条消息只能出现一次。")
                sequence_conflict = db.execute("SELECT id FROM messages WHERE session_id=? AND sequence=?", (local_session_id, record["sequence"])).fetchone()
                if sequence_conflict:
                    raise HTTPException(409, f"会话 {source_conversation_id} 的消息顺序号 {record['sequence']} 已存在。")
                timestamp = record.get("timestamp")
                db.execute(
                    "INSERT INTO messages(id,session_id,role,content,created_at,speaker,speaker_label,sequence,source_message_id,source_timestamp) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (message_id, local_session_id, role, content, parse_archive_time(timestamp, imported_at), speaker, record.get("speaker_label"), record["sequence"], record["id"], timestamp if isinstance(timestamp, str) else None),
                )
                index_message(db, message_id, content)
                for attachment in record.get("attachments", []):
                    pending_attachments.append((local_session_id, message_id, {**attachment, **parsed_attachments[attachment["id"]]}))
                inserted_messages += 1
            elif record["type"] == "summary":
                conversation_id = record.get("conversation_id")
                if selected_conversation_ids is not None and (
                    (conversation_id is not None and conversation_id not in selected_conversation_ids)
                    or (conversation_id is None and not selected_conversation_ids)
                ):
                    continue
                summary_id = archive_message_id(archive_id, f"summary:{record['id']}")
                local_conversation_id = archive_session_id(archive_id, conversation_id) if isinstance(conversation_id, str) else None
                if local_conversation_id and not db.execute("SELECT 1 FROM sessions WHERE id=? AND import_id=?", (local_conversation_id, archive_id)).fetchone():
                    raise HTTPException(422, f"第 {line} 行摘要引用了不存在的会话 {conversation_id}。")
                based_on = json.dumps(record.get("based_on_message_ids", []), ensure_ascii=False)
                existing = db.execute("SELECT content,based_on_message_ids FROM archive_summaries WHERE id=?", (summary_id,)).fetchone()
                if existing:
                    if existing["content"] != record["content"] or existing["based_on_message_ids"] != based_on:
                        raise HTTPException(409, f"摘要 {record['id']} 与已导入记录冲突。")
                    continue
                db.execute(
                    "INSERT INTO archive_summaries(id,archive_id,source_summary_id,conversation_id,content,based_on_message_ids,created_at) VALUES (?,?,?,?,?,?,?)",
                    (summary_id, archive_id, record["id"], local_conversation_id, record["content"], based_on, imported_at),
                )
                inserted_summaries += 1

        if pending_attachments and media_root is None:
            raise HTTPException(422, "带图片的会话档案必须使用 ZIP 包，并包含 media/ 图片文件。")
        for _, message_id, attachment in pending_attachments:
            _store_archive_attachment(db, archive_id, message_id, attachment, media_root, created_files)

        db.execute(
            "INSERT INTO archive_import_parts(archive_id,part_id,part_index,checksum,message_count,summary_count,attachment_count,imported_at) VALUES (?,?,?,?,?,?,?,?)",
            (archive_id, header["part_id"], header["part_index"], checksum, inserted_messages, inserted_summaries, len(pending_attachments), imported_at),
        )
        db.execute("UPDATE archive_imports SET updated_at=? WHERE id=?", (imported_at, archive_id))
    return {"archive_id": archive_id, "part_id": header["part_id"], "duplicate": False, "messages": inserted_messages, "summaries": inserted_summaries, "attachments": len(pending_attachments)}


def validate_archive_package(files: list[Path], media_root: Path | None = None):
    try:
        parsed = [validate_archive(path, media_root) for path in files]
        archive_ids = {item.header["archive_id"] for item in parsed}
        if len(archive_ids) != 1:
            raise ArchiveFormatError("一个 ZIP 包中的所有分片必须使用相同的 archive_id。")
        if len({item.header["part_id"] for item in parsed}) != len(parsed):
            raise ArchiveFormatError("ZIP 包中的 part_id 不能重复。")
        if len({item.header["part_index"] for item in parsed}) != len(parsed):
            raise ArchiveFormatError("ZIP 包中的 part_index 不能重复。")
        known_conversations: dict[str, dict] = {}
        for item in parsed:
            for source_id, conversation in item.conversations.items():
                if source_id in known_conversations and known_conversations[source_id] != conversation:
                    raise ArchiveFormatError(f"会话 {source_id} 在分片中的定义不一致。")
                known_conversations[source_id] = conversation
        known_conversation_ids = set(known_conversations)
        for path in files:
            for line, record in iter_records(path):
                if record["type"] == "message" and record["conversation_id"] not in known_conversation_ids:
                    raise ArchiveFormatError(f"第 {line} 行引用了未定义的会话 {record['conversation_id']}。")
        if len({item.header["part_count"] for item in parsed}) != 1:
            raise ArchiveFormatError("ZIP 包中的 part_count 必须一致。")
        if len({item.header["version"] for item in parsed}) != 1:
            raise ArchiveFormatError("ZIP 包中的档案格式版本必须一致。")
        platforms = {item.header.get("source_platform") for item in parsed if item.header.get("source_platform")}
        if len(platforms) > 1:
            raise ArchiveFormatError("ZIP 包中的 source_platform 必须一致。")
        if sum(item.message_count for item in parsed) > 100_000:
            raise ArchiveFormatError("一个档案最多支持 100,000 条消息。")
        known_messages: set[str] = set()
        known_sequences: set[tuple[str, int]] = set()
        known_attachments: set[str] = set()
        attachment_count = 0
        for item in parsed:
            if known_messages.intersection(item.message_ids):
                raise ArchiveFormatError("message ID 必须在整个档案中唯一。")
            if known_sequences.intersection(item.message_sequences):
                raise ArchiveFormatError("sequence 必须在整个会话中唯一。")
            known_messages.update(item.message_ids)
            known_sequences.update(item.message_sequences)
            attachment_ids = {attachment["id"] for attachment in item.attachments}
            if known_attachments.intersection(attachment_ids):
                raise ArchiveFormatError("图片附件 ID 必须在整个档案中唯一。")
            known_attachments.update(attachment_ids)
            attachment_count += len(item.attachments)
        if attachment_count > MAX_IMAGES_PER_PART:
            raise ArchiveFormatError("一个档案最多支持 10,000 张图片。")
        return parsed
    except ArchiveFormatError as exc:
        raise HTTPException(422, str(exc)) from exc


def import_archive_package(files: list[Path], selected_conversation_ids: set[str] | None = None, media_root: Path | None = None) -> dict:
    parsed = validate_archive_package(files, media_root)
    if selected_conversation_ids is not None:
        available = {conversation_id for item in parsed for conversation_id in item.conversations}
        unknown = selected_conversation_ids - available
        if unknown:
            raise HTTPException(422, f"选择中包含档案不存在的会话：{', '.join(sorted(unknown)[:5])}")
        if not selected_conversation_ids:
            raise HTTPException(400, "请至少选择一个会话导入。")
    results = [import_archive_part(path, selected_conversation_ids, media_root) for path in files]
    return {
        "parts": results,
        "messages": sum(item["messages"] for item in results),
        "summaries": sum(item["summaries"] for item in results),
        "attachments": sum(item.get("attachments", 0) for item in results),
    }


def preview_archive_files(files: list[Path], media_root: Path | None = None) -> dict:
    parsed = validate_archive_package(files, media_root)
    conversation_by_id: dict[str, dict] = {}
    conversations: dict[str, dict] = {}
    for item in parsed:
        for conversation_id, conversation in item.conversations.items():
            current = conversations.setdefault(conversation_id, {
                "id": conversation_id,
                "title": conversation["title"],
                "created_at": conversation.get("created_at"),
                "message_count": 0,
                "image_count": 0,
                "image_bytes": 0,
                "preview_images": [],
            })
            conversation_by_id[conversation_id] = current
            current["message_count"] += item.message_counts_by_conversation.get(conversation_id, 0)
            if current["title"] != conversation["title"] or current["created_at"] != conversation.get("created_at"):
                raise HTTPException(422, f"会话 {conversation_id} 在分片中的定义不一致。")
    for item in parsed:
        for attachment in item.attachments:
            current = conversation_by_id.get(attachment["conversation_id"])
            if current is None:
                raise HTTPException(422, f"图片附件引用了未定义的会话 {attachment['conversation_id']}。")
            current["image_count"] += 1
            current["image_bytes"] += attachment["byte_size"]
            if len(current["preview_images"]) < 3:
                current["preview_images"].append({
                    "id": attachment["id"], "filename": attachment["filename"],
                    "mime_type": attachment["mime_type"], "byte_size": attachment["byte_size"],
                })
    header = parsed[0].header
    return {
        "archive_id": header["archive_id"],
        "source_platform": header.get("source_platform") or "",
        "expected_parts": header["part_count"],
        "image_count": sum(len(item.attachments) for item in parsed),
        "image_bytes": sum(sum(item.attachment_bytes_by_conversation.values()) for item in parsed),
        "conversations": sorted(conversations.values(), key=lambda item: (item["title"].casefold(), item["id"])),
    }


def archive_media_index(files: list[Path], media_root: Path) -> dict[str, tuple[Path, str]]:
    result: dict[str, tuple[Path, str]] = {}
    root = media_root.resolve()
    for archive_file in files:
        parsed = validate_archive(archive_file, media_root)
        for attachment in parsed.attachments:
            relative = PurePosixPath(attachment["path"])
            path = (root / Path(*relative.parts)).resolve()
            if root not in path.parents:
                raise HTTPException(422, "图片附件路径超出档案暂存目录。")
            result[attachment["id"]] = (path, attachment["mime_type"])
    return result


def cleanup_pending_archive_previews() -> None:
    cutoff = time.time() - ARCHIVE_PREVIEW_TTL_SECONDS
    for token, path in list(PENDING_ARCHIVE_PREVIEWS.items()):
        if not path.exists() or path.stat().st_mtime < cutoff:
            shutil.rmtree(path, ignore_errors=True)
            PENDING_ARCHIVE_PREVIEWS.pop(token, None)
            PENDING_ARCHIVE_MEDIA.pop(token, None)
    for path in app_state.DATA_DIR.glob("archive-preview-*"):
        if path.is_dir() and path.stat().st_mtime < cutoff:
            shutil.rmtree(path, ignore_errors=True)


GEMINI_IMAGE_MARKER = re.compile(r"\[图片附件：([^\]；]+)(?:；[^\]]*)?\]")


def normalize_gemini_takeout(files: list[Path], staging: Path, image_names: set[str]) -> int:
    """Convert Gemini Takeout's inline image markers to archive v1.1 attachments."""
    media_root = staging / "media"
    available = {name.casefold(): name for name in image_names}
    linked_images = 0
    for path in files:
        updated: list[str] = []
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                updated.append(raw_line)
                continue
            if not isinstance(record, dict):
                updated.append(raw_line)
                continue
            source_platform = record.get("source_platform")
            if record.get("type") == "archive" and isinstance(source_platform, str) and source_platform.casefold() == "gemini":
                record["version"] = "1.1"
            if record.get("type") == "message" and isinstance(record.get("content"), str):
                attachments = list(record.get("attachments", []))
                marker_index = 0

                def replace_marker(match: re.Match[str]) -> str:
                    nonlocal linked_images, marker_index
                    requested_name = match.group(1).strip()
                    filename = available.get(requested_name.casefold())
                    if filename is None:
                        return match.group(0)
                    marker_index += 1
                    linked_images += 1
                    source = media_root / filename
                    attachment_id = hashlib.sha256(
                        f"{record.get('id', '')}\0{filename}\0{marker_index}".encode("utf-8")
                    ).hexdigest()
                    attachments.append({
                        "id": f"gemini-{attachment_id}",
                        "type": "image",
                        "path": f"media/{filename}",
                        "filename": filename,
                    })
                    return ""

                record["content"] = GEMINI_IMAGE_MARKER.sub(replace_marker, record["content"])
                if marker_index:
                    if len(attachments) > 20:
                        raise HTTPException(422, "每条 Gemini 消息最多支持 20 张图片。")
                    record["attachments"] = attachments
            updated.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        content = "\n".join(updated) + "\n"
        if len(content.encode("utf-8")) > MAX_PART_BYTES:
            raise HTTPException(413, "转换后的 JSONL 分片不能超过 100 MiB。")
        path.write_text(content, encoding="utf-8")
    return linked_images


def extract_archive_zip(upload_path: Path, staging: Path) -> list[Path]:
    try:
        with zipfile.ZipFile(upload_path) as package:
            members = [item for item in package.infolist() if not item.is_dir()]
            if not members or len(members) > 10_100:
                raise HTTPException(422, "ZIP 包内容过多；最多支持 100 个分片和 10,000 张图片。")
            if sum(item.file_size for item in members) > 500 * 1024 * 1024:
                raise HTTPException(413, "ZIP 解压后的内容不能超过 500 MiB。")
            json_members = []
            media_members = []
            root_image_members = []
            seen_names: set[str] = set()
            for member in members:
                name = member.filename
                if "\\" in name:
                    raise HTTPException(422, "ZIP 内路径必须使用正斜杠。")
                member_path = PurePosixPath(name)
                if member_path.is_absolute() or any(part in {"", ".", ".."} for part in member_path.parts):
                    raise HTTPException(422, "ZIP 包含不安全的文件路径。")
                normalized_name = member_path.as_posix()
                if normalized_name.casefold() in seen_names:
                    raise HTTPException(422, "ZIP 中不能包含重名文件。")
                seen_names.add(normalized_name.casefold())
                if member_path.suffix.lower() in {".jsonl", ".ndjson"}:
                    json_members.append(member)
                elif member_path.parts[0] == "media" and len(member_path.parts) > 1 and member_path.suffix.lower() in IMAGE_MIME_BY_SUFFIX:
                    if member.file_size > MAX_IMAGE_BYTES:
                        raise HTTPException(413, "单张图片不能超过 20 MiB。")
                    media_members.append((member, member_path))
                elif len(member_path.parts) == 1 and member_path.suffix.lower() in IMAGE_MIME_BY_SUFFIX:
                    if member.file_size > MAX_IMAGE_BYTES:
                        raise HTTPException(413, "单张图片不能超过 20 MiB。")
                    root_image_members.append((member, member_path))
                else:
                    raise HTTPException(422, "ZIP 只能包含 JSONL/NDJSON 分片、media/ 图片，或 Gemini Takeout 根目录图片。")
            if not json_members or len(json_members) > 100:
                raise HTTPException(422, "ZIP 包必须包含 1 到 100 个 JSONL 分片。")
            if len(media_members) + len(root_image_members) > MAX_IMAGES_PER_PART:
                raise HTTPException(422, "ZIP 包最多支持 10,000 张图片。")
            files = []
            unpacked_bytes = 0
            for index, member in enumerate(json_members):
                target = staging / f"part-{index:03d}.jsonl"
                with package.open(member) as source, target.open("wb") as output:
                    copied = 0
                    while chunk := source.read(1024 * 1024):
                        copied += len(chunk)
                        unpacked_bytes += len(chunk)
                        if unpacked_bytes > 500 * 1024 * 1024:
                            raise HTTPException(413, "ZIP 解压后的内容不能超过 500 MiB。")
                        if copied > MAX_PART_BYTES:
                            raise HTTPException(413, "ZIP 内单个 JSONL 分片不能超过 100 MiB。")
                        output.write(chunk)
                files.append(target)
            for member, member_path in [*media_members, *[(member, PurePosixPath("media") / image_path.name) for member, image_path in root_image_members]]:
                target = staging.joinpath(*member_path.parts)
                resolved = target.resolve()
                if staging.resolve() not in resolved.parents:
                    raise HTTPException(422, "ZIP 图片路径超出暂存目录。")
                if target.exists():
                    raise HTTPException(422, "ZIP 中的图片路径发生冲突。")
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(member) as source, target.open("wb") as output:
                    copied = 0
                    while chunk := source.read(1024 * 1024):
                        copied += len(chunk)
                        unpacked_bytes += len(chunk)
                        if copied > MAX_IMAGE_BYTES or unpacked_bytes > 500 * 1024 * 1024:
                            raise HTTPException(413, "图片超过单张 20 MiB 或档案解压总量 500 MiB 的限制。")
                        output.write(chunk)
            if root_image_members:
                linked = normalize_gemini_takeout(files, staging, {image_path.name for _, image_path in root_image_members})
                if not linked:
                    raise HTTPException(422, "ZIP 根目录中的图片没有匹配到 Gemini 消息里的“图片附件：文件名”标记。")
            return files
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise HTTPException(422, "ZIP 文件无法读取。") from exc


async def receive_archive_files(request: Request, staging: Path) -> tuple[str, list[Path]]:
    filename = unquote(request.headers.get("x-filename", "archive.jsonl")).lower()
    if not filename.endswith((".jsonl", ".ndjson", ".zip")):
        raise HTTPException(415, "请选择标准 JSONL/NDJSON 文件或 ZIP 分片包。")
    staging.mkdir(parents=True, exist_ok=True)
    upload_path = staging / ("upload.zip" if filename.endswith(".zip") else "upload.jsonl")
    total_bytes = 0
    try:
        with upload_path.open("wb") as output:
            async for chunk in request.stream():
                total_bytes += len(chunk)
                if total_bytes > MAX_PART_BYTES:
                    raise HTTPException(413, "上传文件不能超过 100 MiB；请按分片导出。")
                output.write(chunk)
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(500, "无法暂存导入文件。") from exc

    files = extract_archive_zip(upload_path, staging) if filename.endswith(".zip") else [upload_path]
    return filename, files


class SelectedArchiveImport(BaseModel):
    token: str
    selected_conversation_ids: list[str]


@app.post("/api/archives/preview")
async def preview_archive(request: Request) -> dict:
    cleanup_pending_archive_previews()
    token = uuid.uuid4().hex
    staging = app_state.DATA_DIR / f"archive-preview-{token}"
    try:
        _, files = await receive_archive_files(request, staging)
        preview = preview_archive_files(files, staging)
        PENDING_ARCHIVE_PREVIEWS[token] = staging
        PENDING_ARCHIVE_MEDIA[token] = archive_media_index(files, staging)
        return {"token": token, **preview}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


@app.post("/api/archives/import-selected")
def import_selected_archive(payload: SelectedArchiveImport) -> dict:
    cleanup_pending_archive_previews()
    staging = PENDING_ARCHIVE_PREVIEWS.get(payload.token)
    if staging is None or not staging.exists():
        raise HTTPException(410, "档案预览已过期，请重新选择文件。")
    files = sorted(staging.glob("*.jsonl"))
    if not files:
        raise HTTPException(410, "找不到暂存的档案文件，请重新选择文件。")
    result = import_archive_package(files, set(payload.selected_conversation_ids), staging)
    PENDING_ARCHIVE_PREVIEWS.pop(payload.token, None)
    PENDING_ARCHIVE_MEDIA.pop(payload.token, None)
    shutil.rmtree(staging, ignore_errors=True)
    return result


@app.delete("/api/archives/preview/{token}")
def discard_archive_preview(token: str) -> dict:
    staging = PENDING_ARCHIVE_PREVIEWS.pop(token, None)
    PENDING_ARCHIVE_MEDIA.pop(token, None)
    if staging:
        shutil.rmtree(staging, ignore_errors=True)
    return {"discarded": staging is not None}


@app.get("/api/archives/preview/{token}/images/{attachment_id}")
def preview_archive_image(token: str, attachment_id: str):
    media = PENDING_ARCHIVE_MEDIA.get(token, {}).get(attachment_id)
    if media is None:
        raise HTTPException(404, "预览图片不存在或预览已过期。")
    path, mime_type = media
    if not path.is_file():
        raise HTTPException(404, "预览图片已不可用。")
    return FileResponse(path, media_type=mime_type, headers={"X-Content-Type-Options": "nosniff"})


@app.post("/api/archives/import")
async def import_archive(request: Request) -> dict:
    app_state.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="archive-import-", dir=app_state.DATA_DIR) as temp_dir:
        _, files = await receive_archive_files(request, Path(temp_dir))
        result = import_archive_package(files, media_root=Path(temp_dir))
        return result
