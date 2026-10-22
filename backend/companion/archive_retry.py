"""Durable scheduling helpers for archive analysis quota retries."""

import asyncio
import logging

from .common import connect, now

_RETRY_TASKS: set[asyncio.Task] = set()


def quota_limited_error(message: str) -> bool:
    text = message.casefold()
    quota_markers = ("usage limit", "quota", "usage cap", "达到额度", "额度已", "用量限制", "使用限额", "达到上限", "限额已")
    reset_markers = ("reset", "try again", "in 5 hour", "in five hour", "hour limit", "小时后", "小时重置", "稍后重试")
    return any(marker in text for marker in quota_markers) or ("limit" in text and any(marker in text for marker in reset_markers))


def claim_due_archive_analysis_retries() -> list[str]:
    timestamp = now()
    claimed: list[str] = []
    with connect() as db:
        rows = db.execute(
            "SELECT id FROM archive_analysis_jobs WHERE status='waiting_retry' AND retry_after<=?",
            (timestamp,),
        ).fetchall()
        for row in rows:
            result = db.execute(
                "UPDATE archive_analysis_jobs SET status='queued',retry_after=NULL,error=NULL,updated_at=? "
                "WHERE id=? AND status='waiting_retry' AND retry_after<=?",
                (timestamp, row["id"], timestamp),
            )
            if result.rowcount:
                claimed.append(row["id"])
    return claimed


async def archive_analysis_retry_scheduler(poll_seconds: int = 15) -> None:
    while True:
        try:
            from .archive_analysis_routes import run_archive_analysis
            for job_id in claim_due_archive_analysis_retries():
                task = asyncio.create_task(run_archive_analysis(job_id))
                _RETRY_TASKS.add(task)
                task.add_done_callback(_RETRY_TASKS.discard)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("扫描到期的档案分析重试任务失败；稍后会再次扫描。")
        await asyncio.sleep(poll_seconds)
