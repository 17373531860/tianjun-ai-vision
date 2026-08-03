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
from backend.services.sms_providers.at_modem_provider import AtModemProvider
from backend.services.sms_providers.base_provider import RecipientResult, SmsBatchResult
from backend.services.sms_providers.generic_http_provider import GenericHttpProvider
from backend.services.sms_summary import (
    SMS_SUMMARY_STATE_FILENAME,
    SUMMARY_WINDOW_SECONDS,
    SmsSummaryCounts,
    SmsSummaryState,
    SmsSummaryStateStore,
    load_completed_cycle_counts,
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
    """双通道不可变运行时配置；保留 AT 平铺字段兼容一期调用方。"""

    enabled: bool = False
    provider: Literal["at_modem", "generic_http"] = "at_modem"

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

    # 两通道共用
    retries: int = 3
    retry_delay_seconds: float = 2.0
    retry_backoff_seconds: tuple[float, ...] = (1.0, 3.0, 5.0)
    # 旧配置兼容字段；12 小时汇总模式不再读取此值。
    ng_threshold: int = 5
    cooldown_seconds: float = 60.0
    queue_size: int = 100
    offline_queue_max: int = 200
    offline_ttl_seconds: float = 86_400.0

    def validated_recipients(
        self, override: Sequence[str] | str | None = None
    ) -> tuple[str, ...]:
        source: Sequence[str] | str = (
            override if override is not None else self.recipients
        )
        return parse_recipients(source)


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
    """同一门面下按 ``provider`` 选择 AT 或 generic_http。"""

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
    ) -> None:
        self.config = config
        self._logger = logger or (lambda _message: None)
        self._serial_factory = serial_factory
        self._http_request = http_request
        self._clock = clock
        self._wall_clock = wall_clock
        self._summary_reader = summary_reader
        self._summary_poll_seconds = max(0.1, float(summary_poll_seconds))
        self._offline_queue_path = (
            Path(offline_queue_path)
            if offline_queue_path
            else (Path(DATA_DIR) / SMS_OFFLINE_QUEUE_FILENAME)
        )
        self._offline_queue: SmsOfflineQueue | None = None
        self._provider: AtModemProvider | GenericHttpProvider | None = None
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

        # 默认关闭不建库、不起线程。只有已启用 HTTP 且有历史欠账时才自动补发。
        if self.config.enabled and self.config.provider == "generic_http":
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
        self, recipients: Sequence[str] | str, message: str
    ) -> SmsBatchResult:
        """同步发送测试短信，保留给离线工具/后端测试；API 使用后台队列。"""

        normalized = self.config.validated_recipients(recipients)
        self._validate_provider_configuration()
        if self.config.provider == "at_modem":
            validate_message(message, choose_encoding(message, self.config.encoding))
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

        device_name = f"工位{int(channel_id) + 1}"
        time_range = self._format_time_range(window_start, window_end)
        message = (
            f"{device_name}在{time_range}内合格{int(ok_count)}次，"
            f"不合格{int(ng_count)}次，请关注生产状况。"
        )
        return self._queue_message(
            event_id="summary_12h",
            event_name="12小时生产汇总",
            message=message,
            recipients=None,
            context={
                "channel_id": int(channel_id),
                "device_name": device_name,
                "time_range": time_range,
                "ok_count": int(ok_count),
                "ng_count": int(ng_count),
                "window_id": summary_window_id(window_start, window_end),
                "event_time": window_end.strftime("%Y-%m-%d %H:%M:%S"),
            },
            callback=callback,
            bypass_enabled=False,
            # 每个窗口只会由持久化 window_id 入队一次，不再叠加即时告警冷却。
            bypass_cooldown=True,
        )

    def start_summary_scheduler(self) -> None:
        """幂等启动滚动汇总线程；由主程序 API 生命周期显式调用。

        Context: 在后端初始化或配置热替换线程调用；先加载/创建小型水位 JSON，
                 再启动 daemon thread；此方法本身不查询周期库、不发送短信。
        """

        with self._summary_lock:
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
    ) -> bool:
        """读取、逐工位入队并落盘一个闭合窗口的进度。

        Context: 仅由短信汇总线程在持有 ``_summary_lock`` 时调用；数据库读取和
                 状态落盘允许短暂阻塞该调度线程，不阻塞检测线程。
        """

        window_id = summary_window_id(window_start, window_end)
        try:
            counts_by_channel = self._summary_reader(window_start, window_end)
        except Exception as exc:
            self._logger(
                f"短信汇总窗口 {window_id} 查询失败（稍后重试）：{type(exc).__name__}"
            )
            return False

        state = self._ensure_summary_state_locked()
        queued = (
            set(state.queued_channels) if state.queued_window_id == window_id else set()
        )
        non_empty = {
            int(channel_id): counts
            for channel_id, counts in counts_by_channel.items()
            if counts.total > 0
        }
        for channel_id, counts in sorted(non_empty.items()):
            if channel_id in queued:
                continue
            receipt = self.queue_summary_sms(
                channel_id=channel_id,
                window_start=window_start,
                window_end=window_end,
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
            window_start=window_end,
            last_finalized_window_id=window_id,
        )
        self._summary_store.save(finalized)
        self._summary_state = finalized
        if non_empty:
            self._logger(
                f"短信汇总窗口 {window_id} 已完成，{len(non_empty)} 个工位进入后台队列"
            )
        else:
            self._logger(f"短信汇总窗口 {window_id} 无已结算周期，不发送")
        return True

    def _current_window_snapshot(self) -> tuple[str, dict[str, object]]:
        now = self._normalize_datetime(self._wall_clock())
        with self._summary_lock:
            base_start = self._ensure_summary_state_locked().window_start
        elapsed = max(0.0, (now - base_start).total_seconds())
        elapsed_windows = int(elapsed // SUMMARY_WINDOW_SECONDS)
        window_start = base_start + timedelta(
            seconds=elapsed_windows * SUMMARY_WINDOW_SECONDS
        )
        counts_by_channel = self._summary_reader(window_start, now)
        non_empty = sorted(
            (
                (int(channel_id), counts)
                for channel_id, counts in counts_by_channel.items()
                if counts.total > 0
            ),
            key=lambda item: item[0],
        )
        channel_id, counts = non_empty[0] if non_empty else (0, SmsSummaryCounts())
        device_name = f"工位{channel_id + 1}"
        time_range = self._format_time_range(window_start, now)
        message = (
            f"【非完整窗口】{device_name} {time_range} "
            f"OK{counts.ok_count} NG{counts.ng_count}"
        )
        return message, {
            "channel_id": channel_id,
            "device_name": device_name,
            "time_range": time_range,
            "ok_count": counts.ok_count,
            "ng_count": counts.ng_count,
            "event_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "is_test_snapshot": True,
        }

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
            normalized = self.config.validated_recipients(recipients)
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
                raise ValueError("云短信内容过长")
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

    def _validate_provider_configuration(self) -> None:
        if self.config.provider == "at_modem":
            if not self.config.port.strip():
                raise ValueError("未配置短信模块 COM 口")
            return
        if self.config.provider != "generic_http":
            raise ValueError(f"不支持的短信 Provider：{self.config.provider}")
        if not self.config.api_url.strip():
            raise ValueError("未配置云短信 API URL")
        has_token = bool(self.config.token.strip())
        has_key_pair = bool(
            self.config.access_key.strip() and self.config.access_secret.strip()
        )
        if not (has_token or has_key_pair):
            raise ValueError("云短信必须配置 token 或 access_key/access_secret")

    def _new_modem(self) -> SmsModem:
        return SmsModem.from_config(
            SerialConfig(port=self.config.port, baudrate=self.config.baudrate),
            logger=self._logger,
            serial_factory=self._serial_factory,
        )

    def _get_provider(self) -> AtModemProvider | GenericHttpProvider:
        with self._state_lock:
            if self._provider is not None:
                return self._provider
            if self.config.provider == "at_modem":
                provider: AtModemProvider | GenericHttpProvider = AtModemProvider(
                    self.config,
                    modem_factory=self._new_modem,
                    logger=self._logger,
                    stop_event=self._stop_event,
                )
            elif self.config.provider == "generic_http":
                provider = GenericHttpProvider(
                    self.config,
                    request_func=self._http_request,
                    logger=self._logger,
                    stop_event=self._stop_event,
                )
            else:
                raise ValueError(f"不支持的短信 Provider：{self.config.provider}")
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
                if self.config.provider == "generic_http" and result.retryable_failure:
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
        if self.config.provider != "generic_http" or not self.config.enabled:
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
    "validate_alarm_template",
]
