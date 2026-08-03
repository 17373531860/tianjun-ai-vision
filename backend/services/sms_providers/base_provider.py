"""短信 Provider 的最小公共契约。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class RecipientResult:
    """一个接收号码的最终发送结果。"""

    phone: str
    success: bool
    attempts: int
    detail: str
    message_reference: str = ""
    status_code: int | None = None
    error_code: str = ""
    retryable: bool = False


@dataclass(frozen=True)
class SmsBatchResult:
    """同一条短信向多个号码发送的聚合结果。"""

    results: tuple[RecipientResult, ...]
    offline_queued: bool = False
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return bool(self.results) and all(item.success for item in self.results)

    @property
    def succeeded(self) -> int:
        return sum(item.success for item in self.results)

    @property
    def retryable_failure(self) -> bool:
        return bool(self.results) and not self.success and any(
            item.retryable for item in self.results
        )

    @property
    def retry_count(self) -> int:
        return max((max(0, item.attempts - 1) for item in self.results), default=0)


class SmsProvider(ABC):
    """Provider 只负责后台 I/O；事件热路径永远不直接调用它。"""

    name: str

    @abstractmethod
    def send(
        self,
        recipients: Sequence[str],
        message: str,
        *,
        message_id: str,
        event_name: str,
        raw_message: str,
        context: Mapping[str, object],
    ) -> SmsBatchResult:
        """在 worker 中发送一批短信并返回最终结果。"""

    def shutdown(self) -> None:
        """中断或关闭 Provider 当前资源；无资源的 Provider 可保持空实现。"""

