"""
RFC12 展会: 监控页高科技面板的真实数据接口 (只读, 从 detection_cycles 计算).

不侵入 150ms 检测热路径, 不动结算 mixin —— 全部从已落库的周期记录派生:
  - GET /data/stats/behavior-score  行为分析综合评分 + 子项 + 最近周期耗时 (节拍曲线)
  - GET /data/stats/yield-trend     合格率随时间趋势 (分桶)

数据口径说明 (展会语境):
  评分是"真实周期数据派生的综合指标"(良率 / 节拍稳定度 / 连续无 NG), 非物理传感。
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.models.models import DetectionCycle, DetectionSession

router = APIRouter()


def _recent_cycles(db: Session, project_id: Optional[int], limit: int) -> List[DetectionCycle]:
    """取最近已结束的周期 (按结束时间倒序), 可按项目过滤. 返回时翻正为时间正序."""
    q = db.query(DetectionCycle).join(
        DetectionSession, DetectionCycle.session_id == DetectionSession.id
    )
    if project_id is not None:
        q = q.filter(DetectionSession.project_id == project_id)
    q = q.filter(DetectionCycle.end_time.isnot(None))
    rows = q.order_by(desc(DetectionCycle.end_time)).limit(limit).all()
    rows.reverse()  # 时间正序, 方便画曲线
    return rows


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / len(xs)
    return var ** 0.5


@router.get("/stats/behavior-score")
def get_behavior_score(
    project_id: Optional[int] = Query(None),
    window: int = Query(30, ge=5, le=200),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """行为分析综合评分 (0-100) + 子项 + 最近周期耗时序列.

    子项:
      yield     良率得分           = 良率 * 100
      stability 节拍稳定度          = 100 - 变异系数*100 (周期耗时越稳越高)
      streak    连续无 NG 得分      = 最近无 NG 连续比例 * 100
    overall = 0.5*yield + 0.3*stability + 0.2*streak
    """
    cycles = _recent_cycles(db, project_id, window)
    durations = [c.duration for c in cycles if c.duration and c.duration > 0]
    goods = [1 if c.is_good else 0 for c in cycles]

    total = len(cycles)
    ok = sum(goods)
    yield_rate = (ok / total) if total else 0.0

    # 稳定度: 变异系数 cv = std/mean, 越小越稳
    mean_d = _mean(durations)
    cv = (_std(durations) / mean_d) if mean_d > 0 else 0.0
    stability = max(0.0, min(100.0, 100.0 - cv * 100.0))

    # 连续无 NG 比例 (从最近往前数连续 good 的占比)
    streak = 0
    for g in reversed(goods):
        if g == 1:
            streak += 1
        else:
            break
    streak_score = (streak / total * 100.0) if total else 0.0

    yield_score = yield_rate * 100.0
    overall = 0.5 * yield_score + 0.3 * stability + 0.2 * streak_score

    return {
        "ok": True,
        "project_id": project_id,
        "sample_count": total,
        "overall": round(overall, 1),
        "sub_scores": {
            "yield": round(yield_score, 1),
            "stability": round(stability, 1),
            "streak": round(streak_score, 1),
        },
        "yield_rate": round(yield_rate, 4),
        "avg_cycle_time": round(mean_d, 2),
        "recent_cycle_times": [round(d, 2) for d in durations],
    }


@router.get("/stats/yield-trend")
def get_yield_trend(
    project_id: Optional[int] = Query(None),
    points: int = Query(12, ge=3, le=48),
    window: int = Query(120, ge=10, le=2000),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """合格率趋势: 取最近 window 个周期, 等分成 points 桶, 各桶算良率."""
    cycles = _recent_cycles(db, project_id, window)
    total = len(cycles)
    buckets: List[Dict[str, Any]] = []
    if total == 0:
        return {"ok": True, "project_id": project_id, "points": []}

    bucket_size = max(1, total // points)
    idx = 0
    bnum = 1
    while idx < total:
        chunk = cycles[idx: idx + bucket_size]
        c_total = len(chunk)
        c_ok = sum(1 for c in chunk if c.is_good)
        c_yield = (c_ok / c_total) if c_total else 0.0
        buckets.append({
            "label": f"#{bnum}",
            "total": c_total,
            "ok": c_ok,
            "ng": c_total - c_ok,
            "yield": round(c_yield * 100.0, 1),
        })
        idx += bucket_size
        bnum += 1

    return {"ok": True, "project_id": project_id, "points": buckets}
