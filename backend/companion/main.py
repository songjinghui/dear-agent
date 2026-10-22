"""Application assembly and frontend fallback."""

import asyncio

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .common import FRONTEND_DIST, app
from .archive_analysis_routes import run_archive_analysis
from .archive_retry import archive_analysis_retry_scheduler
from .archive_organization_routes import candidate_organization_scheduler
from .common import connect, now
from . import (
    settings_routes,
    session_routes,
    archive_import_routes,
    memory_routes,
    archive_read_routes,
    archive_analysis_routes,
    archive_organization_routes,
    attachment_routes,
    model_call_logs,
    chat_routes,
)


@app.on_event("startup")
async def resume_archive_jobs_after_restart() -> None:
    with connect() as db:
        jobs = db.execute("SELECT id FROM archive_analysis_jobs WHERE status IN ('queued','processing')").fetchall()
        db.execute("UPDATE archive_analysis_jobs SET status='queued',updated_at=? WHERE status='processing'", (now(),))
        db.execute("UPDATE archive_memory_candidate_organizations SET status='queued',updated_at=? WHERE status='processing'", (now(),))
    for job in jobs:
        asyncio.create_task(run_archive_analysis(job["id"]))
    asyncio.create_task(archive_analysis_retry_scheduler())
    asyncio.create_task(candidate_organization_scheduler())

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        candidate = (FRONTEND_DIST / path).resolve()
        if candidate.is_file() and FRONTEND_DIST.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
