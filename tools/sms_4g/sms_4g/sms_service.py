"""短信服务门面与二期异步告警接入预留。

产品定位：这是多客户通用、默认关闭的 NG 短信通知通道，不属于客户插件。
``send_alarm`` 只负责校验和入队，绝不在检测推理线程里同步操作串口。
短信模块与现有 Modbus 灯塔使用不同 USB 设备和不同 COM，也不复用 MES Gateway。
"""

from __future__ import annotations

import queue
import string
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .at_client import SerialConfig
from .modem import DiagnosticReport, SmsModem
from .utils import choose_encoding, mask_phone, parse_recipients, validate_message


DEFAULT_ALARM_TEMPLATE = (
    "【天军AI视觉】{time} 发生{event_name}（事件 {event_id}）：{message}"
)


@dataclass(frozen=True)
class SmsServiceConfig:
    """短信服务配置；``enabled`` 默认 False，未配置时对现网零影响。"""

    enabled: bool = False
    port: str = ""
    baudrate: int = 115200
    recipients: tuple[str, ...] = ()
    template: str = DEFAULT_ALARM_TEMPLATE
    encoding: str = "auto"
    retries: int = 1
    retry_delay_seconds: float = 2.0
    cooldown_seconds: float = 60.0
    queue_size: int = 100

    def validated_recipients(
        self, override: Sequence[str] | str | None = None
    ) -> tuple[str, ...]:
        """返回覆盖号码或默认号码的规范化去重结果。"""

        source: Sequence[str] | str = (
            override if override is not None else self.recipients
        )
        return parse_recipients(source)


@dataclass(frozen=True)
class RecipientResult:
    """一个接收号码的最终发送结果。"""

    phone: str
    success: bool
    attempts: int
    detail: str
    message_reference: str = ""


@dataclass(frozen=True)
class SmsBatchResult:
    """同一条短信向多个号码发送的聚合结果。"""

    results: tuple[RecipientResult, ...]

    @property
    def success(self) -> bool:
        return bool(self.results) and all(item.success for item in self.results)

    @property
    def succeeded(self) -> int:
        return sum(item.success for item in self.results)


@dataclass(frozen=True)
class AlarmQueueReceipt:
    """``send_alarm`` 的即时入队回执，不代表运营商已送达。"""

    accepted: bool
    status: str
    detail: str
    job_id: str = ""


@dataclass(frozen=True)
class _AlarmJob:
    job_id: str
    event_key: str
    message: str
    recipients: tuple[str, ...]
    callback: Callable[[SmsBatchResult], None] | None = field(
        compare=False, default=None
    )


class SmsService:
    """提供同步硬件测试和二期非阻塞告警入队门面。

    Context: 二期 ``send_alarm`` 可在事件触发线程调用；该方法不打开串口、不 sleep，
             只做内存校验与 ``put_nowait``。真实 AT I/O、失败重试和回调都在 daemon worker。
    """

    def __init__(
        self,
        config: SmsServiceConfig,
        *,
        logger: Callable[[str], None] | None = None,
        serial_factory: Callable[..., object] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._logger = logger or (lambda _message: None)
        self._serial_factory = serial_factory
        self._clock = clock
        self._queue: queue.Queue[_AlarmJob] = queue.Queue(
            maxsize=max(1, config.queue_size)
        )
        self._last_enqueued: dict[str, float] = {}
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._job_counter = 0

    def diagnose_modem(self) -> DiagnosticReport:
        """同步执行完整硬件诊断；独立 GUI 会在 QThread 中调用。"""

        modem = self._new_modem()
        try:
            return modem.diagnose()
        finally:
            modem.client.close()

    def send_test_sms(
        self,
        recipients: Sequence[str] | str,
        message: str,
    ) -> SmsBatchResult:
        """同步发送测试短信；独立 GUI 会在 QThread 中调用，避免阻塞界面。"""

        normalized = self.config.validated_recipients(recipients)
        validate_message(message, choose_encoding(message, self.config.encoding))
        return self._send_batch(normalized, message)

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
        """渲染报警短信并非阻塞入队，返回即时接收/拒绝原因。

        Context: 允许从检测事件线程调用；不访问串口、不等待网络、不抛出硬件异常。
                 队列满直接拒绝，避免拖慢推理热路径。
        """

        try:
            if not self.config.enabled:
                return AlarmQueueReceipt(False, "disabled", "NG 短信推送总开关未开启")
            if not self.config.port.strip():
                return AlarmQueueReceipt(
                    False, "not_configured", "未配置短信模块 COM 口"
                )
            normalized = self.config.validated_recipients(recipients)
            rendered = self._render_alarm_message(
                event_id=event_id,
                event_name=event_name,
                message=message,
                context=context,
            )
            validate_message(rendered, choose_encoding(rendered, self.config.encoding))
            event_key = str(event_id)

            with self._state_lock:
                now = self._clock()
                last = self._last_enqueued.get(event_key)
                if last is not None and now - last < max(
                    0.0, self.config.cooldown_seconds
                ):
                    remain = self.config.cooldown_seconds - (now - last)
                    return AlarmQueueReceipt(
                        False,
                        "cooldown",
                        f"同类告警仍在冷却，约 {max(0, int(remain + 0.999))} 秒后可再次入队",
                    )
                self._ensure_worker_locked()
                self._job_counter += 1
                job_id = f"sms-{int(time.time())}-{self._job_counter}"
                job = _AlarmJob(job_id, event_key, rendered, normalized, callback)
                try:
                    self._queue.put_nowait(job)
                except queue.Full:
                    return AlarmQueueReceipt(
                        False, "queue_full", "短信发送队列已满，本次告警未入队"
                    )
                self._last_enqueued[event_key] = now

            self._logger(f"告警短信 {job_id} 已入队，接收人 {len(normalized)} 个")
            return AlarmQueueReceipt(True, "queued", "已进入短信异步队列", job_id)
        except Exception as exc:  # 门面必须错误隔离，不能反向影响事件触发线程
            self._logger(f"告警短信入队失败（已隔离）：{exc}")
            return AlarmQueueReceipt(False, "rejected", str(exc))

    def shutdown(self, timeout: float = 5.0) -> None:
        """请求后台 worker 在当前任务完成后退出。"""

        with self._state_lock:
            worker = self._worker
            self._stop_event.set()
        if worker is not None and worker.is_alive():
            worker.join(timeout=max(0.0, timeout))

    def _new_modem(self) -> SmsModem:
        return SmsModem.from_config(
            SerialConfig(port=self.config.port, baudrate=self.config.baudrate),
            logger=self._logger,
            serial_factory=self._serial_factory,
        )

    def _send_batch(self, recipients: tuple[str, ...], message: str) -> SmsBatchResult:
        results = tuple(
            self._send_one_with_retry(phone, message) for phone in recipients
        )
        return SmsBatchResult(results)

    def _send_one_with_retry(self, phone: str, message: str) -> RecipientResult:
        max_attempts = max(1, int(self.config.retries) + 1)
        last_error = "未知错误"
        for attempt in range(1, max_attempts + 1):
            modem = self._new_modem()
            try:
                modem.client.open()
                modem.ensure_ready()
                sent = modem.send_sms(phone, message, encoding=self.config.encoding)
                return RecipientResult(
                    phone=phone,
                    success=True,
                    attempts=attempt,
                    detail="已提交运营商；最终送达仍取决于 SIM 资费、运营商和手机状态",
                    message_reference=sent.message_reference,
                )
            except Exception as exc:
                last_error = str(exc)
                self._logger(
                    f"{mask_phone(phone)} 第 {attempt}/{max_attempts} 次发送失败：{last_error}"
                )
            finally:
                modem.client.close()
            if attempt < max_attempts:
                self._stop_event.wait(max(0.0, self.config.retry_delay_seconds))
        return RecipientResult(phone, False, max_attempts, last_error)

    def _ensure_worker_locked(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="sms-4g-worker",
            daemon=True,
        )
        self._worker.start()

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                job = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                result = self._send_batch(job.recipients, job.message)
                self._logger(
                    f"告警短信 {job.job_id} 完成：{result.succeeded}/{len(result.results)} 成功"
                )
                if job.callback is not None:
                    try:
                        job.callback(result)
                    except Exception as exc:  # 回调错误同样不能终止 worker
                        self._logger(f"告警短信回调异常（已隔离）：{exc}")
            except Exception as exc:
                self._logger(f"告警短信 {job.job_id} 后台异常（已隔离）：{exc}")
            finally:
                self._queue.task_done()

    def _render_alarm_message(
        self,
        *,
        event_id: int | str,
        event_name: str,
        message: str,
        context: Mapping[str, object] | None,
    ) -> str:
        values: dict[str, Any] = dict(
            event_id=event_id,
            event_name=event_name or "报警事件",
            message=message or "请及时到现场确认",
            time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        if context:
            for key, value in context.items():
                if key not in values:
                    values[key] = value
        try:
            parts: list[str] = []
            for (
                literal,
                field_name,
                format_spec,
                conversion,
            ) in string.Formatter().parse(self.config.template):
                parts.append(literal)
                if field_name is None:
                    continue
                if not field_name.isidentifier() or format_spec or conversion:
                    raise ValueError(
                        f"不支持的模板表达式 {{{field_name}}}；只允许简单字段名"
                    )
                if field_name not in values:
                    raise KeyError(field_name)
                parts.append(str(values[field_name]))
            return "".join(parts)
        except KeyError as exc:
            raise ValueError(f"短信模板缺少字段值：{exc.args[0]}") from exc
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"短信模板格式错误：{exc}") from exc
