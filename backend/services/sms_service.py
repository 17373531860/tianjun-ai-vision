"""主程序 12 小时滚动汇总短信门面。

已结算周期由独立后台调度按工位查询、汇总并非阻塞入队；USB/AT 或
HTTP/HTTPS I/O、重试与离线补发全部留在发送 worker。短信通道独立于
检测热路径、灯塔 Modbus 与 MES。
"""

from __future__ import annotations

import hashlib
import json
import queue
import string
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from backend.core.config import DATA_DIR
from backend.services.sms_at_client import SerialConfig
from backend.services.sms_modem import DiagnosticReport, SmsModem
from backend.services.sms_offline_queue import SmsOfflineQueue
from backend.services.sms_providers import create_provider
from backend.services.sms_providers.base_provider import (
    RecipientResult,
    SmsBatchResult,
    SmsProvider,
)
from backend.services.sms_providers.wxpusher_provider import wxpusher_target_labels
from backend.services.sms_summary import (
    SMS_SUMMARY_STATE_FILENAME,
    SUMMARY_WINDOW_SECONDS,
    SmsSummaryCounts,
    SmsSummaryState,
    SmsSummaryStateStore,
    is_aligned_shift_start,
    load_completed_cycle_counts,
    load_panel_session_snapshots,
    next_shift_window_start,
    open_daily_shift_window_start,
    shift_window_end,
    summary_window_id,
)
from backend.services.sms_utils import (
    choose_encoding,
    parse_recipients,
    validate_message,
)


LEGACY_ALARM_TEMPLATE = "【天军AI视觉】{time} {event_name}：{message}"
DEFAULT_SUMMARY_TEMPLATE = (
    "【天军AI视觉】{device_name} {time_range} OK{ok_count} NG{ng_count}"
)
# 兼容一期 import 名；默认语义已切换为 12 小时汇总模板。
DEFAULT_ALARM_TEMPLATE = DEFAULT_SUMMARY_TEMPLATE
DEFAULT_TEST_MESSAGE = "【天军AI视觉】短信通道测试，收到此短信说明当前配置可发送。"
SMS_OFFLINE_QUEUE_FILENAME = "sms_offline_queue.db"
WORKSTATION_CONFIG_FILENAME = "workstation_config.json"
MERGED_SUMMARY_STATE_SENTINEL = -1


def validate_alarm_template(template: str) -> None:
    """限制模板为简单字段占位，拒绝属性访问、转换和格式表达式。"""

    if not template or not template.strip():
        raise ValueError("短信模板不能为空")
    try:
        for _literal, field_name, format_spec, conversion in string.Formatter().parse(
            template
        ):
            if field_name is None:
                continue
            if not field_name.isidentifier() or format_spec or conversion:
                raise ValueError(
                    f"不支持的模板表达式 {{{field_name}}}；只允许简单字段名"
                )
    except (ValueError, AttributeError) as exc:
        if str(exc).startswith("不支持的模板表达式"):
            raise
        raise ValueError(f"短信模板格式错误：{exc}") from exc


@dataclass(frozen=True)
class SmsServiceConfig:
    """多通道不可变运行时配置；保留 AT 平铺字段兼容一期调用方。"""

    enabled: bool = False
    provider: Literal[
        "at_modem", "generic_http", "wxpusher", "aliyun", "tencent"
    ] = "at_modem"

    # at_modem（一期兼容字段）
    port: str = ""
    baudrate: int = 115200
    recipients: tuple[str, ...] = ()
    template: str = DEFAULT_ALARM_TEMPLATE
    encoding: str = "auto"

    # generic_http
    api_url: str = ""
    request_method: str = "POST"
    timeout_seconds: float = 10.0
    token: str = ""
    access_key: str = ""
    access_secret: str = ""
    sign_name: str = ""
    template_id: str = ""
    verify_ssl: bool = True
    field_mapping: Mapping[str, str] = field(default_factory=dict)

    # wxpusher（第三通道；字段独立，不复用云短信 token/url）
    wxpusher_app_token: str = ""
    wxpusher_uids: tuple[str, ...] = ()
    wxpusher_topic_ids: tuple[int, ...] = ()
    wxpusher_content_type: int = 1
    wxpusher_summary_template: str = (
        "【天军AI视觉】{time_range} OK={ok_count} NG={ng_count}"
    )
    wxpusher_api_url: str = "https://wxpusher.zjiecode.com/api/send/message"
    wxpusher_timeout_seconds: float = 10.0
    wxpusher_verify_ssl: bool = True

    # aliyun（官方云短信；审核签名 + 审核模板 + 命名变量）
    aliyun_access_key_id: str = ""
    aliyun_access_key_secret: str = ""
    aliyun_sign_name: str = ""
    aliyun_template_code: str = ""
    aliyun_region: str = "cn-hangzhou"

    # tencent（官方云短信；审核签名 + 审核模板 + 位置变量）
    tencent_secret_id: str = ""
    tencent_secret_key: str = ""
    tencent_sdk_app_id: str = ""
    tencent_sign_name: str = ""
    tencent_template_id: str = ""
    tencent_region: str = "ap-guangzhou"

    # 各通道共用
    retries: int = 3
    retry_delay_seconds: float = 2.0
    retry_backoff_seconds: tuple[float, ...] = (1.0, 3.0, 5.0)
    # 旧配置兼容字段；12 小时汇总模式不再读取此值。
    ng_threshold: int = 5
    cooldown_seconds: float = 60.0
    queue_size: int = 100
    offline_queue_max: int = 200
    offline_ttl_seconds: float = 86_400.0

    # 汇总调度：默认滚动 12 小时；可选班次（如早八～晚八每天一条）
    summary_schedule_mode: Literal["rolling_12h", "daily_shift"] = "rolling_12h"
    shift_start_hour: int = 8
    shift_end_hour: int = 20
    send_night_window: bool = False
    # 到点发送的数字口径：panel=监控面板当前会话（默认）；window=调度时间窗落库合计
    summary_count_source: Literal["panel", "window"] = "panel"
    # 汇总组包：merged_detail=一条内分列全部参与工位；per_channel=旧版逐工位多条。
    summary_send_mode: Literal["per_channel", "merged_detail"] = "merged_detail"
    # 空元组表示全部启用工位；非空时只汇总指定的 0-based channel id。
    summary_channel_ids: tuple[int, ...] = ()

    def validated_recipients(
        self, override: Sequence[str] | str | None = None
    ) -> tuple[str, ...]:
        source: Sequence[str] | str = (
            override if override is not None else self.recipients
        )
        return parse_recipients(source)


def _integer_rate(numerator: int, denominator: int) -> str:
    """用整数百分比返回模板安全字符串；分母 0 固定为 ``"0"``。"""

    if denominator <= 0:
        return "0"
    return str(int((max(0, numerator) * 100 / denominator) + 0.5))


@dataclass(frozen=True)
class SummaryChannelMetrics:
    """统一汇总载荷中的单工位指标。"""

    channel_id: int
    ok_count: int = 0
    ng_count: int = 0

    @property
    def total(self) -> int:
        return max(0, int(self.ok_count)) + max(0, int(self.ng_count))

    @property
    def ok_rate(self) -> str:
        return _integer_rate(int(self.ok_count), self.total)

    @property
    def ng_rate(self) -> str:
        return _integer_rate(int(self.ng_count), self.total)

    def to_context(self) -> dict[str, object]:
        return {
            "channel_id": int(self.channel_id),
            "device_name": f"工位{int(self.channel_id) + 1}",
            "ok_count": max(0, int(self.ok_count)),
            "ng_count": max(0, int(self.ng_count)),
            "total": self.total,
            "ok_rate": self.ok_rate,
            "ng_rate": self.ng_rate,
        }


@dataclass(frozen=True)
class SummaryPayload:
    """Provider 无关的多工位汇总载荷、正文与命名变量。"""

    window_start: datetime
    window_end: datetime
    channels: tuple[SummaryChannelMetrics, ...]
    is_test_snapshot: bool = False

    @property
    def total_ok(self) -> int:
        return sum(max(0, int(item.ok_count)) for item in self.channels)

    @property
    def total_ng(self) -> int:
        return sum(max(0, int(item.ng_count)) for item in self.channels)

    @property
    def total(self) -> int:
        return self.total_ok + self.total_ng

    @property
    def ok_rate(self) -> str:
        return _integer_rate(self.total_ok, self.total)

    @property
    def ng_rate(self) -> str:
        return _integer_rate(self.total_ng, self.total)

    @property
    def time_range(self) -> str:
        return (
            f"{self.window_start.strftime('%Y-%m-%d %H:%M')}~"
            f"{self.window_end.strftime('%Y-%m-%d %H:%M')}"
        )

    @property
    def device_name(self) -> str:
        return "+".join(f"工位{item.channel_id + 1}" for item in self.channels)

    def template_params(self) -> dict[str, str]:
        params: dict[str, str] = {
            "time_range": self.time_range,
            "device_name": self.device_name,
        }
        for item in self.channels:
            prefix = f"ch{int(item.channel_id) + 1}"
            params.update(
                {
                    f"{prefix}_ok": str(max(0, int(item.ok_count))),
                    f"{prefix}_ng": str(max(0, int(item.ng_count))),
                    f"{prefix}_total": str(item.total),
                    f"{prefix}_ok_rate": item.ok_rate,
                    f"{prefix}_ng_rate": item.ng_rate,
                }
            )
        params.update(
            {
                "total_ok": str(self.total_ok),
                "total_ng": str(self.total_ng),
                "total": str(self.total),
                "ok_rate": self.ok_rate,
                "ng_rate": self.ng_rate,
            }
        )
        return params

    def to_context(self, *, include_template_params: bool = True) -> dict[str, object]:
        context: dict[str, object] = {
            "device_name": self.device_name,
            "time_range": self.time_range,
            "channels": [item.to_context() for item in self.channels],
            "total_ok": self.total_ok,
            "total_ng": self.total_ng,
            "total": self.total,
            "ok_rate": self.ok_rate,
            "ng_rate": self.ng_rate,
            # 兼容 WxPusher 摘要和 Generic HTTP 既有总数字段。
            "ok_count": self.total_ok,
            "ng_count": self.total_ng,
            "window_id": summary_window_id(self.window_start, self.window_end),
            "event_time": self.window_end.strftime("%Y-%m-%d %H:%M:%S"),
            "is_test_snapshot": self.is_test_snapshot,
        }
        if len(self.channels) == 1:
            context["channel_id"] = self.channels[0].channel_id
        if include_template_params:
            context["template_params"] = self.template_params()
        return context

    def render_message(self) -> str:
        details = "；".join(
            f"工位{item.channel_id + 1} OK{max(0, int(item.ok_count))} "
            f"NG{max(0, int(item.ng_count))} 合格{item.ok_rate}% NG率{item.ng_rate}%"
            for item in self.channels
        )
        total = (
            f"；合计 OK{self.total_ok} NG{self.total_ng} "
            f"合格{self.ok_rate}% NG率{self.ng_rate}%"
            if len(self.channels) > 1
            else ""
        )
        prefix = "【非完整窗口】" if self.is_test_snapshot else "【天军AI视觉】"
        return f"{prefix}{self.time_range} {details}{total}".strip()


@dataclass(frozen=True)
class AlarmQueueReceipt:
    """``send_alarm`` 的即时结构化回执，不代表短信最终送达。"""

    # 一期兼容字段
    accepted: bool
    status: str
    detail: str
    job_id: str = ""

    # 双通道统一字段
    success: bool | None = None
    message: str = ""
    message_id: str = ""
    status_code: int | None = None
    retry_count: int = 0
    queued: bool | None = None
    error_code: str = ""

    def __post_init__(self) -> None:
        if self.success is None:
            object.__setattr__(self, "success", self.accepted)
        if not self.message:
            object.__setattr__(self, "message", self.detail)
        if not self.message_id:
            object.__setattr__(self, "message_id", self.job_id)
        if self.queued is None:
            object.__setattr__(
                self, "queued", self.accepted and self.status == "queued"
            )
        if self.status_code is None:
            object.__setattr__(self, "status_code", 202 if self.accepted else 400)


@dataclass(frozen=True)
class _AlarmJob:
    job_id: str
    dedup_key: str
    event_id: str
    event_name: str
    raw_message: str
    message: str
    recipients: tuple[str, ...]
    context: Mapping[str, object]
    callback: Callable[[SmsBatchResult], None] | None = field(
        compare=False, default=None
    )


class SmsService:
    """同一门面下按 ``provider`` 选择 AT / generic_http / wxpusher。"""

    def __init__(
        self,
        config: SmsServiceConfig,
        *,
        logger: Callable[[str], None] | None = None,
        serial_factory: Callable[..., object] | None = None,
        http_request: Callable[..., object] | None = None,
        offline_queue_path: str | Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = datetime.now,
        summary_state_path: str | Path | None = None,
        summary_reader: Callable[
            [datetime, datetime], dict[int, SmsSummaryCounts]
        ] = load_completed_cycle_counts,
        summary_poll_seconds: float = 60.0,
        workstation_config_path: str | Path | None = None,
    ) -> None:
        self.config = config
        self._logger = logger or (lambda _message: None)
        self._serial_factory = serial_factory
        self._http_request = http_request
        self._clock = clock
        self._wall_clock = wall_clock
        self._summary_reader = summary_reader
        self._summary_poll_seconds = max(0.1, float(summary_poll_seconds))
        self._workstation_config_path = (
            Path(workstation_config_path)
            if workstation_config_path
            else Path(DATA_DIR) / WORKSTATION_CONFIG_FILENAME
        )
        self._offline_queue_path = (
            Path(offline_queue_path)
            if offline_queue_path
            else (Path(DATA_DIR) / SMS_OFFLINE_QUEUE_FILENAME)
        )
        self._offline_queue: SmsOfflineQueue | None = None
        self._provider: SmsProvider | None = None
        self._provider_job_context: _AlarmJob | None = None
        self._queue: queue.Queue[_AlarmJob] = queue.Queue(
            maxsize=max(1, config.queue_size)
        )
        self._last_enqueued: dict[str, float] = {}
        self._state_lock = threading.RLock()
        self._summary_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._summary_thread: threading.Thread | None = None
        self._accepting = True
        self._job_counter = 0
        state_path = (
            Path(summary_state_path)
            if summary_state_path
            else (Path(DATA_DIR) / SMS_SUMMARY_STATE_FILENAME)
        )
        self._summary_store = SmsSummaryStateStore(state_path, logger=self._logger)
        # 路由清单、OpenAPI 生成器和单测都会 import 本模块；只在显式启动调度
        # 或主动调用汇总/测试快照时建立窗口，避免“仅导入”被误算为后端启动。
        self._summary_state: SmsSummaryState | None = None

        # 默认关闭不建库、不起线程。只有已启用 HTTP 类通道且有历史欠账时才自动补发。
        if self.config.enabled and self._supports_offline_queue():
            offline = self._get_offline_queue()
            if offline.count() > 0:
                with self._state_lock:
                    self._ensure_worker_locked()

    def diagnose_modem(self) -> DiagnosticReport:
        """同步 AT 诊断；调用方必须放到非 UI/推理线程。"""

        if self.config.provider != "at_modem":
            raise ValueError("当前通道不是 USB/AT，无法执行模块诊断")
        modem = self._new_modem()
        try:
            return modem.diagnose()
        finally:
            modem.client.close()

    def send_test_sms(
        self, recipients: Sequence[str] | str | None, message: str
    ) -> SmsBatchResult:
        """同步发送测试短信，保留给离线工具/后端测试；API 使用后台队列。"""

        self._validate_provider_configuration()
        normalized = self._resolve_delivery_targets(recipients)
        if self.config.provider == "at_modem":
            validate_message(message, choose_encoding(message, self.config.encoding))
        elif len(message) > 10_000:
            raise ValueError("推送内容过长")
        job = _AlarmJob(
            job_id=f"sms-test-{int(time.time())}",
            dedup_key="test",
            event_id="test",
            event_name="短信通道测试",
            raw_message=message,
            message=message,
            recipients=normalized,
            context={},
        )
        self._provider_job_context = job
        try:
            return self._send_batch(normalized, message)
        finally:
            self._provider_job_context = None

    def queue_test_sms(
        self,
        *,
        message: str | None = None,
        recipients: Sequence[str] | str | None = None,
    ) -> AlarmQueueReceipt:
        """发送当前未闭合窗口快照；显式自定义文案仅用于旧调用方兼容。"""

        context: dict[str, object] = {"device_name": "短信配置测试"}
        if message is None:
            try:
                message, context = self._current_window_snapshot()
            except Exception as exc:
                self._logger(
                    f"短信汇总测试快照读取失败（已隔离）：{type(exc).__name__}"
                )
                return self._reject(
                    "snapshot_failed",
                    "当前未闭合窗口快照读取失败",
                    "SMS_SUMMARY_SNAPSHOT_FAILED",
                    500,
                )

        return self._queue_message(
            event_id="test",
            event_name="12小时汇总测试",
            message=message,
            recipients=recipients,
            context=context,
            callback=None,
            bypass_enabled=True,
            bypass_cooldown=True,
        )

    def send_alarm(
        self,
        *,
        event_id: int | str,
        event_name: str,
        message: str = "",
        recipients: Sequence[str] | str | None = None,
        context: Mapping[str, object] | None = None,
        callback: Callable[[SmsBatchResult], None] | None = None,
    ) -> AlarmQueueReceipt:
        """一期即时告警兼容入口；12 小时汇总模式固定拒绝即时发送。"""

        return self._reject(
            "summary_only",
            "短信已切换为每工位滚动 12 小时汇总，单次事件不再即时发送",
            "SMS_SUMMARY_ONLY",
            200,
        )

    def queue_summary_sms(
        self,
        *,
        channel_id: int,
        window_start: datetime,
        window_end: datetime,
        ok_count: int,
        ng_count: int,
        callback: Callable[[SmsBatchResult], None] | None = None,
    ) -> AlarmQueueReceipt:
        """把一个工位的完整闭合窗口汇总送入现有 Provider 队列。

        Context: 由短信汇总线程调用；不持有检测/MES 锁；只做校验和
                 ``put_nowait``，Provider I/O 留在发送 worker。
        """

        payload = SummaryPayload(
            window_start=window_start,
            window_end=window_end,
            channels=(
                SummaryChannelMetrics(
                    channel_id=int(channel_id),
                    ok_count=int(ok_count),
                    ng_count=int(ng_count),
                ),
            ),
        )
        device_name = payload.device_name
        time_range = payload.time_range
        message = (
            f"{device_name}在{time_range}内合格{int(ok_count)}次，"
            f"不合格{int(ng_count)}次，请关注生产状况。"
        )
        return self._queue_message(
            event_id="summary_12h",
            event_name="12小时生产汇总",
            message=message,
            recipients=None,
            # 旧 per_channel 的阿里云三变量 fallback 保持不变。
            context=payload.to_context(include_template_params=False),
            callback=callback,
            bypass_enabled=False,
            # 每个窗口只会由持久化 window_id 入队一次，不再叠加即时告警冷却。
            bypass_cooldown=True,
        )

    def queue_merged_summary(self, payload: SummaryPayload) -> AlarmQueueReceipt:
        """把 Provider 无关的多工位载荷作为单个任务入队。"""

        context = payload.to_context()
        context["use_rendered_summary_message"] = True
        return self._queue_message(
            event_id="summary_12h",
            event_name="12小时生产汇总",
            message=payload.render_message(),
            recipients=None,
            context=context,
            callback=None,
            bypass_enabled=False,
            bypass_cooldown=True,
        )

    def start_summary_scheduler(self, *, reset_rolling_anchor: bool = False) -> None:
        """幂等启动滚动汇总线程；由主程序 API 生命周期显式调用。

        ``reset_rolling_anchor=True`` 仅用于**后端进程冷启动**：滚动 12h 模式
        把水位锚到本次服务启动时刻，与界面文案一致。配置热替换必须保持默认
        ``False``，以免改通道/模板时把正在滚的窗掐断重开。

        Context: 在后端初始化或配置热替换线程调用；先加载/创建小型水位 JSON，
                 再启动 daemon thread；此方法本身不查询周期库、不发送短信。
        """

        with self._summary_lock:
            if (
                reset_rolling_anchor
                and self.config.summary_schedule_mode == "rolling_12h"
            ):
                self._reset_rolling_anchor_locked()
            else:
                self._ensure_summary_state_locked()
        with self._state_lock:
            if not self._accepting:
                return
            if self._summary_thread is not None and self._summary_thread.is_alive():
                return
            self._summary_thread = threading.Thread(
                target=self._summary_loop,
                name="sms-12h-summary",
                daemon=True,
            )
            self._summary_thread.start()

    def _reset_rolling_anchor_locked(self) -> None:
        """滚动 12h：丢弃跨进程遗留水位，以当前墙钟为新窗起点。"""

        now = self._normalize_datetime(self._wall_clock())
        state = SmsSummaryState(window_start=now)
        self._summary_store.save(state)
        self._summary_state = state
        self._logger(
            "滚动12小时汇总已锚定到服务启动时刻 "
            f"{now.isoformat(timespec='seconds')}"
        )

    @property
    def summary_scheduler_running(self) -> bool:
        """返回本实例的汇总调度线程是否正在运行。"""

        with self._state_lock:
            return self._summary_thread is not None and self._summary_thread.is_alive()

    def run_due_summaries(self, now: datetime | None = None) -> int:
        """处理已到点窗口，返回本次成功推进的窗口数；供调度线程和单测复用。

        Context: 生产环境只由短信汇总线程调用；持有汇总锁；允许只读 DB 和小型
                 状态 JSON I/O，不进入检测、MES 或 uvicorn 请求热路径。
        """

        current = self._normalize_datetime(now or self._wall_clock())
        with self._summary_lock:
            self._ensure_summary_state_locked()
            self._maybe_realign_daily_shift_state_locked(current)
            if self.config.summary_schedule_mode == "daily_shift":
                return self._run_due_daily_shift_summaries_locked(current)
            return self._run_due_rolling_summaries_locked(current)

    def _run_due_rolling_summaries_locked(self, current: datetime) -> int:
        state = self._ensure_summary_state_locked()
        elapsed = (current - state.window_start).total_seconds()
        due_count = int(elapsed // SUMMARY_WINDOW_SECONDS)
        if due_count <= 0:
            return 0

        if not self.config.enabled:
            # 关闭期间不查周期、不发送，也不在重新开启后追发历史窗口。
            new_start = state.window_start + timedelta(
                seconds=due_count * SUMMARY_WINDOW_SECONDS
            )
            last_start = new_start - timedelta(seconds=SUMMARY_WINDOW_SECONDS)
            skipped_id = summary_window_id(last_start, new_start)
            advanced = SmsSummaryState(
                window_start=new_start,
                last_finalized_window_id=skipped_id,
            )
            self._summary_store.save(advanced)
            self._summary_state = advanced
            self._logger(f"短信汇总已关闭，跳过并推进 {due_count} 个窗口")
            return due_count

        advanced_count = 0
        # 防止长期停机后单次占满调度线程；余下窗口下一轮继续处理。
        for _index in range(min(due_count, 100)):
            state = self._ensure_summary_state_locked()
            window_end = state.window_start + timedelta(
                seconds=SUMMARY_WINDOW_SECONDS
            )
            if current < window_end:
                break
            if not self._process_summary_window(state.window_start, window_end):
                break
            advanced_count += 1
        return advanced_count

    def _run_due_daily_shift_summaries_locked(self, current: datetime) -> int:
        advanced_count = 0
        for _index in range(100):
            state = self._ensure_summary_state_locked()
            try:
                window_end = shift_window_end(
                    state.window_start,
                    start_hour=self.config.shift_start_hour,
                    end_hour=self.config.shift_end_hour,
                )
            except ValueError:
                self._maybe_realign_daily_shift_state_locked(current, force=True)
                continue
            if current < window_end:
                break
            next_start = next_shift_window_start(
                window_end,
                start_hour=self.config.shift_start_hour,
                end_hour=self.config.shift_end_hour,
                send_night_window=self.config.send_night_window,
            )
            if not self.config.enabled:
                skipped_id = summary_window_id(state.window_start, window_end)
                advanced = SmsSummaryState(
                    window_start=next_start,
                    last_finalized_window_id=skipped_id,
                )
                self._summary_store.save(advanced)
                self._summary_state = advanced
                self._logger("短信汇总已关闭，跳过并推进 1 个班次窗口")
                advanced_count += 1
                continue
            if not self._process_summary_window(
                state.window_start,
                window_end,
                next_window_start=next_start,
            ):
                break
            advanced_count += 1
        return advanced_count

    def _maybe_realign_daily_shift_state_locked(
        self, now: datetime, *, force: bool = False
    ) -> None:
        if self.config.summary_schedule_mode != "daily_shift":
            return
        state = self._ensure_summary_state_locked()
        aligned = is_aligned_shift_start(
            state.window_start,
            start_hour=self.config.shift_start_hour,
            end_hour=self.config.shift_end_hour,
        )
        night_parked = (
            not self.config.send_night_window
            and state.window_start.hour == self.config.shift_end_hour
        )
        if aligned and not force and not night_parked:
            return
        snapped = open_daily_shift_window_start(
            now,
            start_hour=self.config.shift_start_hour,
            end_hour=self.config.shift_end_hour,
            send_night_window=self.config.send_night_window,
        )
        if snapped == state.window_start:
            return
        realigned = SmsSummaryState(
            window_start=snapped,
            last_finalized_window_id=state.last_finalized_window_id,
            queued_window_id="",
            queued_channels=(),
        )
        self._summary_store.save(realigned)
        self._summary_state = realigned
        self._logger(
            f"班次汇总水位已对齐到 {snapped.isoformat(timespec='seconds')}"
        )

    def shutdown(self, timeout: float = 5.0) -> None:
        """停止汇总调度与新任务接收，有限等待当前发送。

        Context: 由 Electron/进程退出或配置热替换线程调用；短暂持有服务状态锁；
                 最多等待 ``timeout`` 秒，不持有检测或 MES 锁。
        """

        deadline = time.monotonic() + max(0.0, timeout)
        with self._state_lock:
            self._accepting = False
            worker = self._worker
            summary_thread = self._summary_thread
            self._stop_event.set()
        self._discard_queued_jobs()
        if summary_thread is not None and summary_thread.is_alive():
            summary_thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if worker is not None and worker.is_alive():
            worker.join(timeout=max(0.0, deadline - time.monotonic()))
        if worker is not None and worker.is_alive():
            provider = self._provider
            if provider is not None:
                provider.shutdown()
                self._logger("短信 worker 有限等待到期，已中断当前 Provider")
            else:
                self._logger("短信 worker 有限等待到期；当前没有活动 Provider")

    def _summary_loop(self) -> None:
        """轮询滚动窗口并隔离汇总异常。

        Context: 仅运行在 ``sms-12h-summary`` daemon thread；不持有检测/MES 锁；
                 等待可被 ``_stop_event`` 立即唤醒。
        """

        while not self._stop_event.is_set():
            try:
                self.run_due_summaries()
            except Exception as exc:
                self._logger(f"短信汇总调度异常（已隔离）：{type(exc).__name__}")
            with self._summary_lock:
                state = self._ensure_summary_state_locked()
                self._maybe_realign_daily_shift_state_locked(
                    self._normalize_datetime(self._wall_clock())
                )
                state = self._ensure_summary_state_locked()
                if self.config.summary_schedule_mode == "daily_shift":
                    try:
                        next_end = shift_window_end(
                            state.window_start,
                            start_hour=self.config.shift_start_hour,
                            end_hour=self.config.shift_end_hour,
                        )
                    except ValueError:
                        next_end = state.window_start + timedelta(hours=1)
                else:
                    next_end = state.window_start + timedelta(
                        seconds=SUMMARY_WINDOW_SECONDS
                    )
            seconds_to_due = max(
                0.1,
                (
                    next_end - self._normalize_datetime(self._wall_clock())
                ).total_seconds(),
            )
            self._stop_event.wait(min(self._summary_poll_seconds, seconds_to_due))

    def _process_summary_window(
        self,
        window_start: datetime,
        window_end: datetime,
        *,
        next_window_start: datetime | None = None,
    ) -> bool:
        """读取、逐工位入队并落盘一个闭合窗口的进度。

        Context: 仅由短信汇总线程在持有 ``_summary_lock`` 时调用；数据库读取和
                 状态落盘允许短暂阻塞该调度线程，不阻塞检测线程。
        """

        window_id = summary_window_id(window_start, window_end)
        try:
            if self.config.summary_count_source == "panel":
                snapshots = load_panel_session_snapshots(as_of=window_end)
                channel_payloads = {
                    int(channel_id): (
                        snap.counts,
                        snap.range_start,
                        snap.range_end,
                    )
                    for channel_id, snap in snapshots.items()
                }
            else:
                counts_by_channel = self._summary_reader(window_start, window_end)
                channel_payloads = {
                    int(channel_id): (counts, window_start, window_end)
                    for channel_id, counts in counts_by_channel.items()
                }
        except Exception as exc:
            self._logger(
                f"短信汇总窗口 {window_id} 查询失败（稍后重试）：{type(exc).__name__}"
            )
            return False

        state = self._ensure_summary_state_locked()
        queued = (
            set(state.queued_channels) if state.queued_window_id == window_id else set()
        )
        effective_mode = self.config.summary_send_mode
        if queued:
            # 热切换配置时继续完成已经开始的旧模式，避免同窗混发或重发。
            effective_mode = (
                "merged_detail"
                if MERGED_SUMMARY_STATE_SENTINEL in queued
                else "per_channel"
            )

        if effective_mode == "merged_detail":
            payload = self._build_summary_payload(
                channel_payloads,
                default_start=window_start,
                default_end=window_end,
            )
            if payload.total > 0 and MERGED_SUMMARY_STATE_SENTINEL not in queued:
                receipt = self.queue_merged_summary(payload)
                if not receipt.queued:
                    self._logger(
                        f"短信汇总窗口 {window_id} 合并消息未入队："
                        f"{receipt.error_code or receipt.status}；稍后重试"
                    )
                    return False
                queued = {MERGED_SUMMARY_STATE_SENTINEL}
                partial = replace(
                    state,
                    queued_window_id=window_id,
                    queued_channels=(MERGED_SUMMARY_STATE_SENTINEL,),
                )
                self._summary_store.save(partial)
                self._summary_state = partial
                state = partial
        else:
            channel_payloads = {
                channel_id: item
                for channel_id, item in channel_payloads.items()
                if item[0].total > 0
                and channel_id in self._resolve_summary_channel_ids(channel_payloads)
            }
            for channel_id in sorted(channel_payloads):
                if channel_id in queued:
                    continue
                counts, msg_start, msg_end = channel_payloads[channel_id]
                receipt = self.queue_summary_sms(
                    channel_id=channel_id,
                    window_start=msg_start,
                    window_end=msg_end,
                    ok_count=counts.ok_count,
                    ng_count=counts.ng_count,
                )
                if not receipt.queued:
                    self._logger(
                        f"短信汇总窗口 {window_id} 工位{channel_id + 1} 未入队："
                        f"{receipt.error_code or receipt.status}；稍后重试"
                    )
                    return False
                queued.add(channel_id)
                partial = replace(
                    state,
                    queued_window_id=window_id,
                    queued_channels=tuple(sorted(queued)),
                )
                self._summary_store.save(partial)
                self._summary_state = partial
                state = partial

        finalized = SmsSummaryState(
            window_start=next_window_start or window_end,
            last_finalized_window_id=window_id,
        )
        self._summary_store.save(finalized)
        self._summary_state = finalized
        sent_count = (
            1
            if effective_mode == "merged_detail" and payload.total > 0
            else len(channel_payloads)
        )
        if sent_count:
            source_label = (
                "面板会话" if self.config.summary_count_source == "panel" else "时间窗"
            )
            self._logger(
                f"短信汇总窗口 {window_id} 已完成（{source_label}），"
                f"{sent_count} 条消息进入后台队列（{effective_mode}）"
            )
        else:
            self._logger(f"短信汇总窗口 {window_id} 无已结算周期，不发送")
        return True

    def _current_window_snapshot(self) -> tuple[str, dict[str, object]]:
        now = self._normalize_datetime(self._wall_clock())
        with self._summary_lock:
            self._ensure_summary_state_locked()
            self._maybe_realign_daily_shift_state_locked(now)
            base_start = self._ensure_summary_state_locked().window_start
        if self.config.summary_count_source == "panel":
            snapshots = load_panel_session_snapshots(as_of=now)
            channel_payloads = {
                int(channel_id): (snap.counts, snap.range_start, snap.range_end)
                for channel_id, snap in snapshots.items()
            }
            default_start, default_end = now, now
        else:
            if self.config.summary_schedule_mode == "daily_shift":
                window_start = base_start
            else:
                elapsed = max(0.0, (now - base_start).total_seconds())
                elapsed_windows = int(elapsed // SUMMARY_WINDOW_SECONDS)
                window_start = base_start + timedelta(
                    seconds=elapsed_windows * SUMMARY_WINDOW_SECONDS
                )
            counts_by_channel = self._summary_reader(window_start, now)
            channel_payloads = {
                int(channel_id): (counts, window_start, now)
                for channel_id, counts in counts_by_channel.items()
            }
            default_start, default_end = window_start, now
        payload = self._build_summary_payload(
            channel_payloads,
            default_start=default_start,
            default_end=default_end,
            is_test_snapshot=True,
        )
        if self.config.summary_send_mode == "merged_detail":
            context = payload.to_context()
            context["use_rendered_summary_message"] = True
            return payload.render_message(), context

        non_empty = [item for item in payload.channels if item.total > 0]
        channel = non_empty[0] if non_empty else payload.channels[0]
        source = channel_payloads.get(channel.channel_id)
        start, end = (source[1], source[2]) if source else (default_start, default_end)
        single = SummaryPayload(
            window_start=start,
            window_end=end,
            channels=(channel,),
            is_test_snapshot=True,
        )
        return single.render_message(), single.to_context(include_template_params=False)

    def _build_summary_payload(
        self,
        channel_payloads: Mapping[int, tuple[SmsSummaryCounts, datetime, datetime]],
        *,
        default_start: datetime,
        default_end: datetime,
        is_test_snapshot: bool = False,
    ) -> SummaryPayload:
        channel_ids = self._resolve_summary_channel_ids(channel_payloads)
        metrics = tuple(
            SummaryChannelMetrics(
                channel_id=channel_id,
                ok_count=(channel_payloads.get(channel_id) or (SmsSummaryCounts(),))[
                    0
                ].ok_count,
                ng_count=(channel_payloads.get(channel_id) or (SmsSummaryCounts(),))[
                    0
                ].ng_count,
            )
            for channel_id in channel_ids
        )
        starts = [
            channel_payloads[channel_id][1]
            for channel_id in channel_ids
            if channel_id in channel_payloads
        ]
        ends = [
            channel_payloads[channel_id][2]
            for channel_id in channel_ids
            if channel_id in channel_payloads
        ]
        return SummaryPayload(
            window_start=min(starts) if starts else default_start,
            window_end=max(ends) if ends else default_end,
            channels=metrics,
            is_test_snapshot=is_test_snapshot,
        )

    def _resolve_summary_channel_ids(
        self, channel_payloads: Mapping[int, object]
    ) -> tuple[int, ...]:
        if self.config.summary_channel_ids:
            return tuple(
                sorted(set(int(value) for value in self.config.summary_channel_ids))
            )

        try:
            raw = json.loads(self._workstation_config_path.read_text(encoding="utf-8"))
            count = int(raw.get("channel_count", 0)) if isinstance(raw, dict) else 0
            if 0 < count <= 64:
                return tuple(range(count))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # 工位配置缺失/损坏不能拖垮短信调度；回退到实际统计 key。
            pass
        channel_ids = {int(value) for value in channel_payloads}
        return tuple(sorted(channel_ids or {0}))

    def _ensure_summary_state_locked(self) -> SmsSummaryState:
        """在持有 ``_summary_lock`` 时惰性加载或创建滚动窗口水位。"""

        if self._summary_state is None:
            self._summary_state = self._summary_store.load_or_create(self._wall_clock())
        return self._summary_state

    @staticmethod
    def _format_time_range(window_start: datetime, window_end: datetime) -> str:
        return (
            f"{window_start.strftime('%Y-%m-%d %H:%M')}~"
            f"{window_end.strftime('%Y-%m-%d %H:%M')}"
        )

    @staticmethod
    def _normalize_datetime(value: datetime) -> datetime:
        if value.tzinfo is not None:
            return value.astimezone().replace(tzinfo=None)
        return value.replace(tzinfo=None)

    def _queue_message(
        self,
        *,
        event_id: int | str,
        event_name: str,
        message: str,
        recipients: Sequence[str] | str | None,
        context: Mapping[str, object] | None,
        callback: Callable[[SmsBatchResult], None] | None,
        bypass_enabled: bool,
        bypass_cooldown: bool,
    ) -> AlarmQueueReceipt:
        try:
            with self._state_lock:
                if not self._accepting:
                    return self._reject(
                        "stopped",
                        "短信服务正在关闭，已停止接收新任务",
                        "SMS_STOPPED",
                        503,
                    )
            if not bypass_enabled and not self.config.enabled:
                return self._reject(
                    "disabled",
                    "NG 短信推送总开关未开启",
                    "SMS_DISABLED",
                    200,
                )
            if self.config.provider == "at_modem" and not self.config.port.strip():
                return self._reject(
                    "not_configured",
                    "未配置短信模块 COM 口",
                    "SMS_NOT_CONFIGURED",
                    400,
                )
            self._validate_provider_configuration()
            normalized = self._resolve_delivery_targets(recipients)
            rendered = self._render_alarm_message(
                event_id=event_id,
                event_name=event_name,
                message=message,
                context=context,
            )
            if self.config.provider == "at_modem":
                validate_message(
                    rendered, choose_encoding(rendered, self.config.encoding)
                )
            elif len(rendered) > 10_000:
                raise ValueError("推送内容过长")
            safe_context = dict(context or {})
            dedup_key = self._dedup_key(event_name, message, safe_context)

            with self._state_lock:
                if not self._accepting:
                    return self._reject(
                        "stopped",
                        "短信服务正在关闭，已停止接收新任务",
                        "SMS_STOPPED",
                        503,
                    )
                now = self._clock()
                last = self._last_enqueued.get(dedup_key)
                if (
                    not bypass_cooldown
                    and last is not None
                    and now - last < max(0.0, self.config.cooldown_seconds)
                ):
                    remain = self.config.cooldown_seconds - (now - last)
                    return self._reject(
                        "cooldown",
                        f"同类告警仍在冷却，约 {max(0, int(remain + 0.999))} 秒后可再次入队",
                        "SMS_COOLDOWN",
                        409,
                    )
                self._ensure_worker_locked()
                self._job_counter += 1
                job_id = f"sms-{int(time.time())}-{self._job_counter}"
                job = _AlarmJob(
                    job_id=job_id,
                    dedup_key=dedup_key,
                    event_id=str(event_id),
                    event_name=event_name or "报警事件",
                    raw_message=message or "请及时到现场确认",
                    message=rendered,
                    recipients=normalized,
                    context=safe_context,
                    callback=callback,
                )
                try:
                    self._queue.put_nowait(job)
                except queue.Full:
                    return self._reject(
                        "queue_full",
                        "短信发送队列已满，本次告警未入队",
                        "SMS_QUEUE_FULL",
                        503,
                    )
                self._last_enqueued[dedup_key] = now

            self._logger(f"告警短信 {job_id} 已入队，接收人 {len(normalized)} 个")
            return AlarmQueueReceipt(
                True,
                "queued",
                "已进入短信异步队列",
                job_id,
                success=True,
                message="已进入短信异步队列",
                message_id=job_id,
                status_code=202,
                retry_count=0,
                queued=True,
                error_code="",
            )
        except Exception as exc:
            self._logger(f"告警短信入队失败（已隔离）：{type(exc).__name__}")
            return self._reject(
                "rejected",
                str(exc),
                "SMS_CONFIG_INVALID",
                400,
            )

    def _supports_offline_queue(self) -> bool:
        return self.config.provider in {"generic_http", "wxpusher", "aliyun", "tencent"}

    def _resolve_delivery_targets(
        self, recipients: Sequence[str] | str | None
    ) -> tuple[str, ...]:
        """按通道解析投递目标：短信用手机号，WxPusher 用 UID/Topic 标签。"""

        if self.config.provider == "wxpusher":
            labels = wxpusher_target_labels(
                self.config.wxpusher_uids, self.config.wxpusher_topic_ids
            )
            if not labels:
                raise ValueError("未配置 WxPusher UID 或 TopicId")
            return labels
        return self.config.validated_recipients(recipients)

    def _validate_provider_configuration(self) -> None:
        if self.config.provider == "at_modem":
            if not self.config.port.strip():
                raise ValueError("未配置短信模块 COM 口")
            return
        if self.config.provider == "generic_http":
            if not self.config.api_url.strip():
                raise ValueError("未配置云短信 API URL")
            has_token = bool(self.config.token.strip())
            has_key_pair = bool(
                self.config.access_key.strip() and self.config.access_secret.strip()
            )
            if not (has_token or has_key_pair):
                raise ValueError("云短信必须配置 token 或 access_key/access_secret")
            return
        if self.config.provider == "wxpusher":
            if not self.config.wxpusher_app_token.strip():
                raise ValueError("未配置 WxPusher appToken")
            if not self.config.wxpusher_uids and not self.config.wxpusher_topic_ids:
                raise ValueError("未配置 WxPusher UID 或 TopicId")
            if not self.config.wxpusher_api_url.strip():
                raise ValueError("未配置 WxPusher API URL")
            return
        if self.config.provider == "aliyun":
            if not (
                self.config.aliyun_access_key_id.strip()
                and self.config.aliyun_access_key_secret.strip()
            ):
                raise ValueError("未配置阿里云 AccessKey")
            if not self.config.aliyun_sign_name.strip():
                raise ValueError("未配置阿里云短信签名")
            if not self.config.aliyun_template_code.strip():
                raise ValueError("未配置阿里云模板 code")
            return
        if self.config.provider == "tencent":
            if not (
                self.config.tencent_secret_id.strip()
                and self.config.tencent_secret_key.strip()
            ):
                raise ValueError("未配置腾讯云 SecretId/SecretKey")
            if not self.config.tencent_sdk_app_id.strip():
                raise ValueError("未配置腾讯云短信应用 SdkAppId")
            if not self.config.tencent_sign_name.strip():
                raise ValueError("未配置腾讯云短信签名")
            if not self.config.tencent_template_id.strip():
                raise ValueError("未配置腾讯云模板 ID")
            return
        raise ValueError(f"不支持的短信 Provider：{self.config.provider}")

    def _new_modem(self) -> SmsModem:
        return SmsModem.from_config(
            SerialConfig(port=self.config.port, baudrate=self.config.baudrate),
            logger=self._logger,
            serial_factory=self._serial_factory,
        )

    def _get_provider(self) -> SmsProvider:
        with self._state_lock:
            if self._provider is not None:
                return self._provider
            provider = create_provider(
                self.config,
                request_func=self._http_request,
                logger=self._logger,
                stop_event=self._stop_event,
                modem_factory=self._new_modem,
            )
            self._provider = provider
            return provider

    def _send_batch(self, recipients: tuple[str, ...], message: str) -> SmsBatchResult:
        job = self._provider_job_context
        return self._get_provider().send(
            recipients,
            message,
            message_id=job.job_id if job else f"sms-sync-{int(time.time())}",
            event_name=job.event_name if job else "短信通道测试",
            raw_message=job.raw_message if job else message,
            context=job.context if job else {},
        )

    def _ensure_worker_locked(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._worker_loop,
            name=f"sms-{self.config.provider}-worker",
            daemon=True,
        )
        self._worker.start()

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                job = self._queue.get(timeout=0.1)
            except queue.Empty:
                self._process_one_offline_job()
                continue
            if self._stop_event.is_set():
                self._queue.task_done()
                break
            try:
                self._provider_job_context = job
                result = self._send_batch(job.recipients, job.message)
                if self._supports_offline_queue() and result.retryable_failure:
                    result = self._persist_offline(job, result)
                self._logger(
                    f"告警短信 {job.job_id} 完成：{result.succeeded}/"
                    f"{len(result.results)} 成功"
                )
                if job.callback is not None:
                    try:
                        job.callback(result)
                    except Exception as exc:
                        self._logger(
                            f"告警短信回调异常（已隔离）：{type(exc).__name__}"
                        )
            except Exception as exc:
                self._logger(
                    f"告警短信 {job.job_id} 后台异常（已隔离）：{type(exc).__name__}"
                )
            finally:
                self._provider_job_context = None
                self._queue.task_done()

    def _persist_offline(
        self, job: _AlarmJob, result: SmsBatchResult
    ) -> SmsBatchResult:
        offline = self._get_offline_queue()
        inserted = offline.enqueue(
            message_id=job.job_id,
            dedup_key=job.dedup_key,
            payload={
                "recipients": list(job.recipients),
                "message": job.message,
                "event_id": job.event_id,
                "event_name": job.event_name,
                "raw_message": job.raw_message,
                "context": dict(job.context),
            },
            next_attempt_at=time.time() + self._offline_retry_delay(0),
        )
        if inserted:
            self._logger(f"云短信 {job.job_id} 已写入离线队列等待补发")
        else:
            self._logger(f"云短信 {job.job_id} 与离线队列现有同键任务重复，未重复写入")
        return replace(result, offline_queued=True)

    def _process_one_offline_job(self) -> None:
        if not self._supports_offline_queue() or not self.config.enabled:
            return
        offline = self._offline_queue
        if offline is None:
            return
        record = offline.next_due(lease_seconds=self._offline_lease_seconds())
        if record is None:
            return
        if self._stop_event.is_set():
            offline.reschedule(
                record.message_id,
                retry_count=record.retry_count,
                next_attempt_at=time.time(),
                last_error="SMS_STOPPED",
            )
            return
        payload = record.payload
        try:
            recipients = parse_recipients(payload.get("recipients", []))
            job = _AlarmJob(
                job_id=record.message_id,
                dedup_key=record.dedup_key,
                event_id=str(payload.get("event_id", "2")),
                event_name=str(payload.get("event_name", "检测异常")),
                raw_message=str(payload.get("raw_message", "请及时处理")),
                message=str(payload.get("message", "")),
                recipients=recipients,
                context=dict(payload.get("context", {}) or {}),
            )
            self._provider_job_context = job
            result = self._send_batch(job.recipients, job.message)
        except Exception as exc:
            self._logger(
                f"离线短信 {record.message_id} 数据异常，已丢弃：{type(exc).__name__}"
            )
            offline.delete(record.message_id)
            return
        finally:
            self._provider_job_context = None
        if result.success:
            offline.delete(record.message_id)
            self._logger(f"离线短信 {record.message_id} 补发成功，已从队列删除")
            return
        if any(item.error_code == "SMS_STOPPED" for item in result.results):
            offline.reschedule(
                record.message_id,
                retry_count=record.retry_count,
                next_attempt_at=time.time(),
                last_error="SMS_STOPPED",
            )
            return
        if not result.retryable_failure:
            offline.delete(record.message_id)
            self._logger(f"离线短信 {record.message_id} 为不可重试错误，已从队列删除")
            return
        retry_count = record.retry_count + 1
        delay = self._offline_retry_delay(record.retry_count)
        error_code = next(
            (item.error_code for item in result.results if item.error_code),
            "HTTP_RETRYABLE",
        )
        offline.reschedule(
            record.message_id,
            retry_count=retry_count,
            next_attempt_at=time.time() + delay,
            last_error=error_code,
        )

    def _offline_retry_delay(self, index: int) -> float:
        backoff = tuple(self.config.retry_backoff_seconds or ()) or (1.0, 3.0, 5.0)
        return max(0.1, float(backoff[min(index, len(backoff) - 1)]))

    def _offline_lease_seconds(self) -> float:
        attempts = max(1, int(self.config.retries) + 1)
        request_budget = attempts * max(0.5, float(self.config.timeout_seconds))
        retry_budget = sum(
            max(0.0, float(value)) for value in self.config.retry_backoff_seconds
        )
        return request_budget + retry_budget + 30.0

    def _get_offline_queue(self) -> SmsOfflineQueue:
        with self._state_lock:
            if self._offline_queue is None:
                self._offline_queue = SmsOfflineQueue(
                    self._offline_queue_path,
                    max_items=self.config.offline_queue_max,
                    ttl_seconds=self.config.offline_ttl_seconds,
                )
            return self._offline_queue

    def _discard_queued_jobs(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return
            else:
                self._queue.task_done()

    def _render_alarm_message(
        self,
        *,
        event_id: int | str,
        event_name: str,
        message: str,
        context: Mapping[str, object] | None,
    ) -> str:
        safe_context = dict(context or {})
        template = self.config.template
        if safe_context.get("is_test_snapshot") is True:
            # 测试快照必须明确标注“非完整窗口”，且避免旧告警模板重复包裹超长。
            template = "{message}"
        elif safe_context.get("use_rendered_summary_message") is True:
            # merged_detail 的正文由统一 SummaryPayload 渲染，内容式 Provider 共用。
            template = "{message}"
        elif str(event_id) == "summary_12h" and template == LEGACY_ALARM_TEMPLATE:
            # 旧 sms_config.json 仍可读；运行时自动改用可在 AT 单条 UCS2 内发送的模板。
            template = DEFAULT_SUMMARY_TEMPLATE
        validate_alarm_template(template)
        values: dict[str, Any] = {
            "event_id": event_id,
            "event_name": event_name or "报警事件",
            "message": message or "请及时到现场确认",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if safe_context:
            for key, value in safe_context.items():
                if key not in values:
                    values[key] = value
        try:
            return template.format_map(values)
        except KeyError as exc:
            raise ValueError(f"短信模板缺少字段值：{exc.args[0]}") from exc

    @staticmethod
    def _dedup_key(
        alarm_type: str,
        alarm_message: str,
        context: Mapping[str, object],
    ) -> str:
        """唯一冷却键：规范化 device_name + alarm_type + alarm_message。

        event_id、provider 和 event_time 不进入键，避免同一工位的同文案 NG 因调用
        入口或发送通道不同绕过冷却；本服务不再叠加第二套冷却状态。
        """

        device = context.get("device_name")
        if device in (None, ""):
            channel = context.get("channel_id")
            device = f"channel-{channel}" if channel is not None else "default"

        def normalize(value: object) -> str:
            return " ".join(str(value or "").strip().lower().split())

        canonical = json.dumps(
            [normalize(device), normalize(alarm_type), normalize(alarm_message)],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _reject(
        status: str,
        detail: str,
        error_code: str,
        status_code: int,
    ) -> AlarmQueueReceipt:
        return AlarmQueueReceipt(
            False,
            status,
            detail,
            success=False,
            message=detail,
            status_code=status_code,
            retry_count=0,
            queued=False,
            error_code=error_code,
        )


__all__ = [
    "AlarmQueueReceipt",
    "DEFAULT_ALARM_TEMPLATE",
    "DEFAULT_SUMMARY_TEMPLATE",
    "DEFAULT_TEST_MESSAGE",
    "LEGACY_ALARM_TEMPLATE",
    "RecipientResult",
    "SmsBatchResult",
    "SmsService",
    "SmsServiceConfig",
    "SummaryChannelMetrics",
    "SummaryPayload",
    "validate_alarm_template",
]
