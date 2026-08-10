"""TriggerHubManager 单例 — 触发源实例生命周期 / 共享动作线程 / 诊断出口。

结构对照 PLCConnectorManager (RFC 13):
- start_all: 启动时拉起全部 enabled 实例 (无实例零线程零开销)
- restart_trigger: 配置改动后热重载单实例 (API CRUD 后调用)
- submit_actions: 引擎触发 → 动作任务进共享动作线程 (与 PLC 各自一线程,
  互不阻塞; 动作面同一套注册表 triggers/actions.py)
- fire_http / mock_fire / set_mock_level: API 层注入口
"""
import logging
import queue
import threading
from typing import Dict, Optional

from backend.db.database import SessionLocal
from backend.models.trigger_models import TriggerChannel
from backend.services.triggers import actions as trigger_actions
from backend.services.triggers.engine import TriggerChannelEngine

logger = logging.getLogger(__name__)


def _row_to_dict(row: TriggerChannel) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "type": row.type,
        "enabled": row.enabled,
        "params": row.params or {},
        "rules": row.rules or [],
        "options": row.options or {},
    }


class TriggerHubManager:

    def __init__(self):
        self._engines: Dict[int, TriggerChannelEngine] = {}
        self._lock = threading.Lock()
        self._action_q: "queue.Queue" = queue.Queue(maxsize=200)
        self._action_thread: Optional[threading.Thread] = None
        self._action_stop = threading.Event()

    # ================= 生命周期 =================

    def start_all(self):
        db = SessionLocal()
        try:
            rows = db.query(TriggerChannel).filter(
                TriggerChannel.enabled == True).all()  # noqa: E712
            row_dicts = [_row_to_dict(r) for r in rows]
        except Exception as e:
            # 老库升级首启时表可能刚建, 查询失败不阻断启动
            logger.warning("[Trigger] 加载触发源配置失败: %s", e)
            row_dicts = []
        finally:
            db.close()
        for row in row_dicts:
            self._start_engine(row)
        if row_dicts:
            logger.info("[Trigger] 已启动 %d 个触发源", len(row_dicts))
            print(f"[Trigger] 已启动 {len(row_dicts)} 个触发源", flush=True)

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

    def restart_trigger(self, trigger_id: int):
        """配置变更后热重载: 停旧引擎, enabled 才拉新引擎。"""
        self.remove_trigger(trigger_id)
        db = SessionLocal()
        try:
            row = db.query(TriggerChannel).get(trigger_id)
            if row is None or not row.enabled:
                return
            row_dict = _row_to_dict(row)
        finally:
            db.close()
        self._start_engine(row_dict)

    def remove_trigger(self, trigger_id: int):
        with self._lock:
            eng = self._engines.pop(trigger_id, None)
        if eng:
            eng.stop()

    def _start_engine(self, row: dict):
        self._ensure_action_thread()
        eng = TriggerChannelEngine(row, self)
        with self._lock:
            self._engines[row["id"]] = eng
        eng.start()

    def has_engines(self) -> bool:
        return bool(self._engines)

    def get_engine(self, trigger_id: int) -> Optional[TriggerChannelEngine]:
        return self._engines.get(trigger_id)

    def on_channel_removed(self, channel_id: int):
        """工位裁撤配套 (不变量 4): 绑定该工位的实例停掉并记日志。"""
        for eng in list(self._engines.values()):
            bound = eng.params.get("channel")
            if bound is None:
                bound = (eng.options or {}).get("default_channel")
            if bound is not None and int(bound) == int(channel_id):
                eng._log("error", f"绑定工位 {channel_id} 已裁撤, 触发源停用")
                self.remove_trigger(eng.trigger_id)

    # ================= 动作线程 =================

    def _ensure_action_thread(self):
        if self._action_thread and self._action_thread.is_alive():
            return
        self._action_stop.clear()
        self._action_thread = threading.Thread(
            target=self._action_loop, daemon=True, name="trigger-actions")
        self._action_thread.start()

    def _action_loop(self):
        while not self._action_stop.is_set():
            try:
                engine, rule, snapshot = self._action_q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                trigger_actions.execute_actions(engine, rule, snapshot)
            except Exception:
                logger.exception("[Trigger] 动作批执行异常")

    def submit_actions(self, engine: TriggerChannelEngine, rule: dict, snapshot: dict):
        """引擎信号线程提交动作 (非阻塞; 队列满丢弃并记日志)。"""
        try:
            self._action_q.put_nowait((engine, rule, snapshot))
        except queue.Full:
            engine._log("error",
                        f"动作队列满, 丢弃规则[{rule.get('name') or '?'}]的动作")

    # ================= API 注入口 =================

    def fire_http(self, key: str, payload: dict, secret: str, client_ip: str) -> dict:
        """HTTP 触发入口: 按 key 找 http 源 → 鉴权 → fire。"""
        from backend.services.triggers.sources.http_source import HttpSource
        for eng in self._engines.values():
            if isinstance(eng.source, HttpSource) and eng.source.key == key:
                reason = eng.source.check_auth(secret, client_ip)
                if reason:
                    eng._log("error", f"HTTP 触发被拒: {reason}")
                    raise PermissionError(reason)
                eng.source.fire(payload)
                return {"trigger": eng.name}
        raise KeyError(f"无启用的 HTTP 触发源: {key}")

    def mock_fire(self, trigger_id: int, meta: dict = None) -> dict:
        eng = self._need_mock(trigger_id)
        eng.source.fire(meta)
        return {"trigger": eng.name}

    def set_mock_level(self, trigger_id: int, value: bool, meta: dict = None) -> dict:
        eng = self._need_mock(trigger_id)
        eng.source.set_level(value, meta)
        return {"trigger": eng.name, "level": bool(value)}

    def _need_mock(self, trigger_id: int) -> TriggerChannelEngine:
        eng = self._engines.get(trigger_id)
        if eng is None:
            raise ValueError("触发源未启用")
        from backend.services.triggers.sources.mock import MockTriggerSource
        if not isinstance(eng.source, MockTriggerSource):
            raise ValueError("仅 mock 触发源支持模拟注入")
        return eng

    # ================= 诊断出口 =================

    def status_all(self) -> list:
        return [eng.snapshot() for eng in self._engines.values()]


_manager: Optional[TriggerHubManager] = None


def get_trigger_manager() -> TriggerHubManager:
    global _manager
    if _manager is None:
        _manager = TriggerHubManager()
    return _manager
