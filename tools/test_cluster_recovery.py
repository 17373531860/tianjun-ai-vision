"""
仿真验证方案 B: BoxAggregation/BoxSummary 二次齐发一致性

场景:
1. 4 工位 A/B/C/D 首次齐发, 其中 B 为 NG => summary 应为 NG 并 push
2. 校正 B 为 OK, 重扫 B => summary 应更新为 OK 并"重推" MES
3. 再次原样重报 B (OK, 无变化) => summary 字段刷新但**不**再重推 MES
4. 工位 A 误报 NG => summary 应更新为 NG 并再次重推 MES

使用独立 SQLite 文件, 不污染主库.
"""
import os
import sys
import shutil
import tempfile

# 切换到独立 DATA_DIR (必须在 import backend.* 之前, 否则会连到主库!)
_TMPDIR = tempfile.mkdtemp(prefix="cluster_recovery_sim_")
os.environ["TIANJUN_DATA_DIR"] = _TMPDIR
# 顺手建必要的子目录, 避免其它模块启动时报错
for sub in ("uploads", "uploads/models", "uploads/images", "uploads/videos",
            "counters", "recordings"):
    os.makedirs(os.path.join(_TMPDIR, sub), exist_ok=True)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.db.database import Base, engine, SessionLocal  # noqa: E402
# 需要先 import models.py 让 Project/WorkOrder 等进 registry, 否则
# mes_models 里的 relationship("Project") 找不到
from backend.models import models as _models  # noqa: E402,F401
from backend.models.mes_models import (  # noqa: E402
    ClusterConfig, BoxAggregation, BoxSummary,
)
from backend.services import cluster_collector as cc_mod  # noqa: E402

# _migrate_old_data 会把主库拷贝过来, 有脏数据, 先整张库清空
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

# Mock MES gateway: 记录所有被推送的 box_complete, 不真的发 HTTP
_push_log = []


class _MockGW:
    def dispatch(self, event_name, payload, channel_id=None):
        _push_log.append({
            "event": event_name,
            "overall_result": payload.get("overall_result"),
            "ng_items": list(payload.get("ng_items") or []),
            "box_serial": payload.get("box_serial"),
        })
        return {"dispatched": True}


def _get_mock_gw():
    return _MockGW()


# Monkey patch: 让 _check_and_dispatch 用 mock gateway
import backend.services.mes_gateway as mes_gw_mod  # noqa: E402
mes_gw_mod.get_mes_gateway = _get_mock_gw


def _seed_config():
    db = SessionLocal()
    try:
        db.query(ClusterConfig).delete()
        db.add(ClusterConfig(
            id=1,
            role="master",
            expected_stations=["A", "B", "C", "D"],
            timeout_sec=300,
            timeout_push=False,
            enabled=True,
        ))
        db.commit()
    finally:
        db.close()


def _get_summary(box_serial):
    db = SessionLocal()
    try:
        s = db.query(BoxSummary).filter_by(box_serial=box_serial).first()
        if not s:
            return None
        return {
            "overall_result": s.overall_result,
            "status": s.status,
            "ng_items": list((s.aggregated_context or {}).get("ng_items") or []),
        }
    finally:
        db.close()


def _mk_ctx(workpiece_id, is_good, missing=None):
    ctx = {
        "workpiece": {"serial_no": workpiece_id},
        "order": {"order_no": "ORD001"},
        "cycle": {"is_good": is_good, "result": "OK" if is_good else "NG"},
    }
    if not is_good and missing:
        # cluster_collector 是从 stations_data[i].ng_steps[].label 里提 ng_items
        ctx["ng_steps"] = [{"label": m} for m in missing]
    return ctx


def _report(collector, station, box, is_good, missing=None, event=None):
    ctx = _mk_ctx(box, is_good, missing)
    return collector.receive_station_report(
        station_id=station,
        box_serial=box,
        cycle_context=ctx,
        source_address=f"{station}-local",
        channel_id=0,
        is_good=is_good,
        event_name=event or ("ok" if is_good else "ng"),
    )


def _assert(cond, msg):
    prefix = "  [PASS]" if cond else "  [FAIL]"
    print(f"{prefix} {msg}")
    if not cond:
        raise AssertionError(msg)


def main():
    _seed_config()
    # 实例化 collector(不启动线程, 只用它的业务方法)
    collector = cc_mod.ClusterCollector()

    box = "BOX-RECOV-001"

    print("\n=== Step 1: 首次齐发 (B=NG, 缺件=[螺丝X]) ===")
    r0 = _report(collector, "A", box, True)
    print(f"  上报 A 返回: {r0}")
    pending = collector.get_pending_boxes()
    print(f"  扫完 A 后 pending: {pending}")
    _assert(any(p["box_serial"] == box for p in pending),
            "仅扫 A 时待汇总应包含本箱")
    _report(collector, "B", box, False, missing=["螺丝X"])
    _report(collector, "C", box, True)
    r = _report(collector, "D", box, True)
    print(f"  最后一次上报返回: dispatched={r.get('dispatched')}, "
          f"overall={r.get('overall_result')}, is_recovery={r.get('is_recovery', False)}")
    s = _get_summary(box)
    print(f"  Summary: {s}")
    _assert(len(_push_log) == 1, "首次齐发应推一次 MES")
    _assert(_push_log[-1]["overall_result"] == "NG", "首推应为 NG")
    _assert("螺丝X" in _push_log[-1]["ng_items"], "首推 ng_items 应含螺丝X")
    _assert(s["status"] == "pushed", "summary.status 应为 pushed")
    _assert(s["overall_result"] == "NG", "summary.overall_result 应为 NG")
    pending = collector.get_pending_boxes()
    _assert(not any(p["box_serial"] == box for p in pending),
            "首发齐后待汇总不应再包含本箱")

    print("\n=== Step 2: 校正 B=OK, 重扫 B ===")
    r = _report(collector, "B", box, True)
    print(f"  上报返回: dispatched={r.get('dispatched')}, "
          f"overall={r.get('overall_result')}, is_recovery={r.get('is_recovery', False)}, "
          f"reason={r.get('reason')}")
    s = _get_summary(box)
    print(f"  Summary: {s}")
    pending = collector.get_pending_boxes()
    _assert(not any(p["box_serial"] == box for p in pending),
            "同码重扫齐发后待汇总不应显示 (否则会和右下角并存)")
    _assert(len(_push_log) == 2, "OK 校正后应再推一次 MES (方案 B)")
    _assert(_push_log[-1]["overall_result"] == "OK", "重推应为 OK")
    _assert(s["overall_result"] == "OK", "summary.overall_result 应更新为 OK")
    _assert(s["status"] == "pushed", "重推成功后 status 应为 pushed")
    _assert(r.get("is_recovery") is True, "返回值应标记 is_recovery=True")

    print("\n=== Step 3: 原样再报 B=OK (无变化) ===")
    r = _report(collector, "B", box, True)
    print(f"  上报返回: dispatched={r.get('dispatched')}, reason={r.get('reason')}")
    s = _get_summary(box)
    print(f"  Summary: {s}")
    _assert(len(_push_log) == 2, "无变化时不应重复推 MES (还是 2 次)")
    _assert(r.get("reason") == "no_change_after_recovery",
            "无变化分支应返回 no_change_after_recovery")
    _assert(s["overall_result"] == "OK", "summary 仍为 OK")
    _assert(s["status"] == "pushed", "status 应保持 pushed")

    print("\n=== Step 4: 工位 A 反报 NG (缺件=[其他件Y]) ===")
    r = _report(collector, "A", box, False, missing=["其他件Y"])
    print(f"  上报返回: dispatched={r.get('dispatched')}, "
          f"overall={r.get('overall_result')}, is_recovery={r.get('is_recovery', False)}")
    s = _get_summary(box)
    print(f"  Summary: {s}")
    _assert(len(_push_log) == 3, "结果翻转应重推 MES (共 3 次)")
    _assert(_push_log[-1]["overall_result"] == "NG", "重推应为 NG")
    _assert("其他件Y" in _push_log[-1]["ng_items"], "ng_items 应含其他件Y")
    _assert(s["overall_result"] == "NG", "summary 应更新为 NG")

    print("\n=== 所有场景通过 ✓ ===")
    print(f"\n共 {len(_push_log)} 次 MES 推送:")
    for i, p in enumerate(_push_log, 1):
        print(f"  {i}. {p['overall_result']} ng_items={p['ng_items']}")


if __name__ == "__main__":
    try:
        main()
    finally:
        # 清理临时库
        try:
            shutil.rmtree(_TMPDIR, ignore_errors=True)
        except Exception:
            pass
