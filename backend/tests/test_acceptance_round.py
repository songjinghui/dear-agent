import json
import tempfile
import unittest
from pathlib import Path

from companion.acceptance_round import create_round, finish_round, rebuild_report, record_case_result


class AcceptanceRoundTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.fixtures = self.root / "fixtures"
        self.fixtures.mkdir()
        (self.fixtures / "dialogue.md").write_text("自然对话输入", encoding="utf-8")
        self.rounds_root = self.root / "rounds"
        self.round_id = "v1-20261028T080000+0800-r01-abc123"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_creates_one_database_directory_and_a_reproducible_manifest_for_a_full_round(self):
        round_dir = create_round(
            rounds_root=self.rounds_root,
            fixture_dir=self.fixtures,
            round_id=self.round_id,
            app_commit="test-commit",
            created_at="2026-10-28T08:00:00+08:00",
        )

        manifest = json.loads((round_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(round_dir, (self.rounds_root / self.round_id).resolve())
        self.assertEqual(manifest["round_id"], self.round_id)
        self.assertEqual(manifest["status"], "prepared")
        self.assertEqual(manifest["app_commit"], "test-commit")
        self.assertTrue(manifest["fixture_sha256"])
        self.assertEqual(manifest["data_dir"], str(round_dir))
        self.assertTrue((round_dir / "evidence").is_dir())
        self.assertIn("A-01", (round_dir / "report.md").read_text(encoding="utf-8"))

    def test_records_case_repeat_evidence_in_the_same_round_report(self):
        round_dir = create_round(
            rounds_root=self.rounds_root,
            fixture_dir=self.fixtures,
            round_id=self.round_id,
            app_commit="test-commit",
            created_at="2026-10-28T08:00:00+08:00",
        )

        record_case_result(
            round_dir,
            case_id="A-03",
            repeat=2,
            status="not_run",
            summary="缺少兼容 API，未执行直接 API 压缩验收。",
            evidence={"reason": "openai_compatible_key_missing"},
        )
        rebuild_report(round_dir)

        manifest = json.loads((round_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["results"][0]["case_id"], "A-03")
        self.assertEqual(manifest["results"][0]["repeat"], 2)
        report = (round_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("A-03 / repeat-02", report)
        self.assertIn("未执行", report)
        self.assertIn('"reason": "openai_compatible_key_missing"', report)

    def test_rejects_a_duplicate_round_id_instead_of_merging_evidence(self):
        create_round(
            rounds_root=self.rounds_root,
            fixture_dir=self.fixtures,
            round_id=self.round_id,
            app_commit="test-commit",
            created_at="2026-10-28T08:00:00+08:00",
        )

        with self.assertRaises(FileExistsError):
            create_round(
                rounds_root=self.rounds_root,
                fixture_dir=self.fixtures,
                round_id=self.round_id,
                app_commit="test-commit",
                created_at="2026-10-28T08:00:00+08:00",
            )

    def test_finishes_a_partial_round_without_claiming_unexecuted_cases_passed(self):
        round_dir = create_round(
            rounds_root=self.rounds_root,
            fixture_dir=self.fixtures,
            round_id=self.round_id,
            app_commit="test-commit",
            created_at="2026-10-28T08:00:00+08:00",
        )

        finish_round(round_dir, status="partial", conclusion="兼容 API 未配置，直接 API 用例未执行。")

        manifest = json.loads((round_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "partial")
        report = (round_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("部分完成", report)
        self.assertIn("兼容 API 未配置", report)
