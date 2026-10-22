import base64
import io
import json
import zipfile
from pathlib import Path

from companion import common
from companion.archive_import_routes import (
    PENDING_ARCHIVE_PREVIEWS, SelectedArchiveImport, discard_archive_preview,
    extract_archive_zip, import_archive_package, import_selected_archive, preview_archive_files,
    validate_archive_package,
)
from companion.archive_read_routes import (
    ArchiveSessionTitleInput, continue_archive_session, delete_archive, delete_archive_session,
    list_archive_imports, list_archive_messages, list_archive_sessions, rename_archive_session,
)
from companion.attachment_routes import get_attachment
from companion.context_store import get_session_summary, save_session_summary
from companion.session_routes import list_messages
from tests.support import ApiFeatureTestCase
from tests.support import archive_jsonl


class ArchiveFeatureTests(ApiFeatureTestCase):
    def test_rename_imported_session_updates_archive_list_and_detail(self) -> None:
        records = [json.loads(line) for line in archive_jsonl("rename-archive").splitlines()]
        staging = Path(common.DATA_DIR) / "rename-archive"
        staging.mkdir()
        path = staging / "part-001.jsonl"
        path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")
        import_archive_package([path], media_root=staging)
        session_id = list_archive_sessions()["items"][0]["id"]

        response = rename_archive_session(session_id, ArchiveSessionTitleInput(title="  杭州周末旅行  "))

        self.assertEqual(response["title"], "杭州周末旅行")
        self.assertEqual(list_archive_sessions()["items"][0]["title"], "杭州周末旅行")
        self.assertEqual(list_archive_messages(session_id)["session"]["title"], "杭州周末旅行")

    def test_rename_archive_session_rejects_blank_title_and_native_session(self) -> None:
        with self.assertRaises(common.HTTPException) as blank_error:
            rename_archive_session("missing", ArchiveSessionTitleInput(title="   "))
        self.assertEqual(blank_error.exception.status_code, 422)

        with common.connect() as db:
            db.execute(
                "INSERT INTO sessions(id,title,created_at,updated_at,origin) VALUES (?,?,?,?, 'native')",
                ("native-session", "本地对话", common.now(), common.now()),
            )
        with self.assertRaises(common.HTTPException) as native_error:
            rename_archive_session("native-session", ArchiveSessionTitleInput(title="不应修改"))
        self.assertEqual(native_error.exception.status_code, 404)

    def test_delete_one_imported_session_keeps_other_sessions_and_cleans_its_images(self) -> None:
        records = [json.loads(line) for line in archive_jsonl("session-delete-archive").splitlines()]
        records[0]["version"] = "1.1"
        records[2]["attachments"] = [{
            "id": "delete-image", "type": "image", "path": "media/delete.png", "filename": "delete.png",
        }]
        records.extend([
            {"type": "conversation", "id": "conversation-2", "title": "保留的会话", "created_at": None},
            {"type": "message", "id": "message-3", "conversation_id": "conversation-2", "sequence": 1,
             "timestamp": None, "speaker": "user", "speaker_label": None, "content": "保留内容"},
        ])
        staging = Path(common.DATA_DIR) / "session-delete-archive"
        (staging / "media").mkdir(parents=True)
        path = staging / "part-001.jsonl"
        path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")
        (staging / "media" / "delete.png").write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/r5sAAAAASUVORK5CYII="
        ))
        imported = import_archive_package([path], media_root=staging)
        archive_id = imported["parts"][0]["archive_id"]
        sessions = list_archive_sessions()["items"]
        delete_target = next(item for item in sessions if item["title"] == "一次旅行")
        preserved = next(item for item in sessions if item["title"] == "保留的会话")
        with common.connect() as db:
            storage_name = db.execute("SELECT storage_name FROM archive_attachments WHERE archive_id=?", (archive_id,)).fetchone()["storage_name"]
        stored_path = Path(common.DATA_DIR) / "attachments" / storage_name
        self.assertTrue(stored_path.is_file())
        continue_archive_session(delete_target["id"])
        with common.connect() as db:
            db.execute(
                "INSERT INTO messages(id,session_id,role,content,created_at,speaker,sequence) VALUES (?,?,?,?,?,?,?)",
                ("session-delete-continuation", delete_target["id"], "user", "续聊内容", common.now(), "user", 3),
            )

        result = delete_archive_session(delete_target["id"])

        self.assertTrue(result["deleted"])
        self.assertFalse(result["deleted_archive"])
        self.assertFalse(stored_path.exists())
        remaining = list_archive_sessions()["items"]
        self.assertEqual([item["id"] for item in remaining], [preserved["id"]])
        self.assertEqual(list_archive_messages(preserved["id"])["items"][0]["content"], "保留内容")
        archive_summary = list_archive_imports()[0]
        self.assertEqual(archive_summary["message_count"], 1)
        self.assertEqual(archive_summary["image_count"], 0)

        result = delete_archive_session(preserved["id"])
        self.assertTrue(result["deleted_archive"])
        self.assertEqual(list_archive_imports(), [])

    def test_gemini_takeout_zip_imports_root_images_referenced_in_message_text(self) -> None:
        records = [json.loads(line) for line in archive_jsonl().splitlines()]
        records[0]["source_platform"] = "Gemini"
        records[2]["content"] = "这是一张图片\n[图片附件：旅行.png；此 JSONL 文本格式不包含图像二进制内容]"
        jsonl = "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n"
        image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/r5sAAAAASUVORK5CYII=")
        package_bytes = io.BytesIO()
        with zipfile.ZipFile(package_bytes, "w") as package:
            package.writestr("Gemini 会话档案.jsonl", jsonl)
            package.writestr("旅行.png", image)

        staging = Path(common.DATA_DIR) / "gemini-takeout-preview"
        staging.mkdir()
        upload_path = staging / "upload.zip"
        upload_path.write_bytes(package_bytes.getvalue())

        files = extract_archive_zip(upload_path, staging)
        preview = preview_archive_files(files, staging)

        self.assertEqual(preview["source_platform"], "Gemini")
        self.assertEqual(preview["image_count"], 1)
        self.assertEqual(preview["conversations"][0]["image_count"], 1)
        imported = import_archive_package(files, media_root=staging)
        self.assertEqual(imported["attachments"], 1)
        session_id = list_archive_sessions()["items"][0]["id"]
        message = list_archive_messages(session_id)["items"][0]
        self.assertEqual(message["content"], "这是一张图片\n")
        self.assertEqual(message["attachments"][0]["filename"], "旅行.png")

    def test_zip_preview_extracts_referenced_images(self) -> None:
        records = [json.loads(line) for line in archive_jsonl("zip-image-archive").splitlines()]
        records[0]["version"] = "1.1"
        records[2]["attachments"] = [{
            "id": "image-1", "type": "image", "path": "media/photo.png", "filename": "photo.png",
        }]
        jsonl = "\n".join(json.dumps(item) for item in records) + "\n"
        image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/r5sAAAAASUVORK5CYII=")
        package_bytes = io.BytesIO()
        with zipfile.ZipFile(package_bytes, "w") as package:
            package.writestr("part-001.jsonl", jsonl)
            package.writestr("media/photo.png", image)

        staging = Path(common.DATA_DIR) / "zip-image-preview"
        staging.mkdir()
        upload_path = staging / "upload.zip"
        upload_path.write_bytes(package_bytes.getvalue())
        files = extract_archive_zip(upload_path, staging)
        preview = preview_archive_files(files, staging)

        self.assertEqual(preview["image_count"], 1)
        self.assertTrue((staging / "media" / "photo.png").is_file())

    def test_imported_images_are_previewed_copied_and_deleted_with_the_archive(self) -> None:
        records = [json.loads(line) for line in archive_jsonl("image-archive").splitlines()]
        records[0]["version"] = "1.1"
        records[2]["attachments"] = [{
            "id": "image-1", "type": "image", "path": "media/photo.png", "filename": "旅行照片.png",
        }]
        staging = Path(common.DATA_DIR) / "image-archive-preview"
        (staging / "media").mkdir(parents=True)
        path = staging / "part-001.jsonl"
        path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")
        (staging / "media" / "photo.png").write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/r5sAAAAASUVORK5CYII="
        ))

        preview = preview_archive_files([path], staging)
        self.assertEqual(preview["image_count"], 1)
        self.assertEqual(preview["conversations"][0]["preview_images"][0]["filename"], "旅行照片.png")

        imported = import_archive_package([path], media_root=staging)
        self.assertEqual(imported["attachments"], 1)
        session_id = list_archive_sessions()["items"][0]["id"]
        detail = list_archive_messages(session_id)
        attachment = detail["items"][0]["attachments"][0]
        self.assertEqual(attachment["filename"], "旅行照片.png")
        self.assertTrue(attachment["url"].startswith("/api/attachments/"))
        served_image = get_attachment(attachment["id"])
        self.assertEqual(served_image.media_type, "image/png")
        with common.connect() as db:
            stored = db.execute("SELECT storage_name FROM archive_attachments WHERE id=?", (attachment["id"],)).fetchone()["storage_name"]
        stored_path = Path(common.DATA_DIR) / "attachments" / stored
        self.assertTrue(stored_path.is_file())

        delete_archive(imported["parts"][0]["archive_id"])
        self.assertFalse(stored_path.exists())

    def test_import_continue_and_delete_preserves_new_turns(self) -> None:
        path = Path(common.DATA_DIR) / "archive.jsonl"
        path.write_text(archive_jsonl(), encoding="utf-8")
        imported = import_archive_package([path])
        self.assertEqual(imported["messages"], 2)
        archive_session = list_archive_sessions()["items"][0]
        session_id = archive_session["id"]
        detail = list_archive_messages(session_id)
        self.assertEqual(detail["total"], 2)
        self.assertEqual(len(detail["summaries"]), 1)

        continued = continue_archive_session(session_id)
        self.assertEqual(continued["origin"], "continued")
        with common.connect() as db:
            db.execute(
                "INSERT INTO messages(id,session_id,role,content,created_at,speaker,sequence) VALUES (?,?,?,?,?,?,?)",
                ("continued-message", session_id, "user", "新增的续聊内容", common.now(), "user", 3),
            )
        save_session_summary(session_id, "导入记录中的旧预算和地点", through_sequence=2)

        deleted = delete_archive(imported["parts"][0]["archive_id"])
        self.assertTrue(deleted["deleted"])
        remaining = list_messages(session_id)
        self.assertEqual([item["content"] for item in remaining], ["新增的续聊内容"])
        self.assertEqual(get_session_summary(session_id)["summary"], "")

    def test_reject_message_that_references_an_undefined_conversation(self) -> None:
        from fastapi import HTTPException

        records = [json.loads(line) for line in archive_jsonl().splitlines()]
        records = [item for item in records if item["type"] != "conversation"]
        path = Path(common.DATA_DIR) / "invalid.jsonl"
        path.write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")
        with self.assertRaises(HTTPException) as error:
            validate_archive_package([path])
        self.assertEqual(error.exception.status_code, 422)

    def test_preview_lists_conversation_message_counts(self) -> None:
        path = Path(common.DATA_DIR) / "preview.jsonl"
        path.write_text(archive_jsonl(), encoding="utf-8")

        preview = preview_archive_files([path])

        self.assertEqual(preview["archive_id"], "feature-archive")
        self.assertEqual(preview["conversations"], [{
            "id": "conversation-1", "title": "一次旅行", "created_at": None,
            "message_count": 2, "image_count": 0, "image_bytes": 0, "preview_images": [],
        }])

    def test_import_only_selected_conversations(self) -> None:
        records = [json.loads(line) for line in archive_jsonl().splitlines()]
        records.extend([
            {"type": "conversation", "id": "conversation-2", "title": "工作计划", "created_at": None},
            {"type": "message", "id": "message-3", "conversation_id": "conversation-2", "sequence": 1,
             "timestamp": None, "speaker": "user", "speaker_label": None, "content": "准备下周的汇报"},
        ])
        staging = Path(common.DATA_DIR) / "archive-preview-test-selection"
        staging.mkdir()
        path = staging / "upload.jsonl"
        path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")

        token = "test-selection"
        PENDING_ARCHIVE_PREVIEWS[token] = staging
        imported = import_selected_archive(SelectedArchiveImport(
            token=token, selected_conversation_ids=["conversation-2"],
        ))
        sessions = list_archive_sessions()["items"]

        self.assertEqual(imported["messages"], 1)
        self.assertEqual([item["title"] for item in sessions], ["工作计划"])
        self.assertEqual(list_archive_messages(sessions[0]["id"])["items"][0]["content"], "准备下周的汇报")
        self.assertNotIn(token, PENDING_ARCHIVE_PREVIEWS)

    def test_discard_preview_removes_staged_files(self) -> None:
        token = "test-discard"
        staging = Path(common.DATA_DIR) / "archive-preview-test-discard"
        staging.mkdir()
        (staging / "upload.jsonl").write_text("temporary", encoding="utf-8")
        PENDING_ARCHIVE_PREVIEWS[token] = staging

        result = discard_archive_preview(token)

        self.assertTrue(result["discarded"])
        self.assertFalse(staging.exists())
