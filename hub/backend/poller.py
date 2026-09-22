"""每节点轮询器 (RFC 15 §10.2 / §11.1 poller)。

- 每个纳管节点一个独立 asyncio 任务, 任一节点异常不拖累其他节点。
- health-summary 默认 2s; 连续 3 次失败判离线, 离线后指数退避 (2s→30s)。
- profile 低频校验: 每 60s 比对 handshake 的 profile_hash, 变化才拉全量。
- 运行态 (NodeRuntime) 以内存为准; 孪生 reported 变化才落 DB (twin_store)。
- 测试不起循环 (HUB_ENABLE_POLLER=0), 直接调 poll_node_once() 驱动。
"""
import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from hub.backend import db as hubdb
from hub.backend import rollup, twin_store
from hub.backend.config import (CYCLE_PULL_INTERVAL_S, CYCLE_PULL_PAGE,
                                EVENT_KEEP_MAX, EVENT_PULL_INTERVAL_S,
                                HEALTH_INTERVAL_S, MAX_BACKOFF_S,
                                OFFLINE_AFTER_FAILURES, PROFILE_INTERVAL_S,
                                RETENTION_INTERVAL_S, ROLLUP_INTERVAL_S,
                                get_data_dir)
from hub.backend.edge_client import EdgeClient, EdgeError
from hub.backend.models import HubEvent, HubNode, HubStation
from hub.backend.security import decrypt_api_key


@dataclass
class NodeRuntime:
    """单节点内存运行态 (墙/下钻的读源; 不落库)"""
    node_id: int
    status: str = "unknown"                # unknown / online / offline
    consecutive_failures: int = 0
    last_seen: Optional[float] = None      # time.time()
    last_summary: Optional[dict] = None    # 最近一次 health-summary 原文
    last_error: Optional[str] = None
    last_profile_check: float = 0.0
    last_event_pull: float = 0.0
    last_cycle_pull: float = 0.0

    def snapshot(self) -> dict:
        return {
            "node_id": self.node_id,
            "status": self.status,
            "consecutive_failures": self.consecutive_failures,
            "last_seen": self.last_seen,
            "last_error": self.last_error,
            "summary": self.last_summary,
        }


class HubPoller:
    """节点轮询管理器。app.state.poller 单实例 (每 create_app 独立, 测试不串)。"""

    def __init__(self, loops_enabled: bool = True):
        # loops_enabled=False (测试态 HUB_ENABLE_POLLER=0): add_node/start_all
        # 全部 no-op, 状态只能由 poll_node_once 显式驱动 —— 否则 enroll 起的
        # 后台循环会和测试手动 poll 并发竞争 runtime (M1 测试期真实踩过)
        self.loops_enabled = loops_enabled
        self.runtimes: Dict[int, NodeRuntime] = {}
        self._tasks: Dict[int, asyncio.Task] = {}
        self._stopping = False

    def runtime(self, node_id: int) -> NodeRuntime:
        if node_id not in self.runtimes:
            self.runtimes[node_id] = NodeRuntime(node_id=node_id)
        return self.runtimes[node_id]

    # ============================================================
    # 单次轮询 (循环与测试共用的核心)
    # ============================================================

    def _make_client(self, node: HubNode) -> EdgeClient:
        api_key = None
        if node.api_key_enc:
            api_key = decrypt_api_key(node.api_key_enc, get_data_dir())
        return EdgeClient(node.base_url, api_key=api_key)

    async def poll_node_once(self, node_id: int) -> NodeRuntime:
        """拉一次 health-summary + (低频) profile 校验, 更新运行态与孪生。"""
        rt = self.runtime(node_id)
        db = hubdb.SessionLocal()
        try:
            node = db.query(HubNode).filter(HubNode.id == node_id).first()
            if not node or not node.enabled:
                rt.status = "unknown"
                rt.last_error = "节点不存在或已停用"
                return rt

            async with self._make_client(node) as client:
                try:
                    summary = await client.health_summary()
                except EdgeError as e:
                    rt.consecutive_failures += 1
                    rt.last_error = e.detail
                    if rt.consecutive_failures >= OFFLINE_AFTER_FAILURES:
                        rt.status = "offline"
                    return rt

                rt.status = "online"
                rt.consecutive_failures = 0
                rt.last_error = None
                rt.last_seen = time.time()
                rt.last_summary = summary

                # 孪生 reported (变化才写库)
                changed = False
                active_pid = summary.get("active_project_id")
                for st in summary.get("stations", []):
                    reported = {
                        "detecting": st.get("is_detecting"),
                        "is_running": st.get("is_running"),
                        "logic_mode": st.get("logic_mode"),
                        "active_project_id": active_pid,
                    }
                    if twin_store.update_reported(
                            db, node_id, st["channel_id"], reported):
                        changed = True
                if changed:
                    db.commit()

                # profile 低频校验: hash 变了才拉全量 (项目切换/工位数变化)
                now = time.time()
                if now - rt.last_profile_check >= PROFILE_INTERVAL_S:
                    rt.last_profile_check = now
                    try:
                        hs = await client.handshake()
                        if hs.get("profile_hash") != node.profile_hash:
                            await self._refresh_profile(db, node, client)
                    except EdgeError:
                        pass  # 摘要已成功, 档案校验失败不降级在线态

                # 事件拉取 (P0-10): 低频档, 游标断点续传, 只拉 NG 落报警中心
                if now - rt.last_event_pull >= EVENT_PULL_INTERVAL_S:
                    rt.last_event_pull = now
                    try:
                        await self._pull_events(db, node, client)
                    except EdgeError:
                        pass  # 事件拉取失败不降级在线态, 下轮游标重试

                # 统计通道 (M5): 全量周期拉取, 独立游标, 慢节奏大页
                if now - rt.last_cycle_pull >= CYCLE_PULL_INTERVAL_S:
                    rt.last_cycle_pull = now
                    try:
                        await self._pull_cycles(db, node, client)
                    except EdgeError:
                        pass  # 同上, 下轮游标重试
            return rt
        finally:
            db.close()

    async def _pull_events(self, db, node: HubNode,
                           client: EdgeClient) -> None:
        """按游标增量拉 NG 事件落 hub_events (报警中心数据源)。

        - 首次 (event_cursor 为 NULL): cursor=None → 边缘"从现在订阅"语义,
          不翻纳管前的历史帐, 拿到 next_cursor 即落底 (可能是 0, 空边缘)。
          之后哪怕游标是 0 也走增量分支 —— NULL 与 0 语义必须分开。
        - (node_id, edge_event_id) 唯一约束兜底重放; 超保留上限删最老。
        """
        cursor = node.event_cursor          # None = 从未拉过
        res = await client.events(cursor=cursor, limit=200, result="ng")
        events = res.get("events") or []
        if cursor is not None:
            existing = {
                r[0] for r in db.query(HubEvent.edge_event_id).filter(
                    HubEvent.node_id == node.id,
                    HubEvent.edge_event_id.in_(
                        [e["id"] for e in events])).all()
            } if events else set()
            for e in events:
                if e["id"] in existing:
                    continue
                db.add(HubEvent(
                    node_id=node.id, channel_id=e.get("channel_id", 0),
                    edge_event_id=e["id"], kind=e.get("kind", "cycle"),
                    result=e.get("result"), event_name=e.get("event_name"),
                    reason=e.get("reason"), ts=e.get("ts")))
        node.event_cursor = int(res.get("next_cursor") or 0)
        db.commit()

        # 保留上限收口 (全局, 不分节点; 报警中心只看近况)
        total = db.query(HubEvent).count()
        if total > EVENT_KEEP_MAX:
            overflow = total - EVENT_KEEP_MAX
            old_ids = [r[0] for r in db.query(HubEvent.id)
                       .order_by(HubEvent.id.asc()).limit(overflow).all()]
            db.query(HubEvent).filter(HubEvent.id.in_(old_ids)) \
                .delete(synchronize_session=False)
            db.commit()

    async def _pull_cycles(self, db, node: HubNode,
                           client: EdgeClient) -> None:
        """按独立游标增量拉全量周期 (OK+NG) 落 hub_cycles (数据中心明细层)。

        - 游标 NULL/0 语义与 _pull_events 完全一致 (同一教训)。
        - 追赶预算: 单轮最多翻 4 页 (4×500=2000 行) —— 枢纽长时间停机后
          重新上线不会在一次 poll 里憋大事务, 剩余的下轮继续。
        - 去重/标脏在 rollup.ingest_cycles; 聚合由 rollup worker 异步消费。
        """
        for _ in range(4):
            cursor = node.cycle_cursor      # None = 从未拉过
            res = await client.events(cursor=cursor, limit=CYCLE_PULL_PAGE)
            events = res.get("events") or []
            if cursor is not None and events:
                rollup.ingest_cycles(db, node.id, events)
            node.cycle_cursor = int(res.get("next_cursor") or 0)
            db.commit()
            if cursor is None or len(events) < CYCLE_PULL_PAGE:
                break   # 首拉只落游标; 增量拉完本批未截断即追平

    async def _refresh_profile(self, db, node: HubNode,
                               client: EdgeClient) -> None:
        """档案变化: 更新快照 + 同步工位行 (新增补行 / 消失删行)"""
        profile = await client.profile()
        ident = profile.get("identity", {})
        node.profile = profile
        node.profile_hash = profile.get("profile_hash")
        node.app_version = ident.get("app_version") or node.app_version
        node.api_contract = ident.get("api_contract") or node.api_contract
        node.license_state = (ident.get("license") or {}).get(
            "state", node.license_state)

        seen = set()
        existing = {s.channel_id: s for s in db.query(HubStation).filter(
            HubStation.node_id == node.id).all()}
        for st in profile.get("stations", []):
            ch = st["channel_id"]
            seen.add(ch)
            if ch not in existing:
                db.add(HubStation(node_id=node.id, channel_id=ch,
                                  display_name=f"{node.name}-工位{ch}"))
        for ch, row in existing.items():
            if ch not in seen:
                db.delete(row)
        db.commit()

    # ============================================================
    # 循环管理
    # ============================================================

    def _interval(self, rt: NodeRuntime) -> float:
        """在线走 2s 档; 离线指数退避 2s→30s (RFC 15 §10.2)"""
        if rt.status != "offline":
            return HEALTH_INTERVAL_S
        over = rt.consecutive_failures - OFFLINE_AFTER_FAILURES
        return min(HEALTH_INTERVAL_S * (2 ** max(over, 0)), MAX_BACKOFF_S)

    async def _node_loop(self, node_id: int):
        while not self._stopping:
            try:
                rt = await self.poll_node_once(node_id)
            except Exception as e:  # 循环永不因未知异常退出
                rt = self.runtime(node_id)
                rt.consecutive_failures += 1
                rt.last_error = f"{e.__class__.__name__}: {e}"
                if rt.consecutive_failures >= OFFLINE_AFTER_FAILURES:
                    rt.status = "offline"
            await asyncio.sleep(self._interval(rt))

    async def _rollup_loop(self):
        """全局唯一: 脏桶重算 (M5)。同步 DB 操作丢线程池, 不卡事件循环。"""
        while not self._stopping:
            try:
                await asyncio.to_thread(self._rollup_once)
            except Exception:
                pass  # 下轮重试; 脏桶还在, 不丢账
            await asyncio.sleep(ROLLUP_INTERVAL_S)

    @staticmethod
    def _rollup_once() -> int:
        db = hubdb.SessionLocal()
        try:
            return rollup.recompute_dirty(db)
        finally:
            db.close()

    async def _retention_loop(self):
        """全局唯一: 过期清理 (明细 90 天 / 小时桶 2 年 / 启动即跑一次)。"""
        while not self._stopping:
            try:
                await asyncio.to_thread(self._retention_once)
            except Exception:
                pass
            await asyncio.sleep(RETENTION_INTERVAL_S)

    @staticmethod
    def _retention_once() -> dict:
        db = hubdb.SessionLocal()
        try:
            return rollup.run_retention(db)
        finally:
            db.close()

    def add_node(self, node_id: int) -> None:
        """纳管成功后启动该节点循环 (幂等; loops_enabled=False 时 no-op)"""
        if not self.loops_enabled:
            return
        if node_id in self._tasks and not self._tasks[node_id].done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 无事件循环: 不起循环 (由 start_all 统一拉起)
        self._tasks[node_id] = loop.create_task(self._node_loop(node_id))

    def remove_node(self, node_id: int) -> None:
        task = self._tasks.pop(node_id, None)
        if task:
            task.cancel()
        self.runtimes.pop(node_id, None)

    async def start_all(self) -> None:
        """lifespan 启动: 为 DB 里全部启用节点起循环"""
        if not self.loops_enabled:
            return  # 测试态: rollup/retention 由测试显式调 _rollup_once 等驱动
        db = hubdb.SessionLocal()
        try:
            ids = [n.id for n in db.query(HubNode).filter(
                HubNode.enabled == True).all()]  # noqa: E712
        finally:
            db.close()
        for nid in ids:
            self.add_node(nid)
        # M5 全局工作循环 (与节点数无关, 各一份)
        loop = asyncio.get_running_loop()
        self._tasks[-1] = loop.create_task(self._rollup_loop())
        self._tasks[-2] = loop.create_task(self._retention_loop())

    async def stop_all(self) -> None:
        self._stopping = True
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
