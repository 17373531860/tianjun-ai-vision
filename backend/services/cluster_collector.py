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

from backend.db.database import SessionLocal
from backend.models.mes_models import (
    ClusterConfig, BoxAggregation, BoxSummary,
)

import logging
logger = logging.getLogger(__name__)


class ClusterCollector:

    def __init__(self):
        self._lock = threading.Lock()
        self._timeout_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._config_cache: Optional[dict] = None
        self._config_ts: float = 0

    def start(self):
        self._stop_event.clear()
        self._timeout_thread = threading.Thread(
            target=self._timeout_checker, daemon=True,
            name="cluster-timeout-checker"
        )
        self._timeout_thread.start()
        logger.info("[Cluster] 汇总服务已启动")

    def stop(self):
        self._stop_event.set()
        if self._timeout_thread:
            self._timeout_thread.join(timeout=5)

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

    def receive_station_report(self, station_id: str, box_serial: str,
                               cycle_context: dict, source_address: str = "local",
                               channel_id: int = None, is_good: bool = True,
                               event_name: str = None) -> dict:
        """收到一个工位的数据，存入汇总表并检查是否齐"""
        db = SessionLocal()
        try:
            existing = (
                db.query(BoxAggregation)
                .filter(BoxAggregation.box_serial == box_serial,
                        BoxAggregation.station_id == station_id)
                .first()
            )
            if existing:
                existing.cycle_context = cycle_context
                existing.is_good = is_good
                existing.event_name = event_name
                existing.source_address = source_address
                existing.channel_id = channel_id
                existing.received_at = datetime.now()
                existing.status = "received"
            else:
                agg = BoxAggregation(
                    box_serial=box_serial,
                    station_id=station_id,
                    source_address=source_address,
                    channel_id=channel_id,
                    cycle_context=cycle_context,
                    is_good=is_good,
                    event_name=event_name,
                    status="received",
                )
                db.add(agg)
            db.commit()

            result = self._check_and_dispatch(db, box_serial)
            return {"success": True, "box_serial": box_serial,
                    "station_id": station_id, **result}
        except Exception as e:
            db.rollback()
            logger.error("[Cluster] receive_station_report 失败: %s\n%s",
                         e, traceback.format_exc())
            return {"success": False, "error": str(e)}
        finally:
            db.close()

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
        expected_set = set(expected)
        missing = expected_set - received_stations

        if missing:
            return {
                "dispatched": False,
                "received": list(received_stations),
                "missing": list(missing),
            }

        overall_good = all(r.is_good for r in records if r.station_id in expected_set)
        stations_data = []
        for r in records:
            if r.station_id in expected_set:
                stations_data.append({
                    "station_id": r.station_id,
                    "source": r.source_address or "unknown",
                    "channel_id": r.channel_id,
                    "is_good": r.is_good,
                    "event_name": r.event_name,
                    "received_at": r.received_at.isoformat() if r.received_at else None,
                    **(r.cycle_context or {}),
                })

        aggregated = {
            "box_serial": box_serial,
            "overall_result": "OK" if overall_good else "NG",
            "total_stations": len(expected),
            "completed_stations": len(received_stations & expected_set),
            "stations": stations_data,
            "timestamp": datetime.now().isoformat(),
        }

        summary = db.query(BoxSummary).filter(BoxSummary.box_serial == box_serial).first()
        if not summary:
            summary = BoxSummary(
                box_serial=box_serial,
                total_stations=len(expected),
                completed_stations=len(received_stations & expected_set),
                overall_result="OK" if overall_good else "NG",
                aggregated_context=aggregated,
                status="complete",
            )
            db.add(summary)
        else:
            summary.total_stations = len(expected)
            summary.completed_stations = len(received_stations & expected_set)
            summary.overall_result = "OK" if overall_good else "NG"
            summary.aggregated_context = aggregated
            summary.status = "complete"

        for r in records:
            if r.station_id in expected_set:
                r.status = "dispatched"

        db.commit()

        try:
            from backend.services.mes_gateway import get_mes_gateway
            gw = get_mes_gateway()
            gw.dispatch("box_complete", aggregated, channel_id=None)
            summary.pushed_at = datetime.now()
            summary.status = "pushed"
            db.commit()
            logger.info("[Cluster] 箱子 %s 汇总推送完成 (%s)", box_serial,
                        "OK" if overall_good else "NG")
        except Exception as e:
            logger.error("[Cluster] 箱子 %s MES 推送失败: %s", box_serial, e)

        return {
            "dispatched": True,
            "overall_result": "OK" if overall_good else "NG",
            "stations": len(expected),
        }

    def _push_timeout_result(self, db, box_serial: str, records, expected: set, missing: set):
        """超时后仍推送已收集到的数据给 MES，标注缺失工位"""
        received_set = {r.station_id for r in records}
        any_ng = any(not r.is_good for r in records if r.station_id in expected)

        stations_data = []
        for r in records:
            if r.station_id in expected:
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

        aggregated = {
            "box_serial": box_serial,
            "overall_result": "TIMEOUT",
            "total_stations": len(expected),
            "completed_stations": len(received_set & expected),
            "missing_stations": list(missing),
            "stations": stations_data,
            "timestamp": datetime.now().isoformat(),
        }

        summary = db.query(BoxSummary).filter(BoxSummary.box_serial == box_serial).first()
        if not summary:
            summary = BoxSummary(
                box_serial=box_serial,
                total_stations=len(expected),
                completed_stations=len(received_set & expected),
                overall_result="TIMEOUT",
                aggregated_context=aggregated,
                status="timeout",
            )
            db.add(summary)
        else:
            summary.total_stations = len(expected)
            summary.completed_stations = len(received_set & expected)
            summary.overall_result = "TIMEOUT"
            summary.aggregated_context = aggregated
            summary.status = "timeout"

        for r in records:
            r.status = "timeout"

        db.commit()

        try:
            from backend.services.mes_gateway import get_mes_gateway
            gw = get_mes_gateway()
            gw.dispatch("box_timeout", aggregated, channel_id=None)
            summary.pushed_at = datetime.now()
            summary.status = "pushed_timeout"
            db.commit()
            logger.info("[Cluster] 目标 %s 超时推送完成 (缺 %s)", box_serial, list(missing))
        except Exception as e:
            logger.error("[Cluster] 目标 %s 超时推送失败: %s", box_serial, e)

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
                if expected:
                    missing = expected - received
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

                db = SessionLocal()
                try:
                    expected = set(config.get("expected_stations", []))
                    if not expected:
                        continue
                    box_serials = (
                        db.query(BoxAggregation.box_serial)
                        .filter(BoxAggregation.status == "received")
                        .group_by(BoxAggregation.box_serial)
                        .all()
                    )
                    now = datetime.now()
                    for (box_serial,) in box_serials:
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
                        missing = expected - received
                        logger.warning(
                            "[Cluster] 箱子 %s 超时 (%.0fs > %ds), 已收 %s, 缺 %s",
                            box_serial, elapsed, timeout_sec,
                            list(received), list(missing)
                        )

                        timeout_push = config.get("timeout_push", False)
                        if timeout_push:
                            self._push_timeout_result(db, box_serial, records, expected, missing)
                        else:
                            for r in records:
                                r.status = "timeout"
                            db.commit()
                finally:
                    db.close()
            except Exception as e:
                logger.error("[Cluster] 超时检查异常: %s", e)


_collector_instance: Optional[ClusterCollector] = None


def get_cluster_collector() -> ClusterCollector:
    global _collector_instance
    if _collector_instance is None:
        _collector_instance = ClusterCollector()
    return _collector_instance
