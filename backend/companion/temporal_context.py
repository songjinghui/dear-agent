"""Temporal metadata for reliable long-running conversation context."""

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class TemporalAnchor:
    now: datetime
    timezone_name: str
    previous_message_at: datetime | None


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def build_temporal_anchor(
    messages: list[dict],
    current_index: int,
    *,
    now_iso: str | None = None,
    timezone_name: str | None = None,
) -> TemporalAnchor:
    supplied_now = parse_timestamp(now_iso)
    local_now = supplied_now or datetime.now().astimezone()
    display_timezone = timezone_name or _local_timezone_name(local_now)
    display_now = _in_timezone(local_now, display_timezone)
    previous_message_at = next(
        (
            timestamp
            for item in reversed(messages[:current_index])
            if (timestamp := parse_timestamp(item.get("created_at"))) is not None
        ),
        None,
    )
    return TemporalAnchor(display_now, display_timezone, previous_message_at)


def temporal_prompt(anchor: TemporalAnchor) -> str:
    rows = [
        "时间坐标（仅以明确时间为准，不得把未知事件时间臆测成刚刚发生）：",
        f"- 当前时间：{format_timestamp(anchor.now)}",
        f"- 时区：{anchor.timezone_name}",
    ]
    if anchor.previous_message_at is None:
        rows.append("- 上次对话：当前会话中没有可用的较早消息时间。")
    else:
        previous = _in_timezone(anchor.previous_message_at, anchor.timezone_name)
        rows.append(f"- 上次对话：{format_timestamp(previous)}（距上次对话：{elapsed_label(anchor.now - previous)}）")
    return "\n".join(rows)


def message_content_with_time(item: dict, timezone_name: str) -> str:
    timestamp = parse_timestamp(item.get("created_at"))
    content = str(item.get("content", ""))
    if timestamp is None:
        return content
    return f"[消息时间：{format_timestamp(_in_timezone(timestamp, timezone_name))}（{timezone_name}）]\n{content}"


def format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def elapsed_label(delta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return "不足 1 分钟"
    if seconds < 3600:
        return f"{seconds // 60} 分钟"
    if seconds < 86400:
        return f"{seconds // 3600} 小时"
    return f"{seconds // 86400} 天"


def _in_timezone(value: datetime, timezone_name: str) -> datetime:
    try:
        return value.astimezone(ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError:
        return value


def _local_timezone_name(value: datetime) -> str:
    zone_key = getattr(value.tzinfo, "key", None)
    return zone_key or value.tzname() or "UTC"
