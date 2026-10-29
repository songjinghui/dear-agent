"""Create and maintain one isolated, reproducible V1 acceptance round."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


CASE_IDS = tuple(f"A-{number:02d}" for number in range(1, 10))
RESULT_LABELS = {
    "passed": "通过",
    "failed": "失败",
    "not_run": "未执行",
}
ROUND_LABELS = {
    "prepared": "待执行",
    "running": "执行中",
    "complete": "完成",
    "partial": "部分完成",
}
RoundStatus = Literal["prepared", "running", "complete", "partial"]
ResultStatus = Literal["passed", "failed", "not_run"]


def create_round(
    *,
    rounds_root: Path,
    fixture_dir: Path,
    round_id: str,
    app_commit: str,
    created_at: str | None = None,
) -> Path:
    """Allocate one data directory for a whole acceptance round, never a single case."""
    _validate_round_id(round_id)
    fixture_dir = fixture_dir.resolve()
    if not fixture_dir.is_dir():
        raise FileNotFoundError(f"验收数据目录不存在：{fixture_dir}")

    rounds_root = rounds_root.resolve()
    round_dir = rounds_root / round_id
    if round_dir.exists():
        raise FileExistsError(f"验收轮次已存在，拒绝混合证据：{round_id}")

    round_dir.mkdir(parents=True)
    (round_dir / "evidence").mkdir()
    manifest = {
        "format": "personal-companion-agent-acceptance-round",
        "version": 1,
        "round_id": round_id,
        "status": "prepared",
        "created_at": created_at or _now_iso(),
        "app_commit": app_commit or "unknown",
        "fixture_dir": str(fixture_dir),
        "fixture_sha256": fixture_sha256(fixture_dir),
        "data_dir": str(round_dir),
        "results": [],
        "conclusion": "",
    }
    _write_manifest(round_dir, manifest)
    _write_report(round_dir, manifest)
    return round_dir


def record_case_result(
    round_dir: Path,
    *,
    case_id: str,
    repeat: int,
    status: ResultStatus,
    summary: str,
    evidence: dict,
) -> None:
    """Record one case/repeat once, keeping its evidence inside the same round."""
    if case_id not in CASE_IDS:
        raise ValueError(f"未知验收用例：{case_id}")
    if not isinstance(repeat, int) or repeat < 1:
        raise ValueError("重复次数必须是从 1 开始的整数。")
    if status not in RESULT_LABELS:
        raise ValueError(f"未知验收结论：{status}")
    if not isinstance(evidence, dict):
        raise ValueError("验收证据必须为对象。")

    manifest = _read_manifest(round_dir)
    if manifest["status"] in {"complete", "partial"}:
        raise ValueError("验收轮次已经结束，不能再追加结果。")
    if any(item["case_id"] == case_id and item["repeat"] == repeat for item in manifest["results"]):
        raise ValueError(f"{case_id} / repeat-{repeat:02d} 已有记录，拒绝覆盖证据。")

    manifest["results"].append({
        "case_id": case_id,
        "repeat": repeat,
        "status": status,
        "summary": summary,
        "evidence": evidence,
    })
    manifest["results"].sort(key=lambda item: (item["case_id"], item["repeat"]))
    manifest["status"] = "running"
    _write_manifest(round_dir, manifest)
    _write_report(round_dir, manifest)


def finish_round(round_dir: Path, *, status: Literal["complete", "partial"], conclusion: str) -> None:
    """Close a round explicitly; incomplete evidence remains visible as not executed."""
    if status not in {"complete", "partial"}:
        raise ValueError("结束状态只能是 complete 或 partial。")
    manifest = _read_manifest(round_dir)
    if manifest["status"] in {"complete", "partial"}:
        raise ValueError("验收轮次已经结束。")
    manifest["status"] = status
    manifest["finished_at"] = _now_iso()
    manifest["conclusion"] = conclusion
    _write_manifest(round_dir, manifest)
    _write_report(round_dir, manifest)


def rebuild_report(round_dir: Path) -> None:
    """Regenerate a human-readable report from the immutable round manifest."""
    _write_report(round_dir, _read_manifest(round_dir))


def fixture_sha256(fixture_dir: Path) -> str:
    """Hash names and contents so later reports identify the exact input package."""
    digest = hashlib.sha256()
    files = sorted(path for path in fixture_dir.rglob("*") if path.is_file())
    for path in files:
        digest.update(path.relative_to(fixture_dir).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _read_manifest(round_dir: Path) -> dict:
    round_dir = round_dir.resolve()
    manifest_path = round_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"没有找到验收清单：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("round_id") != round_dir.name:
        raise ValueError("验收清单的 round_id 与目录名不一致。")
    return manifest


def _write_manifest(round_dir: Path, manifest: dict) -> None:
    _atomic_write(round_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def _write_report(round_dir: Path, manifest: dict) -> None:
    status_label = ROUND_LABELS[manifest["status"]]
    lines = [
        f"# V1 验收报告：{manifest['round_id']}",
        "",
        f"- 状态：{status_label}",
        f"- 创建时间：{manifest['created_at']}",
        f"- 应用版本：{manifest['app_commit']}",
        f"- 数据包 SHA-256：`{manifest['fixture_sha256']}`",
        f"- 隔离数据目录：`{manifest['data_dir']}`",
    ]
    if manifest.get("finished_at"):
        lines.append(f"- 结束时间：{manifest['finished_at']}")
    lines.extend(["", "## 用例结果", "", "| 用例 / 重复 | 结果 | 摘要 |", "| --- | --- | --- |"])
    results = manifest.get("results", [])
    if results:
        for item in results:
            lines.append(
                f"| {item['case_id']} / repeat-{item['repeat']:02d} | {RESULT_LABELS[item['status']]} | {item['summary']} |"
            )
    else:
        lines.append("| A-01 至 A-09 / — | 未执行 | 尚未记录执行结果。 |")
    if results:
        lines.extend(["", "## 证据索引", ""])
        for item in results:
            lines.extend([
                f"### {item['case_id']} / repeat-{item['repeat']:02d}",
                "",
                "```json",
                json.dumps(item["evidence"], ensure_ascii=False, indent=2, sort_keys=True),
                "```",
                "",
            ])
    lines.extend([
        "",
        "## 与上一轮对比",
        "",
        "本轮未自动推断变化；比较时应选择上一轮的 `report.md`，逐个用例核对相同数据包版本、配置快照与原始证据。",
    ])
    if manifest.get("conclusion"):
        lines.extend(["", "## 结论", "", manifest["conclusion"]])
    _atomic_write(round_dir / "report.md", "\n".join(lines) + "\n")


def _atomic_write(path: Path, content: str) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _validate_round_id(round_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{2,127}", round_id):
        raise ValueError("round_id 只能由字母、数字、点、下划线、加号和连字符组成。")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
