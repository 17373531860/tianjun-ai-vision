"""数据中心查询面 (M5) — 全部只读, 数据源为小时桶聚合 + 周期明细。

口径铁律 (调研裁决, 违反会出假报表):
  - 合格率 = sum(ok)/sum(total) 件数加权, 永远在这里算, 禁止平均工位比率。
  - 分母为 0 → yield_rate 回 None (前端画 "—"), 不回 0。
  - 时序缺桶 → 服务端填桶: total=0 (真实零产画零柱), yield_rate=None (断线)。
  - 天粒度按枢纽本地时区切日 (车间墙钟), 不按 UTC。
"""
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from hub.backend.auth import require_perm
from hub.backend.db import get_db
from hub.backend.models import (HubCycle, HubCycleHourly, HubNgHourly,
                                HubNode, HubRollupDirty, HubStation)
from hub.backend.rollup import UNKNOWN_REASON

router = APIRouter()

HOUR = 3600


# ============================================================
# 共用: 筛选 + 聚合底座
# ============================================================

def _clamp_window(start: int, end: int) -> tuple:
    """窗口守门: end>start, 跨度 ≤ 366 天 (超了按 end 回推截断)。"""
    start, end = int(start), int(end)
    if end <= start:
        end = start + HOUR
    if end - start > 366 * 86400:
        start = end - 366 * 86400
    return start, end


def _hourly_rows(db: Session, start: int, end: int,
                 node_id: Optional[int], channel_id: Optional[int],
                 project_id: Optional[int]):
    q = db.query(HubCycleHourly).filter(
        HubCycleHourly.bucket_epoch >= start - start % HOUR,
        HubCycleHourly.bucket_epoch < end)
    if node_id is not None:
        q = q.filter(HubCycleHourly.node_id == node_id)
    if channel_id is not None:
        q = q.filter(HubCycleHourly.channel_id == channel_id)
    if project_id is not None:
        q = q.filter(HubCycleHourly.project_id == project_id)
    return q.all()


def _fold(rows) -> dict:
    """行折叠成一份 KPI (分子分母 + 派生比率)。"""
    total = sum(r.count_total for r in rows)
    ok = sum(r.count_ok for r in rows)
    ng = sum(r.count_ng for r in rows)
    sum_dur = sum(r.sum_duration_ms for r in rows)
    cnt_dur = sum(r.cnt_duration for r in rows)
    return {
        "total": total, "ok": ok, "ng": ng,
        "yield_rate": round(ok / total, 4) if total else None,
        "avg_duration_ms": int(sum_dur / cnt_dur) if cnt_dur else None,
    }


def _day_floor(epoch: int) -> int:
    """本地时区的当日 00:00 (车间墙钟口径)。"""
    d = datetime.fromtimestamp(epoch)
    return int(datetime(d.year, d.month, d.day).timestamp())


def _bucket_seq(start: int, end: int, interval: str) -> list:
    """窗口内连续桶起点序列 (填桶用)。"""
    out = []
    if interval == "day":
        cur = _day_floor(start)
        while cur < end:
            out.append(cur)
            nxt = datetime.fromtimestamp(cur) + timedelta(days=1)
            cur = int(nxt.timestamp())
    else:
        cur = start - start % HOUR
        while cur < end:
            out.append(cur)
            cur += HOUR
    return out


def _bucket_of(epoch: int, interval: str) -> int:
    return _day_floor(epoch) if interval == "day" else epoch


def _auto_interval(start: int, end: int) -> str:
    return "hour" if end - start <= 48 * 3600 else "day"


# ============================================================
# 端点
# ============================================================

class KpiBlock(BaseModel):
    total: int
    ok: int
    ng: int
    yield_rate: Optional[float]
    avg_duration_ms: Optional[int]


class SummaryResponse(BaseModel):
    current: KpiBlock
    prev: Optional[KpiBlock] = None        # 环比: 紧邻等长窗口
    stations_active: int                    # 窗口内有产出的工位数
    stations_total: int                     # 纳管工位总数 (筛选范围内)
    catching_up: bool                       # 有脏桶未消化 (数据在追赶)


@router.get("/stats/summary", response_model=SummaryResponse,
            summary="数据中心 KPI 汇总 (含环比等长窗口)",
            dependencies=[Depends(require_perm("wall.view"))])
def stats_summary(start: int = Query(...), end: int = Query(...),
                  node_id: Optional[int] = None,
                  channel_id: Optional[int] = None,
                  project_id: Optional[int] = None,
                  compare: bool = True,
                  db: Session = Depends(get_db)):
    start, end = _clamp_window(start, end)
    rows = _hourly_rows(db, start, end, node_id, channel_id, project_id)
    current = _fold(rows)

    prev = None
    if compare:
        span = end - start
        prev_rows = _hourly_rows(db, start - span, start,
                                 node_id, channel_id, project_id)
        prev = _fold(prev_rows)

    active = len({(r.node_id, r.channel_id) for r in rows if r.count_total})
    st_q = db.query(HubStation)
    if node_id is not None:
        st_q = st_q.filter(HubStation.node_id == node_id)
    if channel_id is not None:
        st_q = st_q.filter(HubStation.channel_id == channel_id)
    catching = db.query(HubRollupDirty.id).first() is not None
    return {"current": current, "prev": prev,
            "stations_active": active, "stations_total": st_q.count(),
            "catching_up": catching}


class TimeseriesBucket(BaseModel):
    bucket_epoch: int
    total: int
    ok: int
    ng: int
    yield_rate: Optional[float]


class TimeseriesResponse(BaseModel):
    interval: str
    buckets: list[TimeseriesBucket]


@router.get("/stats/timeseries", response_model=TimeseriesResponse,
            summary="产量/合格率时序 (服务端填桶, 缺桶零柱+断线)",
            dependencies=[Depends(require_perm("wall.view"))])
def stats_timeseries(start: int = Query(...), end: int = Query(...),
                     interval: str = Query("auto"),
                     node_id: Optional[int] = None,
                     channel_id: Optional[int] = None,
                     project_id: Optional[int] = None,
                     db: Session = Depends(get_db)):
    start, end = _clamp_window(start, end)
    if interval not in ("hour", "day"):
        interval = _auto_interval(start, end)
    rows = _hourly_rows(db, start, end, node_id, channel_id, project_id)

    acc: dict = {}
    for r in rows:
        b = _bucket_of(r.bucket_epoch, interval)
        slot = acc.setdefault(b, [0, 0])
        slot[0] += r.count_total
        slot[1] += r.count_ok
    buckets = []
    for b in _bucket_seq(start, end, interval):
        total, ok = acc.get(b, [0, 0])
        buckets.append({
            "bucket_epoch": b, "total": total, "ok": ok, "ng": total - ok,
            "yield_rate": round(ok / total, 4) if total else None,
        })
    return {"interval": interval, "buckets": buckets}


class ParetoItem(BaseModel):
    reason: str
    count: int
    pct: float          # 占全部 NG 比例
    cum_pct: float      # 累计占比 (80% 线画在前端)


class ParetoResponse(BaseModel):
    total_ng: int
    items: list[ParetoItem]       # Top N 降序
    other_count: int              # 尾部合并


@router.get("/stats/pareto", response_model=ParetoResponse,
            summary="NG 原因 Pareto (Top N + Other)",
            dependencies=[Depends(require_perm("wall.view"))])
def stats_pareto(start: int = Query(...), end: int = Query(...),
                 node_id: Optional[int] = None,
                 channel_id: Optional[int] = None,
                 top: int = Query(8, ge=1, le=50),
                 db: Session = Depends(get_db)):
    start, end = _clamp_window(start, end)
    q = db.query(HubNgHourly).filter(
        HubNgHourly.bucket_epoch >= start - start % HOUR,
        HubNgHourly.bucket_epoch < end)
    if node_id is not None:
        q = q.filter(HubNgHourly.node_id == node_id)
    if channel_id is not None:
        q = q.filter(HubNgHourly.channel_id == channel_id)

    counts: dict = {}
    for r in q.all():
        counts[r.reason] = counts.get(r.reason, 0) + r.count_ng
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    total_ng = sum(counts.values())
    items, cum = [], 0
    for reason, cnt in ranked[:top]:
        cum += cnt
        items.append({"reason": reason, "count": cnt,
                      "pct": round(cnt / total_ng, 4) if total_ng else 0,
                      "cum_pct": round(cum / total_ng, 4) if total_ng else 0})
    other = total_ng - cum
    return {"total_ng": total_ng, "items": items, "other_count": other}


class NgMatrixResponse(BaseModel):
    reasons: list[str]            # 行 (按总数降序)
    stations: list[dict]          # 列: {node_id, channel_id, name}
    cells: list[list[int]]        # cells[i][j] = reasons[i] × stations[j]


@router.get("/stats/ng-matrix", response_model=NgMatrixResponse,
            summary="NG 原因 × 工位交叉表 (工艺定位: 谁的病/全厂的病)",
            dependencies=[Depends(require_perm("wall.view"))])
def stats_ng_matrix(start: int = Query(...), end: int = Query(...),
                    node_id: Optional[int] = None,
                    top: int = Query(10, ge=1, le=50),
                    db: Session = Depends(get_db)):
    start, end = _clamp_window(start, end)
    q = db.query(HubNgHourly).filter(
        HubNgHourly.bucket_epoch >= start - start % HOUR,
        HubNgHourly.bucket_epoch < end)
    if node_id is not None:
        q = q.filter(HubNgHourly.node_id == node_id)
    rows = q.all()

    by_reason: dict = {}
    by_cell: dict = {}
    stations_seen = set()
    for r in rows:
        key = (r.node_id, r.channel_id)
        stations_seen.add(key)
        by_reason[r.reason] = by_reason.get(r.reason, 0) + r.count_ng
        by_cell[(r.reason, key)] = by_cell.get((r.reason, key), 0) + r.count_ng

    reasons = [k for k, _ in
               sorted(by_reason.items(), key=lambda kv: -kv[1])[:top]]
    names = _station_names(db)
    stations = [{"node_id": n, "channel_id": c,
                 "name": names.get((n, c), f"节点{n}-工位{c}")}
                for n, c in sorted(stations_seen)]
    cells = [[by_cell.get((reason, (s["node_id"], s["channel_id"])), 0)
              for s in stations] for reason in reasons]
    return {"reasons": reasons, "stations": stations, "cells": cells}


class StationRow(BaseModel):
    node_id: int
    node_name: str
    channel_id: int
    station_name: str
    project_name: Optional[str]    # 窗口内件数最多的项目
    total: int
    ok: int
    ng: int
    yield_rate: Optional[float]
    avg_duration_ms: Optional[int]
    spark: list[int]               # 窗口内按桶产量序列 (sparkline)


class StationsResponse(BaseModel):
    interval: str
    spark_buckets: list[int]       # spark 各点对应的桶起点
    items: list[StationRow]


@router.get("/stats/stations", response_model=StationsResponse,
            summary="工位统计表 (产量/合格率/均耗时/sparkline)",
            dependencies=[Depends(require_perm("wall.view"))])
def stats_stations(start: int = Query(...), end: int = Query(...),
                   node_id: Optional[int] = None,
                   project_id: Optional[int] = None,
                   db: Session = Depends(get_db)):
    start, end = _clamp_window(start, end)
    interval = _auto_interval(start, end)
    rows = _hourly_rows(db, start, end, node_id, None, project_id)
    seq = _bucket_seq(start, end, interval)
    idx = {b: i for i, b in enumerate(seq)}

    per: dict = {}
    for r in rows:
        st = per.setdefault((r.node_id, r.channel_id), {
            "rows": [], "spark": [0] * len(seq), "proj": {}})
        st["rows"].append(r)
        i = idx.get(_bucket_of(r.bucket_epoch, interval))
        if i is not None:
            st["spark"][i] += r.count_total
        if r.project_name:
            st["proj"][r.project_name] = (
                st["proj"].get(r.project_name, 0) + r.count_total)

    node_names = {n.id: n.name for n in db.query(HubNode).all()}
    st_names = _station_names(db)
    # 全部纳管工位都出行 (窗口内无产出的也要在表里, 产量 0 合格率 "—")
    st_q = db.query(HubStation)
    if node_id is not None:
        st_q = st_q.filter(HubStation.node_id == node_id)
    items = []
    for s in st_q.all():
        key = (s.node_id, s.channel_id)
        st = per.get(key)
        kpi = _fold(st["rows"]) if st else _fold([])
        top_proj = (max(st["proj"].items(), key=lambda kv: kv[1])[0]
                    if st and st["proj"] else None)
        items.append({
            "node_id": s.node_id,
            "node_name": node_names.get(s.node_id, f"节点{s.node_id}"),
            "channel_id": s.channel_id,
            "station_name": st_names.get(key, f"工位{s.channel_id}"),
            "project_name": top_proj,
            **kpi,
            "spark": st["spark"] if st else [0] * len(seq),
        })
    # 默认排序: 合格率升序 (最差在上, 主任扫一眼知道看谁), 无数据行沉底
    items.sort(key=lambda r: (r["yield_rate"] is None,
                              r["yield_rate"] if r["yield_rate"] is not None
                              else 2.0))
    return {"interval": interval, "spark_buckets": seq, "items": items}


class CycleRow(BaseModel):
    id: int
    node_id: int
    node_name: str
    channel_id: int
    station_name: str
    project_name: Optional[str]
    result: Optional[str]
    event_name: Optional[str]
    reason: Optional[str]
    duration_ms: Optional[int]
    ts_epoch: int


class CyclesResponse(BaseModel):
    total: int
    items: list[CycleRow]


@router.get("/stats/cycles", response_model=CyclesResponse,
            summary="周期明细分页 (证据层, NG 分析样本表/导出原始行)",
            dependencies=[Depends(require_perm("wall.view"))])
def stats_cycles(start: int = Query(...), end: int = Query(...),
                 node_id: Optional[int] = None,
                 channel_id: Optional[int] = None,
                 project_id: Optional[int] = None,
                 result: Optional[str] = None,
                 reason: Optional[str] = None,
                 limit: int = Query(50, ge=1, le=500),
                 offset: int = Query(0, ge=0),
                 db: Session = Depends(get_db)):
    start, end = _clamp_window(start, end)
    q = db.query(HubCycle).filter(HubCycle.ts_epoch >= start,
                                  HubCycle.ts_epoch < end)
    if node_id is not None:
        q = q.filter(HubCycle.node_id == node_id)
    if channel_id is not None:
        q = q.filter(HubCycle.channel_id == channel_id)
    if project_id is not None:
        q = q.filter(HubCycle.project_id == project_id)
    if result in ("OK", "NG"):
        q = q.filter(HubCycle.result == result)
    if reason:
        # 规整原因是原串截断 120 字符 → 前缀匹配即命中原行;
        # "未知原因" 特判为 reason/event_name 双空
        if reason == UNKNOWN_REASON:
            q = q.filter(
                (HubCycle.reason.is_(None) | (HubCycle.reason == "")) &
                (HubCycle.event_name.is_(None) | (HubCycle.event_name == "")))
        else:
            q = q.filter(HubCycle.reason.like(f"{reason}%") |
                         ((HubCycle.reason.is_(None) |
                           (HubCycle.reason == "")) &
                          HubCycle.event_name.like(f"{reason}%")))

    total = q.count()
    rows = (q.order_by(HubCycle.ts_epoch.desc(), HubCycle.id.desc())
            .offset(offset).limit(limit).all())
    node_names = {n.id: n.name for n in db.query(HubNode).all()}
    st_names = _station_names(db)
    items = [{
        "id": r.id, "node_id": r.node_id,
        "node_name": node_names.get(r.node_id, f"节点{r.node_id}"),
        "channel_id": r.channel_id,
        "station_name": st_names.get((r.node_id, r.channel_id),
                                     f"工位{r.channel_id}"),
        "project_name": r.project_name, "result": r.result,
        "event_name": r.event_name, "reason": r.reason,
        "duration_ms": r.duration_ms, "ts_epoch": r.ts_epoch,
    } for r in rows]
    return {"total": total, "items": items}


def _station_names(db: Session) -> dict:
    return {(s.node_id, s.channel_id): (s.display_name or f"工位{s.channel_id}")
            for s in db.query(HubStation).all()}
