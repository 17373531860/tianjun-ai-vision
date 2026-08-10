"""
事件写回入口 (RFC 13 维度五) — 主程序事件 → PLC 写点位。

调用方 (目前两处, 加事件源就是加一行调用):
- mes_hooks._handle_cycle_start / _handle_cycle_end (MES worker 线程, 非检测热路径)
- point_engine 自身 (connection_up / connection_down)

本函数只做规则匹配 + 值解析 + enqueue_write (put_nowait), 微秒级返回;
真正的 PLC IO 在各引擎线程执行 —— 满足不变量 15 (绝不阻塞调用方)。
无启用连接时是一次空 dict 遍历, 零开销。
"""
import logging

logger = logging.getLogger(__name__)


def dispatch_plc_event(event: str, ctx: dict, channel_id: int = None) -> None:
    """把主程序事件派发给所有 PLC 连接的写回规则。永不抛异常。"""
    try:
        from backend.services.plc.manager import get_plc_manager
        mgr = get_plc_manager()
        if not mgr.has_engines():
            return
        mgr.dispatch_event(event, ctx or {}, channel_id=channel_id)
    except Exception as e:
        logger.warning("[PLC] 事件写回派发失败 (%s): %s", event, e)
