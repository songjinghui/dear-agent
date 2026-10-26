import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from companion import common
from companion.archive_analysis_routes import CandidateDecision, call_analysis_provider, decide_archive_memory_candidate
from companion.archive_retry import claim_due_archive_analysis_retries
from companion.archive_organization_routes import (
    candidate_batches, claim_queued_candidate_organizations, parse_organization_groups,
    queue_candidate_organization,
)
from companion.memory_routes import list_memories
from companion.providers import ProviderStatus
from tests.support import ApiFeatureTestCase


class ArchiveAnalysisFeatureTests(ApiFeatureTestCase):
    def test_archive_analysis_uses_selected_cli_provider(self) -> None:
        class RecordingProvider:
            display_name = "Codex Agent"

            def __init__(self) -> None:
                self.context = None

            def status(self) -> ProviderStatus:
                return ProviderStatus(available=True, authenticated=True)

            async def run_turn(self, context, on_delta) -> str:
                self.context = context
                return '{"summary":"本地 CLI 分析完成"}'

        provider = RecordingProvider()
        with (
            patch("companion.archive_analysis_routes.read_settings", return_value={"provider": "codex-app-server"}),
            patch.object(common.cli_providers, "get", return_value=provider),
        ):
            answer = asyncio.run(call_analysis_provider("job-123", "分析提示词", "导入的会话内容"))

        self.assertEqual(answer, '{"summary":"本地 CLI 分析完成"}')
        self.assertIsNotNone(provider.context)
        self.assertEqual(provider.context.session_id, "archive-analysis:job-123")
        self.assertEqual(provider.context.prompt, "分析提示词")
        self.assertEqual(provider.context.content, "导入的会话内容")
        self.assertEqual(provider.context.prior_history, [])

    def test_organization_batches_fit_limit_and_preserve_all_candidates(self) -> None:
        candidates = [{"id": str(index), "topic": "主题", "content": "x" * 3000} for index in range(3)]
        batches = candidate_batches(candidates, max_chars=8000)
        self.assertTrue(all(sum(len(item["content"]) + 100 for item in batch) <= 8000 for batch in batches))
        self.assertEqual([item["id"] for batch in batches for item in batch], ["0", "1", "2"])

    def test_organization_parser_skips_non_json_prefix(self) -> None:
        result = parse_organization_groups("说明文字 {not json} 结果：{\"groups\":[]}")
        self.assertEqual(result, {"groups": []})

    def test_completed_analysis_automatically_queues_candidate_organization(self) -> None:
        job_id, candidate_id = str(uuid.uuid4()), str(uuid.uuid4())
        timestamp = common.now()
        with common.connect() as db:
            db.execute(
                "INSERT INTO archive_analysis_jobs(id,archive_id,session_id,status,created_at,updated_at) VALUES (?, 'archive', 'session', 'complete', ?, ?)",
                (job_id, timestamp, timestamp),
            )
            db.execute(
                "INSERT INTO archive_memory_candidates(id,job_id,archive_id,session_id,content,memory_type,evidence_type,sensitivity,rationale,created_at) "
                "VALUES (?,?, 'archive','session','用户喜欢提前规划','semantic','preference','normal','明确表达',?)",
                (candidate_id, job_id, timestamp),
            )

        self.assertTrue(queue_candidate_organization(job_id))
        self.assertFalse(queue_candidate_organization(job_id))
        self.assertEqual(claim_queued_candidate_organizations(), [job_id])
        self.assertEqual(claim_queued_candidate_organizations(), [])
        with common.connect() as db:
            status = db.execute("SELECT status FROM archive_memory_candidate_organizations WHERE job_id=?", (job_id,)).fetchone()["status"]
        self.assertEqual(status, "processing")

    def test_due_analysis_retry_is_claimed_once_and_future_retry_waits(self) -> None:
        due_id, future_id = str(uuid.uuid4()), str(uuid.uuid4())
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds")
        future = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(timespec="seconds")
        with common.connect() as db:
            for job_id, retry_after in ((due_id, past), (future_id, future)):
                db.execute(
                    "INSERT INTO archive_analysis_jobs(id,archive_id,session_id,status,progress,checkpoint_sequence,retry_after,created_at,updated_at) "
                    "VALUES (?, 'archive', 'session', 'waiting_retry', 42, 8, ?, ?, ?)",
                    (job_id, retry_after, common.now(), common.now()),
                )

        self.assertEqual(claim_due_archive_analysis_retries(), [due_id])
        self.assertEqual(claim_due_archive_analysis_retries(), [])
        with common.connect() as db:
            due = db.execute("SELECT status,retry_after,progress,checkpoint_sequence FROM archive_analysis_jobs WHERE id=?", (due_id,)).fetchone()
            future_status = db.execute("SELECT status FROM archive_analysis_jobs WHERE id=?", (future_id,)).fetchone()["status"]
        self.assertEqual(tuple(due), ("queued", None, 42.0, 8))
        self.assertEqual(future_status, "waiting_retry")

    def test_approving_candidate_creates_memory_with_source_reference(self) -> None:
        job_id = str(uuid.uuid4())
        candidate_id = str(uuid.uuid4())
        source_refs = [{
            "archive_id": "import-1", "session_id": "session-1", "message_id": "message-1",
            "sequence": 1, "timestamp": "时间未知",
        }]
        with common.connect() as db:
            db.execute(
                "INSERT INTO archive_analysis_jobs(id,archive_id,session_id,status,created_at,updated_at) VALUES (?,?,?,'complete',?,?)",
                (job_id, "import-1", "session-1", common.now(), common.now()),
            )
            db.execute(
                "INSERT INTO archive_memory_candidates(id,job_id,archive_id,session_id,content,memory_type,evidence_type,importance,sensitivity,rationale,source_refs,status,created_at) "
                "VALUES (?,?,?,?,?,'semantic','preference',4,'normal','用户明确表达偏好',?,'pending',?)",
                (candidate_id, job_id, "import-1", "session-1", "喜欢先听完再讨论建议", json.dumps(source_refs), common.now()),
            )

        result = decide_archive_memory_candidate(candidate_id, CandidateDecision(action="approve"))
        memories = list_memories()

        self.assertEqual(result["id"], candidate_id)
        self.assertEqual(len(memories), 1)
        self.assertEqual(json.loads(memories[0]["source_refs"]), source_refs)
