"""Read-only delivery for locally managed conversation images."""

from .common import *  # noqa: F403
from . import common as app_state


@app.get("/api/attachments/{attachment_id}")
def get_attachment(attachment_id: str):
    with connect() as db:
        row = db.execute(
            "SELECT storage_name,mime_type FROM archive_attachments WHERE id=?",
            (attachment_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "图片附件不存在。")
    if row["mime_type"] not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
        raise HTTPException(415, "不支持的图片格式。")
    root = (app_state.DATA_DIR / "attachments").resolve()
    path = (root / row["storage_name"]).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "图片文件不可用。")
    return FileResponse(
        path, media_type=row["mime_type"],
        headers={"X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"},
    )
