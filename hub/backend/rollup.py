"""M5 数据中心聚合引擎: 明细入库 → 脏桶登记 → 小时桶重算 → 保留清理。

设计裁决 (三路调研交叉, 见 RFC 15 v7):
  - 全部物化 (含当前小时): 查询只读小时桶, 逻辑单一; 新明细进来就标脏,
    rollup worker 每 ROLLUP_INTERVAL_S 消费 → 数据新鲜度 ≤ 拉取延迟+重算延迟
    (~25s), 数据中心看"这段时间"不看"此刻", 完全够用。
  - 脏桶重算 = 整桶 GROUP BY 覆盖写 (先删后插): 迟到数据/游标重放/枢纽重启
    天然幂等, 不做增量 +1 (增量计数一旦漂移无法自愈)。
  - 合格率永远 sum(ok)/sum(total) 在查询层算, 本模块只存分子分母。
"""
import time
from datetime import datetime
from typing import List, Optional

from sqlalchemy import func

from hub.backend.config import CYCLE_KEEP_DAYS, ROLLUP_KEEP_DAYS
from hub.backend.models import (HubCycle, HubCycleHourly, HubNgHourly,
                                HubRollupDirty)

UNKNOWN_REASON = "未知原因"


def norm_reason(reason: Optional[str], event_name: Optional[str]) -> str:
    """NG 原因规整: 结算原因优先, 回退事件名, 都空归"未知原因"; 截 120 字符。

    调研裁决: 原因码必须稳定才能画 Pareto —— 边缘结算串原样保留 (不做同义
    合并, 那是前端展示层映射表的活), 只做空值收口与长度收口。
    """
    text = (reason or "").strip() or (event_name or "").strip()
    if not text:
        return UNKNOWN_REASON
    return text[:120]


def parse_ts_epoch(ts: Optional[str]) -> int:
    """边缘 ISO 时间串 → epoch 秒。解析失败用枢纽当前时刻兜底 (宁可归错桶
    也不丢件 —— 产量守恒优先于时刻精确)。边缘与枢纽同厂同时区, naive 时间
    按枢纽本地时区解释。"""
    if ts:
        try:
            return int(datetime.fromisoformat(ts).timestamp())
        except (ValueError, OSError):
            pass
    return int(time.time())


def ingest_cycles(db, node_id: int, events: List[dict]) -> int:
    """周期明细入库 + 脏桶登记 (poller._pull_cycles 调用, 调用方 commit)。

    (node_id, edge_cycle_id) 去重在这里查一次 —— 游标重放时批量跳过已有行。
    返回实际新增行数。
    """
    if not events:
        return 0
    ids = [e["id"] for e in events]
    existing = {r[0] for r in db.query(HubCycle.edge_cycle_id).filter(
        HubCycle.node_id == node_id,
        HubCycle.edge_cycle_id.in_(ids)).all()}
    dirty_buckets = set()
    added = 0
    for e in events:
        if e["id"] in existing:
            continue
        ts_epoch = parse_ts_epoch(e.get("ts"))
        bucket = ts_epoch // 3600 * 3600
        db.add(HubCycle(
            node_id=node_id,
            channel_id=e.get("channel_id", 0),
            edge_cycle_id=e["id"],
            result=e.get("result"),
            event_name=e.get("event_name"),
            reason=e.get("reason"),
            project_id=e.get("project_id") or 0,
            project_name=e.get("project_name"),
            duration_ms=e.get("duration_ms"),
            ts_epoch=ts_epoch,
            bucket_epoch=bucket,
        ))
        dirty_buckets.add(bucket)
        added += 1
    for bucket in dirty_buckets:
        mark_dirty(db, node_id, bucket)
    return added


def mark_dirty(db, node_id: int, bucket_epoch: int) -> None:
    """脏桶登记 (幂等)。"""
    exists = db.query(HubRollupDirty.id).filter(
        HubRollupDirty.node_id == node_id,
        HubRollupDirty.bucket_epoch == bucket_epoch).first()
    if not exists:
        db.add(HubRollupDirty(node_id=node_id, bucket_epoch=bucket_epoch,
                              marked_at=datetime.now()))


def recompute_dirty(db, max_buckets: int = 200) -> int:
    """消费脏桶: 每桶从明细整桶 GROUP BY 覆盖写两张小时表。返回处理桶数。

    覆盖写 = 先删 (node, bucket) 全部聚合行再插 —— 明细是唯一真相源,
    重算任意次结果一致。
    """
    dirty = (db.query(HubRollupDirty)
             .order_by(HubRollupDirty.bucket_epoch.asc())
             .limit(max_buckets).all())
    for d in dirty:
        _recompute_bucket(db, d.node_id, d.bucket_epoch)
        db.delete(d)
    if dirty:
        db.commit()
    return len(dirty)


def _recompute_bucket(db, node_id: int, bucket_epoch: int) -> None:
    base = db.query(HubCycle).filter(
        HubCycle.node_id == node_id,
        HubCycle.bucket_epoch == bucket_epoch)

    # ---- hub_cycle_hourly: 工位 × 项目 ----
    db.query(HubCycleHourly).filter(
        HubCycleHourly.node_id == node_id,
        HubCycleHourly.bucket_epoch == bucket_epoch).delete(
        synchronize_session=False)
    rows = (base.with_entities(
        HubCycle.channel_id,
        HubCycle.project_id,
        func.max(HubCycle.project_name),      # 桶内快照, 同 id 不同名取任一
        func.count(HubCycle.id),
        func.sum(_ok_case()),
        func.sum(func.coalesce(HubCycle.duration_ms, 0)),
        func.count(HubCycle.duration_ms),      # count 忽略 NULL → 均值分母
    ).group_by(HubCycle.channel_id, HubCycle.project_id).all())
    now = datetime.now()
    for ch, pid, pname, total, ok, sum_dur, cnt_dur in rows:
        ok = int(ok or 0)
        db.add(HubCycleHourly(
            node_id=node_id, channel_id=ch, project_id=pid or 0,
            project_name=pname, bucket_epoch=bucket_epoch,
            count_total=total, count_ok=ok, count_ng=total - ok,
            sum_duration_ms=int(sum_dur or 0), cnt_duration=int(cnt_dur or 0),
            updated_at=now))

    # ---- hub_ng_hourly: 工位 × 原因 ----
    db.query(HubNgHourly).filter(
        HubNgHourly.node_id == node_id,
        HubNgHourly.bucket_epoch == bucket_epoch).delete(
        synchronize_session=False)
    ng_rows = (base.filter(HubCycle.result == "NG")
               .with_entities(HubCycle.channel_id, HubCycle.reason,
                              HubCycle.event_name, func.count(HubCycle.id))
               .group_by(HubCycle.channel_id, HubCycle.reason,
                         HubCycle.event_name).all())
    # reason 规整后可能合并 (不同 event_name 同空 reason → 同一规整值),
    # 在 Python 层二次聚合再落库, 避免 UNIQUE 冲突
    merged: dict = {}
    for ch, reason, event_name, cnt in ng_rows:
        key = (ch, norm_reason(reason, event_name))
        merged[key] = merged.get(key, 0) + cnt
    for (ch, reason), cnt in merged.items():
        db.add(HubNgHourly(node_id=node_id, channel_id=ch, reason=reason,
                           bucket_epoch=bucket_epoch, count_ng=cnt,
                           updated_at=now))


def _ok_case():
    from sqlalchemy import case
    return case((HubCycle.result == "OK", 1), else_=0)


def run_retention(db) -> dict:
    """过期清理: 明细 CYCLE_KEEP_DAYS 天 / 小时桶 ROLLUP_KEEP_DAYS 天。

    删明细不标脏 —— 对应小时桶早已闭合且聚合行保留, 删明细只收证据层。
    """
    now = int(time.time())
    cycle_cut = now - CYCLE_KEEP_DAYS * 86400
    rollup_cut = now - ROLLUP_KEEP_DAYS * 86400
    n_cycles = db.query(HubCycle).filter(
        HubCycle.bucket_epoch < cycle_cut).delete(synchronize_session=False)
    n_hourly = db.query(HubCycleHourly).filter(
        HubCycleHourly.bucket_epoch < rollup_cut).delete(
        synchronize_session=False)
    n_ng = db.query(HubNgHourly).filter(
        HubNgHourly.bucket_epoch < rollup_cut).delete(
        synchronize_session=False)
    db.commit()
    return {"cycles": n_cycles, "hourly": n_hourly, "ng_hourly": n_ng}
