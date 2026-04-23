"""
集群数据汇总服务

职责:
1. 接收本地和远程工位的 cycle 结果
2. 按 box_serial 汇总所有工位数据
3. 所有工位到齐后触发 MES Gateway 推送 box_complete 事件
4. 超时未齐的箱子按策略处理（推送不完整数据或告警）
"""
import json
import threading
import time
import traceback
import requests
from datetime import datetime
from typing import Optional

from sqlalchemy.exc import OperationalError, IntegrityError

from backend.db.database import SessionLocal
from backend.models.mes_models import (
    ClusterConfig, BoxAggregation, BoxSummary,
)

import logging
logger = logging.getLogger(__name__)


def _build_sub_report(ctx: dict, channel_id=None, source_address=None,
                      is_good=None, event_name=None) -> dict:
    """从 cycle_context 摘一份"本次上报快照"，用于前端分路展示。

    只保留前端展示需要的关键字段，避免体积膨胀。
    """
    if not isinstance(ctx, dict):
        ctx = {}
    cycle_block = ctx.get("cycle") if isinstance(ctx.get("cycle"), dict) else {}
    snap = {
        "channel_id": channel_id,
        "source_address": source_address,
        "is_good": is_good,
        "event_name": event_name,
        "ng_reason": cycle_block.get("ng_reason"),
        "duration": cycle_block.get("duration"),
        "device_role": ctx.get("device_role"),
        "device_name": ctx.get("device_name"),
        "device_data": ctx.get("device_data"),
    }
    return {k: v for k, v in snap.items() if v is not None}


def _merge_cycle_context(old: dict, new: dict, merged_is_good: bool) -> dict:
    """合并同一个站点多次上报的 cycle_context。

    适用于一个站点由多路视觉组成的场景（例如机器 B 有两路摄像头都归到站点 B）。
    合并规则：
    - 顶层字段：new 覆盖 old（保留最新源地址、时间戳等）
    - cycle.result / cycle.is_good / 事件等判定：按 merged_is_good 统一覆写
    - 列表型（defects / images / violations 等）：去重累加
    - 字典型（extra_fields / device_data 等）：浅合并，new 覆盖 old

    sub_reports 由调用方在外面追加，本函数只做常规深合并。
    """
    if not isinstance(old, dict):
        old = {}
    if not isinstance(new, dict):
        new = {}
    result = dict(old)
    for k, v in new.items():
        if isinstance(v, list) and isinstance(result.get(k), list):
            seen = []
            for item in list(result[k]) + v:
                if item not in seen:
                    seen.append(item)
            result[k] = seen
        elif isinstance(v, dict) and isinstance(result.get(k), dict):
            merged = dict(result[k])
            for kk, vv in v.items():
                if isinstance(vv, list) and isinstance(merged.get(kk), list):
                    seen = []
                    for item in list(merged[kk]) + vv:
                        if item not in seen:
                            seen.append(item)
                    merged[kk] = seen
                elif isinstance(vv, dict) and isinstance(merged.get(kk), dict):
                    sub = dict(merged[kk])
                    sub.update(vv)
                    merged[kk] = sub
                else:
                    merged[kk] = vv
            result[k] = merged
        else:
            result[k] = v

    cycle_block = result.get("cycle")
    if isinstance(cycle_block, dict):
        cycle_block["is_good"] = merged_is_good
        cycle_block["result"] = "OK" if merged_is_good else "NG"
    return result


def _run_with_retry(db, build_fn, context: str = "write",
                    max_retries: int = 6, base_sleep: float = 0.2) -> bool:
    """SQLite 写锁退避重试：每轮重新执行 build_fn(db) 再 commit。

    **关键设计**：SQLite 遇到 locked 时 commit 会抛；如果我们此时 rollback，
    session 里 add 过的对象也会被清空。之前那版 `_commit_with_retry` 只重试
    `db.commit()` 就等于 no-op（pending 对象已没了），导致 HTTP 返回 success=True
    但数据**根本没入库**。所以必须由调用方提供 build_fn，每次重试都重新
    add/update 对象再 commit。

    - build_fn(db) 负责 add/update 当轮的 ORM 改动（不要在里面 commit）。
    - 捕获 OperationalError(locked/busy) → rollback + sleep + 重建。
    - 其他 OperationalError / IntegrityError 直接抛，调用方自己处理。
    - 成功返回 True，所有重试均 locked 才返回 False。
    """
    last_err = None
    for attempt in range(max_retries):
        try:
            build_fn(db)
            db.commit()
            return True
        except OperationalError as e:
            msg = str(e).lower()
            if "locked" in msg or "busy" in msg:
                last_err = e
                db.rollback()
                logger.warning(
                    "[Cluster] %s 第 %d 次写锁冲突，回滚重做: %s",
                    context, attempt + 1, e,
                )
                time.sleep(base_sleep * (attempt + 1))
                continue
            raise
    logger.error("[Cluster] %s 重试 %d 次仍失败: %s",
                 context, max_retries, last_err)
    return False


class ClusterCollector:

    def __init__(self):
        self._lock = threading.Lock()
        self._timeout_thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._config_cache: Optional[dict] = None
        self._config_ts: float = 0
        self._connected_slaves: dict = {}  # station_id -> {info}
        self._slave_timeout = 20  # 超过20秒没心跳视为离线
        self._heartbeat_interval = 5  # 副机 5 秒发一次心跳
        # per-box 进程内锁：同箱号的 receive_station_report 串行化，
        # 避免 MES box_complete 被多线程重复推送、UNIQUE/IntegrityError 冲突。
        # SQLite 不支持 SELECT FOR UPDATE，这是最稳妥的进程内串行手段。
        self._box_locks: dict = {}
        self._box_locks_guard = threading.Lock()

    def _acquire_box_lock(self, box_serial: str) -> threading.Lock:
        """获取/创建该箱号的串行锁，返回 Lock（未 acquire，由调用方 with 使用）"""
        with self._box_locks_guard:
            lk = self._box_locks.get(box_serial)
            if lk is None:
                lk = threading.Lock()
                self._box_locks[box_serial] = lk
            return lk

    def _release_box_lock(self, box_serial: str):
        """箱子 pushed/timeout 后清理锁，避免 dict 无界增长"""
        with self._box_locks_guard:
            self._box_locks.pop(box_serial, None)

    def start(self):
        self._stop_event.clear()
        self._timeout_thread = threading.Thread(
            target=self._timeout_checker, daemon=True,
            name="cluster-timeout-checker"
        )
        self._timeout_thread.start()

        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_sender_loop, daemon=True,
            name="cluster-heartbeat-sender"
        )
        self._heartbeat_thread.start()

        logger.info("[Cluster] 汇总服务已启动 (含副机心跳发送线程)")

    def stop(self):
        self._stop_event.set()
        if self._timeout_thread:
            self._timeout_thread.join(timeout=5)
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)

    def get_config(self, db=None) -> dict:
        """读取集群配置，带 5 秒缓存"""
        now = time.time()
        if self._config_cache and (now - self._config_ts) < 5:
            return self._config_cache

        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True
        try:
            cfg = db.query(ClusterConfig).filter(ClusterConfig.id == 1).first()
            if not cfg:
                result = {
                    "role": "standalone", "master_url": None,
                    "station_id": "A", "expected_stations": [],
                    "sync_mode": "wait_all", "timeout_sec": 300,
                    "timeout_push": False,
                    "enabled": False,
                    "channel_station_map": {},
                }
            else:
                result = {
                    "role": cfg.role,
                    "master_url": cfg.master_url,
                    "station_id": cfg.station_id,
                    "expected_stations": cfg.expected_stations or [],
                    "sync_mode": cfg.sync_mode,
                    "timeout_sec": cfg.timeout_sec,
                    "timeout_push": getattr(cfg, 'timeout_push', False) or False,
                    "enabled": cfg.enabled,
                    "channel_station_map": getattr(cfg, 'channel_station_map', None) or {},
                }
            self._config_cache = result
            self._config_ts = now
            return result
        finally:
            if close_db:
                db.close()

    def invalidate_config_cache(self):
        self._config_cache = None
        self._config_ts = 0

    def register_slave(self, station_id: str, ip: str, port: int = 8001,
                       hostname: str = "", project: str = "",
                       channel_count: int = 1, detecting: bool = False) -> dict:
        """副机心跳注册/更新"""
        now = time.time()
        with self._lock:
            self._connected_slaves[station_id] = {
                "station_id": station_id,
                "ip": ip,
                "port": port,
                "hostname": hostname,
                "project": project,
                "channel_count": channel_count,
                "detecting": detecting,
                "last_seen": now,
                "first_seen": self._connected_slaves.get(station_id, {}).get("first_seen", now),
            }
        return {"success": True}

    def get_connected_slaves(self) -> list:
        """获取当前在线副机列表，自动清理超时的"""
        now = time.time()
        result = []
        with self._lock:
            expired = []
            for sid, info in self._connected_slaves.items():
                if now - info["last_seen"] > self._slave_timeout:
                    expired.append(sid)
                else:
                    result.append({
                        **info,
                        "online_seconds": int(now - info["first_seen"]),
                        "last_heartbeat_ago": round(now - info["last_seen"], 1),
                    })
            for sid in expired:
                del self._connected_slaves[sid]
        return result

    def receive_station_report(self, station_id: str, box_serial: str,
                               cycle_context: dict, source_address: str = "local",
                               channel_id: int = None, is_good: bool = True,
                               event_name: str = None) -> dict:
        """收到一个工位的数据，存入汇总表并检查是否齐。

        并发模型（多个线程同时 POST 同一箱号 / 同一 station_id）：
        - 用 per-box_serial 进程内锁把整段查+写+_check_and_dispatch 串行化，
          避免 MES box_complete 被重复推送、BoxSummary/BoxAggregation UNIQUE 冲突、
          records.status 判定读到脏数据。
        - 同 station_id 重复 INSERT（跨进程也可能发生，因此保留 IntegrityError 重试）。
        - SQLite WAL 下仍只允许单写，与 _timeout_checker 会抢锁 → locked，
          由 _run_with_retry 兜底（每轮回滚后重建 add/update 再 commit）。
        """
        with self._acquire_box_lock(box_serial):
            return self._receive_station_report_locked(
                station_id, box_serial, cycle_context,
                source_address, channel_id, is_good, event_name,
            )

    def _receive_station_report_locked(
            self, station_id, box_serial, cycle_context,
            source_address, channel_id, is_good, event_name):
        db = SessionLocal()
        try:
            def build_upsert(db):
                existing = (
                    db.query(BoxAggregation)
                    .filter(BoxAggregation.box_serial == box_serial,
                            BoxAggregation.station_id == station_id)
                    .first()
                )
                if existing:
                    # v2.8.0 合并语义：同站点多次上报（例如一个站点由多路视觉组成）
                    # 不再整体覆盖，而是按业务规则合并：
                    #   - is_good：两路都 OK 才算 OK（有一路 NG 即 NG）
                    #   - event_name：NG 方优先；都 OK 则保留后到的
                    #   - cycle_context：深合并，defects / extra_fields 等列表做去重累加
                    # v2.8.x 增强：同一路重复上报（同 channel_id + source_address）时
                    # 用最新那次覆盖旧的，不再无限累加；不同路各自保留。
                    merged_context = _merge_cycle_context(
                        existing.cycle_context, cycle_context,
                        bool(existing.is_good) and bool(is_good)
                    )

                    # 整理 sub_reports: 按 (channel_id, source_address) 去重，同路最新覆盖
                    existing_subs = (merged_context.get("sub_reports")
                                     if isinstance(merged_context.get("sub_reports"), list)
                                     else [])
                    if not existing_subs:
                        old_snap = _build_sub_report(
                            existing.cycle_context,
                            channel_id=existing.channel_id,
                            source_address=existing.source_address,
                            is_good=existing.is_good,
                            event_name=existing.event_name,
                        )
                        existing_subs = [old_snap]
                    new_snap = _build_sub_report(
                        cycle_context,
                        channel_id=channel_id,
                        source_address=source_address,
                        is_good=is_good,
                        event_name=event_name,
                    )
                    dedup_key = (channel_id, source_address)
                    existing_subs = [
                        s for s in existing_subs
                        if (s.get("channel_id"), s.get("source_address")) != dedup_key
                    ]
                    existing_subs.append(new_snap)
                    merged_context["sub_reports"] = existing_subs

                    # 基于去重后的 sub_reports 重新推导 is_good / event_name
                    # 只要任意一路 NG 即整站 NG；都 OK 才算 OK。
                    sub_goods = [bool(s.get("is_good")) for s in existing_subs
                                 if s.get("is_good") is not None]
                    merged_is_good = all(sub_goods) if sub_goods else bool(is_good)
                    ng_events = [s.get("event_name") for s in existing_subs
                                 if not s.get("is_good") and s.get("event_name")]
                    ok_events = [s.get("event_name") for s in existing_subs
                                 if s.get("is_good") and s.get("event_name")]
                    if ng_events:
                        merged_event = ng_events[-1]
                    elif ok_events:
                        merged_event = ok_events[-1]
                    else:
                        merged_event = event_name or existing.event_name

                    # 同步更新 cycle.is_good / cycle.result 给前端
                    if isinstance(merged_context.get("cycle"), dict):
                        merged_context["cycle"]["is_good"] = merged_is_good
                        merged_context["cycle"]["result"] = "OK" if merged_is_good else "NG"

                    existing.cycle_context = merged_context
                    existing.is_good = merged_is_good
                    existing.event_name = merged_event
                    existing.source_address = source_address
                    existing.channel_id = channel_id
                    existing.received_at = datetime.utcnow()
                    existing.status = "received"
                else:
                    db.add(BoxAggregation(
                        box_serial=box_serial,
                        station_id=station_id,
                        source_address=source_address,
                        channel_id=channel_id,
                        cycle_context=cycle_context,
                        is_good=is_good,
                        event_name=event_name,
                        status="received",
                    ))

            # 第一轮：正常 upsert（含 locked 退避重试；重试每轮重建 session 状态）
            # 若仍遭遇 IntegrityError（跨进程并发下可能出现 UNIQUE(box_serial,
            # station_id) 冲突），捕获后再跑一轮 → 此时 existing 已可查到 → 走 UPDATE。
            for uniq_attempt in range(2):
                try:
                    if not _run_with_retry(
                            db, build_upsert,
                            f"receive_station_report({station_id})"):
                        return {"success": False,
                                "error": "database locked after retries"}
                    break
                except IntegrityError as ie:
                    db.rollback()
                    if uniq_attempt == 0:
                        logger.warning(
                            "[Cluster] receive_station_report UNIQUE 竞态，"
                            "回滚后改走 UPDATE: box=%s station=%s err=%s",
                            box_serial, station_id, ie,
                        )
                        continue
                    raise

            result = self._check_and_dispatch(db, box_serial)
            return {"success": True, "box_serial": box_serial,
                    "station_id": station_id, **result}
        except Exception as e:
            db.rollback()
            logger.error("[Cluster] receive_station_report 失败: %s\n%s",
                         e, traceback.format_exc())
            return {"success": False, "error": str(e)}
        finally:
            try:
                db.close()
            except Exception:
                pass

    def report_to_master(self, cycle_context: dict, box_serial: str,
                         station_id: str, master_url: str,
                         is_good: bool = True, event_name: str = None) -> dict:
        """从机向主机 POST 数据"""
        url = master_url.rstrip("/") + "/api/v1/cluster/report"
        payload = {
            "station_id": station_id,
            "box_serial": box_serial,
            "cycle_context": cycle_context,
            "is_good": is_good,
            "event_name": event_name,
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.info("[Cluster] 上报主机成功: %s -> %s", box_serial, master_url)
                return {"success": True, "response": resp.json()}
            else:
                logger.error("[Cluster] 上报主机失败: HTTP %d %s",
                             resp.status_code, resp.text[:200])
                return {"success": False, "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            logger.error("[Cluster] 上报主机异常: %s", e)
            return {"success": False, "error": str(e)}

    @staticmethod
    def _match_records_to_expected(expected, records):
        """把 BoxAggregation 记录按 expected_stations 做前缀匹配分类。

        规则：expected 里的 "B" 视为匹配 received 里 station_id == "B"
        或以 "B-" 开头的所有条目（主机多通道场景下 mes_hooks 会把
        station_id 变成 "B-0"/"B-1"，这样 expected 写一个裸 B 就能覆盖）。

        返回 (matched_records, matched_expected_set, missing_expected_list)。
        matched_records 按匹配顺序去重；未被任何 expected 匹配的 records 不进入结果。
        """
        matched_records = []
        matched_expected = set()
        seen_ids = set()
        for e in expected or []:
            hits = [r for r in records
                    if r.station_id == e or (r.station_id or "").startswith(f"{e}-")]
            if hits:
                matched_expected.add(e)
                for r in hits:
                    if id(r) not in seen_ids:
                        seen_ids.add(id(r))
                        matched_records.append(r)
        missing_expected = [e for e in (expected or []) if e not in matched_expected]
        return matched_records, matched_expected, missing_expected

    def _check_and_dispatch(self, db, box_serial: str) -> dict:
        """检查该箱子是否所有工位都到齐，到齐则触发汇总推送"""
        config = self.get_config(db)
        expected = config.get("expected_stations", [])
        if not expected:
            return {"dispatched": False, "reason": "no_expected_stations"}

        records = (
            db.query(BoxAggregation)
            .filter(BoxAggregation.box_serial == box_serial,
                    BoxAggregation.status == "received")
            .all()
        )
        received_stations = {r.station_id for r in records}
        matched_records, matched_expected, missing = \
            self._match_records_to_expected(expected, records)

        if missing:
            return {
                "dispatched": False,
                "received": list(received_stations),
                "missing": list(missing),
            }

        overall_good = all(r.is_good for r in matched_records)
        stations_data = []
        for r in matched_records:
            stations_data.append({
                "station_id": r.station_id,
                "source": r.source_address or "unknown",
                "channel_id": r.channel_id,
                "is_good": r.is_good,
                "event_name": r.event_name,
                "received_at": r.received_at.isoformat() if r.received_at else None,
                **(r.cycle_context or {}),
            })

        # 顶层便利字段：从各站点 cycle_context 里提取 order_no / workpiece_id /
        # ng_items, 让客户 MES 的模板可以直接写 {order_no} {workpiece_id}
        # {ng_items}, 不用在模板里写复杂的嵌套取值.
        order_no = ""
        workpiece_id = ""
        ng_items: list = []
        seen_items: set = set()
        for s in stations_data:
            if not order_no:
                order = s.get("order") or {}
                if isinstance(order, dict):
                    order_no = order.get("order_no") or order.get("order_number") or ""
            if not workpiece_id:
                wp = s.get("workpiece") or {}
                if isinstance(wp, dict):
                    workpiece_id = wp.get("serial_no") or wp.get("id") or ""
            if not s.get("is_good"):
                for ns in (s.get("ng_steps") or []):
                    if isinstance(ns, dict):
                        lbl = ns.get("label")
                        if lbl and lbl not in seen_items:
                            seen_items.add(lbl)
                            ng_items.append(lbl)

        aggregated = {
            "box_serial": box_serial,
            "overall_result": "OK" if overall_good else "NG",
            "result": "OK" if overall_good else "NG",
            "total_stations": len(expected),
            "completed_stations": len(matched_expected),
            "stations": stations_data,
            "order_no": order_no,
            "workpiece_id": workpiece_id,
            "ng_items": ng_items,
            "timestamp": datetime.now().isoformat(),
        }

        # 并发场景：两个上报线程都读到全齐状态，都尝试 INSERT summary；
        # UNIQUE(box_serial) 会让第二个触发 IntegrityError。
        # 对应处理：捕获后 rollback，再次查询 summary（此时应已存在）并 UPDATE。
        early_return = {}

        def build_summary(db):
            summary_local = (
                db.query(BoxSummary)
                .filter(BoxSummary.box_serial == box_serial).first()
            )
            if not summary_local:
                db.add(BoxSummary(
                    box_serial=box_serial,
                    total_stations=len(expected),
                    completed_stations=len(matched_expected),
                    overall_result="OK" if overall_good else "NG",
                    aggregated_context=aggregated,
                    status="complete",
                ))
            else:
                if summary_local.status in ("pushed", "pushed_timeout"):
                    early_return["payload"] = {
                        "dispatched": False,
                        "reason": "already_pushed_by_peer",
                        "box_serial": box_serial,
                    }
                    return
                summary_local.total_stations = len(expected)
                summary_local.completed_stations = len(matched_expected)
                summary_local.overall_result = "OK" if overall_good else "NG"
                summary_local.aggregated_context = aggregated
                summary_local.status = "complete"
            for r in matched_records:
                # matched_records 是外层查到的 ORM 实例，重试时可能已 expired；
                # 通过 merge 确保每轮都挂到当前 session 上。
                db.merge(r).status = "dispatched"

        for attempt in range(2):
            try:
                if not _run_with_retry(
                        db, build_summary,
                        f"check_and_dispatch({box_serial})"):
                    return {"dispatched": False, "reason": "commit_locked",
                            "box_serial": box_serial}
                break
            except IntegrityError as ie:
                db.rollback()
                if attempt == 0:
                    logger.warning(
                        "[Cluster] BoxSummary UNIQUE 竞态，回滚重试: %s err=%s",
                        box_serial, ie,
                    )
                    continue
                raise

        if early_return.get("payload") is not None:
            return early_return["payload"]

        # summary 现在一定存在且 status='complete'，重新取一下用于后续 push 字段更新
        summary = (
            db.query(BoxSummary)
            .filter(BoxSummary.box_serial == box_serial).first()
        )

        try:
            from backend.services.mes_gateway import get_mes_gateway
            gw = get_mes_gateway()
            gw.dispatch("box_complete", aggregated, channel_id=None)

            def mark_pushed(db):
                s = (db.query(BoxSummary)
                     .filter(BoxSummary.box_serial == box_serial).first())
                if s:
                    s.pushed_at = datetime.utcnow()
                    s.status = "pushed"

            _run_with_retry(db, mark_pushed,
                            f"check_and_dispatch_pushed({box_serial})")
            logger.info("[Cluster] 箱子 %s 汇总推送完成 (%s)", box_serial,
                        "OK" if overall_good else "NG")
        except Exception as e:
            logger.error("[Cluster] 箱子 %s MES 推送失败: %s\n%s",
                         box_serial, e, traceback.format_exc())

        # 箱子已推送完成，释放进程内 box 锁，避免 dict 长期累积
        self._release_box_lock(box_serial)

        return {
            "dispatched": True,
            "overall_result": "OK" if overall_good else "NG",
            "stations": len(expected),
        }

    def _push_timeout_result(self, db, box_serial: str, records, expected, missing):
        """超时后仍推送已收集到的数据给 MES，标注缺失工位

        expected/missing 可为 set 或 list；按前缀匹配规则统计已到工位。
        """
        expected_list = list(expected)
        matched_records, matched_expected, _ = \
            self._match_records_to_expected(expected_list, records)

        stations_data = []
        for r in matched_records:
            stations_data.append({
                "station_id": r.station_id,
                "source": r.source_address or "unknown",
                "channel_id": r.channel_id,
                "is_good": r.is_good,
                "event_name": r.event_name,
                "received_at": r.received_at.isoformat() if r.received_at else None,
                **(r.cycle_context or {}),
            })
        for ms in missing:
            stations_data.append({
                "station_id": ms,
                "is_good": False,
                "event_name": "timeout_missing",
                "status": "missing",
            })

        # 顶层便利字段 (同非超时分支)
        order_no = ""
        workpiece_id = ""
        ng_items: list = []
        seen_items: set = set()
        for s in stations_data:
            if not order_no:
                order = s.get("order") or {}
                if isinstance(order, dict):
                    order_no = order.get("order_no") or order.get("order_number") or ""
            if not workpiece_id:
                wp = s.get("workpiece") or {}
                if isinstance(wp, dict):
                    workpiece_id = wp.get("serial_no") or wp.get("id") or ""
            if not s.get("is_good"):
                for ns in (s.get("ng_steps") or []):
                    if isinstance(ns, dict):
                        lbl = ns.get("label")
                        if lbl and lbl not in seen_items:
                            seen_items.add(lbl)
                            ng_items.append(lbl)
        for ms in missing:
            tag = f"MISSING-{ms}"
            if tag not in seen_items:
                seen_items.add(tag)
                ng_items.append(tag)

        aggregated = {
            "box_serial": box_serial,
            "overall_result": "TIMEOUT",
            "result": "NG",
            "total_stations": len(expected_list),
            "completed_stations": len(matched_expected),
            "missing_stations": list(missing),
            "stations": stations_data,
            "order_no": order_no,
            "workpiece_id": workpiece_id,
            "ng_items": ng_items,
            "timestamp": datetime.now().isoformat(),
        }

        def build_timeout(db):
            summary_local = (
                db.query(BoxSummary)
                .filter(BoxSummary.box_serial == box_serial).first()
            )
            if not summary_local:
                db.add(BoxSummary(
                    box_serial=box_serial,
                    total_stations=len(expected_list),
                    completed_stations=len(matched_expected),
                    overall_result="TIMEOUT",
                    aggregated_context=aggregated,
                    status="timeout",
                ))
            else:
                summary_local.total_stations = len(expected_list)
                summary_local.completed_stations = len(matched_expected)
                summary_local.overall_result = "TIMEOUT"
                summary_local.aggregated_context = aggregated
                summary_local.status = "timeout"
            for r in records:
                db.merge(r).status = "timeout"

        if not _run_with_retry(db, build_timeout,
                               f"push_timeout_result({box_serial})"):
            return

        try:
            from backend.services.mes_gateway import get_mes_gateway
            gw = get_mes_gateway()
            gw.dispatch("box_timeout", aggregated, channel_id=None)

            def mark_pushed_timeout(db):
                s = (db.query(BoxSummary)
                     .filter(BoxSummary.box_serial == box_serial).first())
                if s:
                    s.pushed_at = datetime.utcnow()
                    s.status = "pushed_timeout"

            _run_with_retry(db, mark_pushed_timeout,
                            f"push_timeout_pushed({box_serial})")
            logger.info("[Cluster] 目标 %s 超时推送完成 (缺 %s)", box_serial, list(missing))
            self._release_box_lock(box_serial)
        except Exception as e:
            logger.error("[Cluster] 目标 %s 超时推送失败: %s\n%s",
                         box_serial, e, traceback.format_exc())

    def get_pending_boxes(self) -> list:
        """获取当前待汇总的箱子状态"""
        db = SessionLocal()
        try:
            config = self.get_config(db)
            expected = set(config.get("expected_stations", []))

            box_serials = (
                db.query(BoxAggregation.box_serial)
                .filter(BoxAggregation.status == "received")
                .group_by(BoxAggregation.box_serial)
                .all()
            )

            result = []
            for (box_serial,) in box_serials:
                records = (
                    db.query(BoxAggregation)
                    .filter(BoxAggregation.box_serial == box_serial,
                            BoxAggregation.status == "received")
                    .all()
                )
                received = {r.station_id for r in records}
                expected_list = list(expected)
                if expected_list:
                    _, _, missing_list = self._match_records_to_expected(
                        expected_list, records)
                    missing = set(missing_list)
                    is_complete = len(missing) == 0
                else:
                    missing = set()
                    is_complete = False
                oldest = min((r.received_at for r in records if r.received_at),
                             default=None)
                result.append({
                    "box_serial": box_serial,
                    "received_stations": list(received),
                    "missing_stations": list(missing),
                    "first_received_at": oldest.isoformat() if oldest else None,
                    "is_complete": is_complete,
                })
            return result
        finally:
            db.close()

    def get_recent_summaries(self, limit: int = 20, skip: int = 0) -> dict:
        """获取最近已完成的箱子汇总（支持分页）"""
        db = SessionLocal()
        try:
            q = db.query(BoxSummary).order_by(BoxSummary.id.desc())
            total = q.count()
            summaries = q.offset(skip).limit(limit).all()
            items = [{
                "id": s.id,
                "box_serial": s.box_serial,
                "total_stations": s.total_stations,
                "completed_stations": s.completed_stations,
                "overall_result": s.overall_result,
                "status": s.status,
                "pushed_at": s.pushed_at.isoformat() if s.pushed_at else None,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            } for s in summaries]
            return {"items": items, "total": total}
        finally:
            db.close()

    def _timeout_checker(self):
        """后台线程：检查超时未齐的箱子"""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=30)
            if self._stop_event.is_set():
                break
            try:
                config = self.get_config()
                if config["role"] != "master" or not config["enabled"]:
                    continue
                timeout_sec = config["timeout_sec"]
                if timeout_sec <= 0:
                    continue

                expected = set(config.get("expected_stations", []))
                if not expected:
                    continue

                # 先用一个只读短 session 列箱子，立刻关，避免长事务和上报路径争锁
                probe = SessionLocal()
                try:
                    box_serials = [
                        row[0] for row in
                        probe.query(BoxAggregation.box_serial)
                        .filter(BoxAggregation.status == "received")
                        .group_by(BoxAggregation.box_serial)
                        .all()
                    ]
                finally:
                    probe.close()

                now = datetime.utcnow()  # 与 received_at(server_default func.now()) 的 UTC 对齐
                expected_list = list(expected)
                timeout_push = config.get("timeout_push", False)

                for box_serial in box_serials:
                    # 每个箱子独立 session，缩短单次持锁时间，避免
                    # 一次 checker 循环把 receive_station_report 连续顶掉
                    db = SessionLocal()
                    try:
                        records = (
                            db.query(BoxAggregation)
                            .filter(BoxAggregation.box_serial == box_serial,
                                    BoxAggregation.status == "received")
                            .all()
                        )
                        if not records:
                            continue
                        oldest = min((r.received_at for r in records if r.received_at),
                                     default=now)
                        elapsed = (now - oldest).total_seconds()
                        if elapsed < timeout_sec:
                            continue

                        received = {r.station_id for r in records}
                        _, _, missing_list = self._match_records_to_expected(
                            expected_list, records)
                        missing = set(missing_list)
                        logger.warning(
                            "[Cluster] 箱子 %s 超时 (%.0fs > %ds), 已收 %s, 缺 %s",
                            box_serial, elapsed, timeout_sec,
                            list(received), list(missing)
                        )

                        if timeout_push:
                            self._push_timeout_result(db, box_serial, records,
                                                     expected_list, missing)
                        else:
                            _records = records

                            def build_timeout_mark(db):
                                for r in _records:
                                    db.merge(r).status = "timeout"

                            _run_with_retry(
                                db, build_timeout_mark,
                                f"timeout_mark({box_serial})"
                            )
                    finally:
                        try:
                            db.close()
                        except Exception:
                            pass
            except Exception as e:
                logger.error("[Cluster] 超时检查异常: %s\n%s",
                             e, traceback.format_exc())

    def _heartbeat_sender_loop(self):
        """后台线程：副机定时向主机 POST /cluster/heartbeat。

        - 只在 role=='slave' 且 enabled 且 master_url 非空时发送
        - 失败不抛，仅记日志（避免前端感知后端心跳失败）
        - 每轮读一次 config，支持运行时切换角色/主机地址
        - 心跳间隔 = _heartbeat_interval (5s)，主机超时 _slave_timeout (20s) 给足裕量
        """
        import socket
        hostname = ""
        try:
            hostname = socket.gethostname()
        except Exception:
            pass

        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._heartbeat_interval)
            if self._stop_event.is_set():
                break
            try:
                config = self.get_config()
                if config.get("role") != "slave":
                    continue
                if not config.get("enabled"):
                    continue
                master_url = config.get("master_url")
                if not master_url:
                    continue

                station_id = config.get("station_id") or "unknown"
                channel_count = 1
                detecting = False
                project_name = ""
                try:
                    from backend.api.channel_manager import channel_manager
                    channel_count = max(1, int(channel_manager.channel_count or 1))
                    any_detecting = False
                    for ch in channel_manager.channels.values():
                        if getattr(ch, "is_running", False) or getattr(ch, "is_detecting", False):
                            any_detecting = True
                            if getattr(ch, "project_config", None):
                                project_name = ch.project_config.get("name") or project_name
                    detecting = any_detecting
                except Exception:
                    pass

                url = master_url.rstrip("/") + "/api/v1/cluster/heartbeat"
                payload = {
                    "station_id": station_id,
                    "port": 8001,
                    "hostname": hostname,
                    "project": project_name,
                    "channel_count": channel_count,
                    "detecting": detecting,
                }
                try:
                    resp = requests.post(url, json=payload, timeout=5)
                    if resp.status_code != 200:
                        logger.warning(
                            "[Cluster] 副机心跳 HTTP %d: %s",
                            resp.status_code, resp.text[:120]
                        )
                except requests.exceptions.RequestException as e:
                    logger.warning("[Cluster] 副机心跳失败: %s", e)
            except Exception as e:
                logger.error("[Cluster] 心跳线程异常: %s", e)


_collector_instance: Optional[ClusterCollector] = None


def get_cluster_collector() -> ClusterCollector:
    global _collector_instance
    if _collector_instance is None:
        _collector_instance = ClusterCollector()
    return _collector_instance
