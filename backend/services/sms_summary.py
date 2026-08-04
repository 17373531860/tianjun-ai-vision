"""短信 12 小时滚动汇总的数据读取与窗口水位存储。"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path


SUMMARY_WINDOW_SECONDS = 12 * 60 * 60
SMS_SUMMARY_STATE_FILENAME = "sms_summary_state.json"
ALLOWED_SUMMARY_SCHEDULE_MODES = frozenset({"rolling_12h", "daily_shift"})


def shift_window_end(
    window_start: datetime,
    *,
    start_hour: int,
    end_hour: int,
) -> datetime:
    """由对齐后的窗起点计算窗终点（左闭右开）。"""

    start = _normalize_datetime(window_start).replace(
        minute=0, second=0, microsecond=0
    )
    if start.hour == start_hour:
        return start.replace(hour=end_hour)
    if start.hour == end_hour:
        nxt = start + timedelta(days=1)
        return nxt.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    raise ValueError(
        f"班次窗起点未对齐到 {start_hour}:00 或 {end_hour}:00：{start.isoformat()}"
    )


def next_shift_window_start(
    window_end: datetime,
    *,
    start_hour: int,
    end_hour: int,
    send_night_window: bool,
) -> datetime:
    """闭合一个班次窗后的下一窗起点。"""

    end = _normalize_datetime(window_end).replace(minute=0, second=0, microsecond=0)
    if end.hour == end_hour:
        if send_night_window:
            return end
        nxt = end + timedelta(days=1)
        return nxt.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    if end.hour == start_hour:
        return end
    raise ValueError(f"班次窗终点未对齐：{end.isoformat()}")


def open_daily_shift_window_start(
    now: datetime,
    *,
    start_hour: int,
    end_hour: int,
    send_night_window: bool,
) -> datetime:
    """推断当前应处理/等待的班次窗起点（用于模式切换对齐）。

    晚于班次结束时仍返回「当天白班起点」，便于 ``run_due`` 补发刚到期的白班窗。
    """

    current = _normalize_datetime(now)
    today_start = current.replace(
        hour=start_hour, minute=0, second=0, microsecond=0
    )
    today_end = current.replace(hour=end_hour, minute=0, second=0, microsecond=0)
    if not send_night_window:
        if current < today_start:
            return today_start - timedelta(days=1)
        return today_start
    if current < today_start:
        return (today_start - timedelta(days=1)).replace(hour=end_hour)
    if current < today_end:
        return today_start
    tomorrow_start = today_start + timedelta(days=1)
    if current < tomorrow_start:
        return today_end
    return tomorrow_start


def is_aligned_shift_start(
    value: datetime, *, start_hour: int, end_hour: int
) -> bool:
    ts = _normalize_datetime(value)
    return (
        ts.minute == 0
        and ts.second == 0
        and ts.microsecond == 0
        and ts.hour in {start_hour, end_hour}
    )


@dataclass(frozen=True)
class SmsSummaryCounts:
    """一个工位在一个闭合窗口内的已结算周期数。"""

    ok_count: int = 0
    ng_count: int = 0

    @property
    def total(self) -> int:
        return self.ok_count + self.ng_count


@dataclass(frozen=True)
class SmsSummaryState:
    """当前滚动窗口及部分入队进度。"""

    window_start: datetime
    last_finalized_window_id: str = ""
    queued_window_id: str = ""
    queued_channels: tuple[int, ...] = ()


def summary_window_id(start: datetime, end: datetime) -> str:
    """构造稳定窗口 ID；边界采用左闭右开 ``[start, end)``。"""

    return f"{start.isoformat(timespec='seconds')}__{end.isoformat(timespec='seconds')}"


def load_completed_cycle_counts(
    window_start: datetime,
    window_end: datetime,
) -> dict[int, SmsSummaryCounts]:
    """按工位汇总窗口内已落库且已结束的周期。

    工位归属来自 ``DetectionSession.channel_id``；进行中周期的 ``end_time``
    为 NULL，因此不会进入统计。

    Context: 仅由短信汇总后台线程或测试调用；不持有检测状态锁；允许短暂执行
             只读聚合查询，禁止从检测帧循环直接调用。
    """

    from sqlalchemy import func

    from backend.db.database import SessionLocal
    from backend.models.models import DetectionCycle, DetectionSession

    db = SessionLocal()
    try:
        rows = (
            db.query(
                DetectionSession.channel_id,
                DetectionCycle.is_good,
                func.count(DetectionCycle.id),
            )
            .join(
                DetectionSession,
                DetectionCycle.session_id == DetectionSession.id,
            )
            .filter(
                DetectionCycle.end_time.isnot(None),
                DetectionCycle.end_time >= window_start,
                DetectionCycle.end_time < window_end,
            )
            .group_by(DetectionSession.channel_id, DetectionCycle.is_good)
            .all()
        )
    finally:
        db.close()

    counts: dict[int, SmsSummaryCounts] = {}
    for raw_channel, is_good, raw_count in rows:
        channel_id = int(raw_channel or 0)
        current = counts.get(channel_id, SmsSummaryCounts())
        amount = int(raw_count or 0)
        if is_good is True:
            counts[channel_id] = replace(current, ok_count=current.ok_count + amount)
        elif is_good is False:
            counts[channel_id] = replace(current, ng_count=current.ng_count + amount)
    return counts


class SmsSummaryStateStore:
    """以原子 JSON 替换保存窗口水位，不依赖灯塔或 MES 配置。"""

    def __init__(
        self,
        path: str | Path,
        *,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.path = Path(path)
        self._logger = logger or (lambda _message: None)
        self._lock = threading.RLock()

    def load_or_create(self, now: datetime) -> SmsSummaryState:
        """读取原窗口；缺失或损坏时以本次服务启动时刻新开窗口。"""

        normalized_now = _normalize_datetime(now)
        with self._lock:
            if self.path.exists():
                try:
                    payload = json.loads(self.path.read_text(encoding="utf-8"))
                    return self._from_dict(payload)
                except Exception as exc:
                    self._logger(
                        f"短信汇总状态无效，已从当前时刻新开窗口：{type(exc).__name__}"
                    )
            state = SmsSummaryState(window_start=normalized_now)
            self.save(state)
            return state

    def save(self, state: SmsSummaryState) -> None:
        """原子写入状态，避免进程中断留下半份 JSON。

        Context: 由短信汇总线程或服务初始化线程调用；持有本存储实例锁；仅做
                 小型 JSON 的本地磁盘同步写，不得从检测帧循环调用。
        """

        payload = {
            "version": 1,
            "window_start": _normalize_datetime(state.window_start).isoformat(
                timespec="seconds"
            ),
            "last_finalized_window_id": state.last_finalized_window_id,
            "queued_window_id": state.queued_window_id,
            "queued_channels": sorted(set(state.queued_channels)),
        }
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    newline="\n",
                    dir=self.path.parent,
                    prefix=".sms_summary_state_",
                    suffix=".tmp",
                    delete=False,
                ) as temp_file:
                    temp_path = temp_file.name
                    temp_file.write(serialized)
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                os.replace(temp_path, self.path)
            finally:
                if temp_path:
                    try:
                        Path(temp_path).unlink(missing_ok=True)
                    except OSError:
                        pass

    @staticmethod
    def _from_dict(payload: object) -> SmsSummaryState:
        if not isinstance(payload, dict):
            raise ValueError("状态根节点必须是对象")
        window_start = _normalize_datetime(
            datetime.fromisoformat(str(payload["window_start"]))
        )
        raw_channels = payload.get("queued_channels", [])
        if not isinstance(raw_channels, list):
            raise ValueError("queued_channels 必须是数组")
        queued_channels = tuple(sorted({int(value) for value in raw_channels}))
        return SmsSummaryState(
            window_start=window_start,
            last_finalized_window_id=str(
                payload.get("last_finalized_window_id", "") or ""
            ),
            queued_window_id=str(payload.get("queued_window_id", "") or ""),
            queued_channels=queued_channels,
        )


def _normalize_datetime(value: datetime) -> datetime:
    """统一为本地无时区时间，与现有 SQLite ``end_time`` 写入口径一致。"""

    if value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value.replace(tzinfo=None)


__all__ = [
    "SMS_SUMMARY_STATE_FILENAME",
    "SUMMARY_WINDOW_SECONDS",
    "SmsSummaryCounts",
    "SmsSummaryState",
    "SmsSummaryStateStore",
    "load_completed_cycle_counts",
    "summary_window_id",
]
