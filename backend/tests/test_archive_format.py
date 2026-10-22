import base64
import json
import tempfile
import unittest
from pathlib import Path

from companion.archive_format import ArchiveFormatError, validate_archive
from tests.support import archive_jsonl


class ArchiveFormatFeatureTests(unittest.TestCase):
    def test_accepts_a_valid_standard_archive(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "archive.jsonl"
            path.write_text(archive_jsonl(), encoding="utf-8")
            parsed = validate_archive(path)
        self.assertEqual(parsed.message_count, 2)
        self.assertEqual(parsed.summary_count, 1)

    def test_accepts_included_image_attachment_from_media_directory(self) -> None:
        records = [json.loads(line) for line in archive_jsonl().splitlines()]
        records[0]["version"] = "1.1"
        records[2]["attachments"] = [{
            "id": "image-1", "type": "image", "path": "media/photo.png", "filename": "photo.png",
        }]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder)
            (root / "media").mkdir()
            (root / "media" / "photo.png").write_bytes(base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/r5sAAAAASUVORK5CYII="
            ))
            path = root / "archive.jsonl"
            path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records), encoding="utf-8")

            parsed = validate_archive(path, root)

        self.assertEqual(len(parsed.attachments), 1)
        self.assertEqual(parsed.attachments[0]["mime_type"], "image/png")
        self.assertEqual(parsed.attachment_counts_by_conversation["conversation-1"], 1)

    def test_image_attachment_requires_an_included_archive_file(self) -> None:
        records = [json.loads(line) for line in archive_jsonl().splitlines()]
        records[2]["attachments"] = [{
            "id": "image-1", "type": "image", "path": "media/photo.png", "filename": "photo.png",
        }]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "archive.jsonl"
            path.write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")
            with self.assertRaises(ArchiveFormatError):
                validate_archive(path)

    def test_rejects_duplicate_message_sequences(self) -> None:
        invalid = archive_jsonl().replace('"sequence": 2', '"sequence": 1')
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "archive.jsonl"
            path.write_text(invalid, encoding="utf-8")
            with self.assertRaises(ArchiveFormatError):
                validate_archive(path)
