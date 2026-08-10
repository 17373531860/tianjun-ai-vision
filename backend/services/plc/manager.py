"""
PLCConnectorManager 单例 — 连接生命周期 / 动作线程 / 事件写回匹配 / 诊断出口。

- start_all: 启动时拉起全部 enabled 连接 (无连接零开销)
- restart_connection: 配置改动后热重载单条连接 (API CRUD 后调用)
- submit_actions: 引擎触发规则 → 动作任务进共享动作线程
  (switch_project 加载模型秒级, 全局单线程串行还能防两条连接同时切项目打架)
- dispatch_event: 事件 → 各连接 write_rules 匹配 → 引擎写队列 (全程非阻塞)
"""
import logging
import queue
import threading
import time
from typing import Dict, Optional

from backend.db.database import SessionLocal
from backend.models.plc_models import PLCConnection
from backend.services.plc.point_engine import PLCPointEngine
from backend.services.plc import rule_actions

logger = logging.getLogger(__name__)


def _row_to_dict(row: PLCConnection) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "driver": row.driver,
        "enabled": row.enabled,
        "conn_params": row.conn_params or {},
        "points": row.points or [],
        "read_rules": row.read_rules or [],
        "write_rules": row.write_rules or [],
        "options": row.options or {},
    }


class PLCConnectorManager:

    def __init__(self):
        self._engines: Dict[int, PLCPointEngine] = {}
        self._lock = threading.Lock()
        self._action_q: "queue.Queue" = queue.Queue(maxsize=200)
        self._action_thread: Optional[threading.Thread] = None
        self._action_stop = threading.Event()

    # ================= 生命周期 =================

    def start_all(self):
        db = SessionLocal()
        try:
            rows = db.query(PLCConnection).filter(
                PLCConnection.enabled == True).all()  # noqa: E712
            row_dicts = [_row_to_dict(r) for r in rows]
        except Exception as e:
            # 老库升级首启时表可能刚建, 查询失败不阻断启动
            logger.warning("[PLC] 加载连接配置失败: %s", e)
            row_dicts = []
        finally:
            db.close()
        for row in row_dicts:
            self._start_engine(row)
        if row_dicts:
            logger.info("[PLC] 已启动 %d 条 PLC 连接", len(row_dicts))
            print(f"[PLC] 已启动 {len(row_dicts)} 条 PLC 连接", flush=True)

    def stop_all(self):
        with self._lock:
            engines = list(self._engines.values())
            self._engines.clear()
        for eng in engines:
            try:
                eng.stop()
            except Exception:
                pass
        self._action_stop.set()
        if self._action_thread and self._action_thread.is_alive():
            self._action_thread.join(timeout=3)
        self._action_thread = None

    def restart_connection(self, connection_id: int):
        """配置变更后热重载: 停旧引擎, enabled 才拉新引擎。"""
        self.remove_connection(connection_id)
        db = SessionLocal()
        try:
            row = db.query(PLCConnection).get(connection_id)
            if row is None or not row.enabled:
                return
            row_dict = _row_to_dict(row)
        finally:
            db.close()
        self._start_engine(row_dict)

    def remove_connection(self, connection_id: int):
        with self._lock:
            eng = self._engines.pop(connection_id, None)
        if eng:
            eng.stop()

    def _start_engine(self, row: dict):
        self._ensure_action_thread()
        eng = PLCPointEngine(row, self)
        with self._lock:
            self._engines[row["id"]] = eng
        eng.start()

    def has_engines(self) -> bool:
        return bool(self._engines)

    def get_engine(self, connection_id: int) -> Optional[PLCPointEngine]:
        return self._engines.get(connection_id)

    # ================= 动作线程 =================

    def _ensure_action_thread(self):
        if self._action_thread and self._action_thread.is_alive():
            return
        self._action_stop.clear()
        self._action_thread = threading.Thread(
            target=self._action_loop, daemon=True, name="plc-actions")
        self._action_thread.start()

    def _action_loop(self):
        while not self._action_stop.is_set():
            try:
                engine, rule, snapshot = self._action_q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                rule_actions.execute_actions(engine, rule, snapshot)
            except Exception:
                logger.exception("[PLC] 动作批执行异常")

    def submit_actions(self, engine: PLCPointEngine, rule: dict, snapshot: dict):
        """引擎线程提交动作 (非阻塞; 队列满丢弃并记日志, 不卡轮询)。"""
        try:
            self._action_q.put_nowait((engine, rule, snapshot))
        except queue.Full:
            engine._log("error",
                        f"动作队列满, 丢弃规则[{rule.get('name') or '?'}]的动作")

    # ================= 事件写回匹配 =================

    def dispatch_event(self, event: str, ctx: dict, channel_id: int = None,
                       only_engine: PLCPointEngine = None):
        """事件 → write_rules 匹配 → 写队列。调用方线程只做字典运算。"""
        # 顶层快捷键: 模板/value_map 里 {{result}} 直接可用 (等价 cycle.result)
        flat = dict(ctx)
        cycle = ctx.get("cycle") if isinstance(ctx.get("cycle"), dict) else {}
        flat.setdefault("result", cycle.get("result"))
        flat.setdefault("event", event)
        if channel_id is not None:
            flat.setdefault("channel_id", channel_id)

        engines = [only_engine] if only_engine is not None \
            else list(self._engines.values())
        for eng in engines:
            for rule in eng.write_rules:
                if (rule.get("on") or "") != event:
                    continue
                channels = rule.get("channels")
                if channels and channel_id is not None \
                        and int(channel_id) not in [int(c) for c in channels]:
                    continue
                results = rule.get("results")
                if results and flat.get("result") not in results:
                    continue
                ctx_full = {**eng.vars, **flat}
                for w in (rule.get("writes") or []):
                    try:
                        eng.enqueue_write(
                            w.get("point"),
                            rule_actions.resolve_write_value(w, ctx_full),
                            reset_after_ms=w.get("reset_after_ms"),
                            reset_value=w.get("reset_value"))
                    except Exception as e:
                        eng._log("error", f"事件 {event} 写回失败: {e}")

    # ================= 诊断出口 (API 用) =================

    def status_all(self) -> list:
        return [eng.snapshot() for eng in self._engines.values()]

    def mock_set(self, connection_id: int, key: str, value) -> dict:
        """mock 驱动专属: 模拟 PLC 侧改点位值 (联调/测试/演示)。"""
        eng = self._engines.get(connection_id)
        if eng is None:
            raise ValueError("连接未启用")
        from backend.services.plc.drivers.mock import MockPLCDriver
        if not isinstance(eng.driver, MockPLCDriver):
            raise ValueError("仅 mock 驱动支持模拟 PLC 侧写值")
        point = eng.points.get(key)
        if point is None:
            raise ValueError(f"无点位: {key}")
        from backend.services.plc import point_codec
        coerced = point_codec.coerce_value(value, point)
        eng.driver.set_external(key, coerced)
        return {"key": key, "value": coerced, "ts": time.time()}


_manager: Optional[PLCConnectorManager] = None


def get_plc_manager() -> PLCConnectorManager:
    global _manager
    if _manager is None:
        _manager = PLCConnectorManager()
    return _manager
